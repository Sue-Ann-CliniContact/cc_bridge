"""Lead-sourcing orchestration.

Each `source_from_*` fn takes a Project, pulls candidates from an external
source, filters against OptOut, dedups against the global Lead table, and
persists new Leads. Returns a summary dict the UI can render as a flash
message. No ProjectLead rows are created here — selection-to-project happens
in a separate step after human review.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests
from django.db import transaction

from core.models import Lead, OptOut, PartnerProfile, Project
from integrations import ai_sourcing, apollo, monday_client, npi

log = logging.getLogger(__name__)


# ──────────────────────── URL validation ────────────────────────────────────

def _head_check(url: str, timeout: float = 4.0) -> bool:
    """Return True iff the URL resolves with a 2xx/3xx response."""
    if not url or not url.startswith(('http://', 'https://')):
        return False
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        ),
    }
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        if 200 <= r.status_code < 400:
            return True
        # Some sites reject HEAD but answer GET. Retry with GET, streamed.
        if r.status_code in (400, 403, 405, 501):
            r2 = requests.get(url, timeout=timeout, allow_redirects=True, headers=headers, stream=True)
            r2.close()
            return 200 <= r2.status_code < 400
        return False
    except Exception:  # noqa: BLE001
        return False


def validate_urls(urls: list[str], timeout: float = 4.0, max_workers: int = 10) -> dict[str, bool]:
    """Parallel HEAD-check a list of URLs. Returns {url: is_valid}."""
    unique = list({u for u in urls if u})
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(lambda u: _head_check(u, timeout=timeout), unique))
    return dict(zip(unique, results))


@dataclass
class SourcingResult:
    source: str
    candidates_found: int = 0
    created: list[int] = field(default_factory=list)  # lead IDs
    reused: list[int] = field(default_factory=list)
    conflicts: list[int] = field(default_factory=list)  # lead IDs flagged with pending_conflict
    skipped_opted_out: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self):
        return {
            'source': self.source,
            'candidates_found': self.candidates_found,
            'created_count': len(self.created),
            'reused_count': len(self.reused),
            'conflict_count': len(self.conflicts),
            'skipped_opted_out': self.skipped_opted_out,
            'errors': self.errors,
        }


def _opted_out_emails(emails: list[str]) -> set[str]:
    if not emails:
        return set()
    return set(OptOut.objects.filter(email__in=emails).values_list('email', flat=True))


def _persist_candidate(candidate: dict, default_source: str, default_enrichment: str = Lead.ENRICHMENT_COMPLETE) -> tuple[Lead, bool, bool]:
    """Look up an existing Lead by email or npi; create if absent.
    If the match has materially different name/org, attach pending_conflict for
    human review rather than silently overwriting.

    Returns (lead, created, conflict_flagged).
    """
    email = (candidate.get('email') or '').strip().lower() or None
    npi_val = (candidate.get('npi') or '').strip() or None

    existing = None
    if email:
        existing = Lead.objects.filter(email__iexact=email).first()
    if not existing and npi_val:
        existing = Lead.objects.filter(npi=npi_val).first()

    if existing:
        conflict = _diff_fields(existing, candidate)
        if conflict:
            existing.pending_conflict = {
                'source': default_source,
                'incoming': {
                    'first_name': candidate.get('first_name', ''),
                    'last_name': candidate.get('last_name', ''),
                    'email': email or '',
                    'phone': candidate.get('phone', ''),
                    'npi': npi_val or '',
                    'organization': candidate.get('organization', ''),
                    'role': candidate.get('role', ''),
                    'specialty': candidate.get('specialty', ''),
                },
                'differs': conflict,
            }
            existing.save(update_fields=['pending_conflict', 'updated_at'])
            return existing, False, True
        return existing, False, False

    lead = Lead.objects.create(
        first_name=candidate.get('first_name', ''),
        last_name=candidate.get('last_name', ''),
        email=email,
        phone=candidate.get('phone', ''),
        npi=npi_val,
        organization=candidate.get('organization', ''),
        role=candidate.get('role', ''),
        specialty=candidate.get('specialty', ''),
        contact_url=(candidate.get('contact_url') or '').strip(),
        linkedin_url=(candidate.get('linkedin_url') or '').strip(),
        geography=candidate.get('geography', {}) or {},
        source=default_source,
        enrichment_status=default_enrichment,
    )
    return lead, True, False


def _diff_fields(lead: Lead, candidate: dict) -> list[str]:
    """Return list of field names where incoming differs from existing in a meaningful way."""
    differs = []
    pairs = [
        ('first_name', lead.first_name, candidate.get('first_name', '')),
        ('last_name', lead.last_name, candidate.get('last_name', '')),
        ('organization', lead.organization, candidate.get('organization', '')),
    ]
    for field, existing_val, incoming_val in pairs:
        existing_norm = (existing_val or '').strip().lower()
        incoming_norm = (incoming_val or '').strip().lower()
        if incoming_norm and existing_norm and incoming_norm != existing_norm:
            differs.append(field)
    return differs


def resolve_conflict(lead: Lead, action: str) -> Lead:
    """action='merge' applies incoming fields to lead; 'skip' just clears the flag."""
    if not lead.pending_conflict:
        return lead
    if action == 'merge':
        incoming = lead.pending_conflict.get('incoming') or {}
        for field in ('first_name', 'last_name', 'organization', 'role', 'specialty', 'phone'):
            val = (incoming.get(field) or '').strip()
            if val:
                setattr(lead, field, val)
    lead.pending_conflict = None
    lead.save()
    return lead


@transaction.atomic
def source_from_npi(project: Project, *, limit: int = 100) -> SourcingResult:
    result = SourcingResult(source='npi')
    profile: PartnerProfile | None = getattr(project, 'partner_profile', None)
    if not profile:
        result.errors.append('Define a Partner Profile before sourcing.')
        return result
    if profile.partner_type == PartnerProfile.PARTNER_SUPPORT_GROUP:
        result.errors.append('NPI Registry does not list support groups — use "Suggest with AI" instead.')
        return result

    taxonomies = [t for t in (profile.specialty_tags or []) if t and t.strip()]
    if not taxonomies:
        result.errors.append('Add at least one specialty tag to the partner profile.')
        return result

    geography = profile.geography or {}
    state = (geography.get('states') or [None])[0] if geography.get('type') == 'state' else None
    postal_code = geography.get('zip') if geography.get('type') == 'zip_radius' else None

    try:
        raw_candidates, unrecognized = npi.search_multi(
            taxonomies=taxonomies,
            state=state,
            postal_code=postal_code,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 — surface network/HTTP errors to the UI
        result.errors.append(f'NPI API error: {exc}')
        return result

    if unrecognized:
        result.errors.append(
            f"Not recognized by NPI: {', '.join(unrecognized)}. "
            f"Use real CMS taxonomy descriptions like 'Medical Oncology', 'Cardiology', "
            f"'Family Medicine', 'Pediatrics', etc. — not generic terms like 'clinical research'."
        )

    # Physician filter: for clinician / investigator partner types, only keep
    # MDs/DOs (taxonomy codes 207X / 208X). Otherwise NPs, RNs, PAs, social
    # workers, chaplains, etc. flood the list.
    if profile.partner_type in (PartnerProfile.PARTNER_CLINICIAN, PartnerProfile.PARTNER_INVESTIGATOR):
        before = len(raw_candidates)
        raw_candidates = [c for c in raw_candidates if npi.is_physician(c)]
        filtered_out = before - len(raw_candidates)
        if filtered_out:
            result.errors.append(f'Filtered {filtered_out} non-physician providers (NPs, PAs, RNs, etc.).')

    result.candidates_found = len(raw_candidates)
    # NPI rarely returns email; mark new leads as needing enrichment
    for cand in raw_candidates:
        lead, created, conflict = _persist_candidate(cand, default_source=Lead.SOURCE_NPI, default_enrichment=Lead.ENRICHMENT_NEEDED)
        (result.created if created else result.reused).append(lead.pk)
        if conflict:
            result.conflicts.append(lead.pk)
    return result


@transaction.atomic
def source_from_ai(project: Project, *, limit: int = 30, user=None) -> SourcingResult:
    result = SourcingResult(source='ai_suggested')
    profile: PartnerProfile | None = getattr(project, 'partner_profile', None)
    if not profile:
        result.errors.append('Define a Partner Profile before sourcing.')
        return result

    if not profile.study_indication and not profile.target_org_types:
        result.errors.append(
            'Add a study_indication (or at least target_org_types) to the partner profile '
            'for indication-specific org suggestions. Run "Suggest from project info" if unsure.'
        )

    asset_texts = []
    for asset in project.assets.all():
        if asset.content_text:
            asset_texts.append(
                f'[{asset.get_type_display()}{": " + asset.subject if asset.subject else ""}]\n{asset.content_text}'
            )

    try:
        suggestions = ai_sourcing.suggest_support_groups(
            specialty_tags=profile.specialty_tags or [],
            geography=profile.geography or {},
            study_indication=profile.study_indication or '',
            patient_population_description=profile.patient_population_description or '',
            target_org_types=profile.target_org_types or [],
            target_contact_roles=profile.target_contact_roles or [],
            asset_texts=asset_texts,
            limit=limit,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f'AI error: {exc}')
        return result

    result.candidates_found = len(suggestions)
    for s in suggestions:
        # AI suggestions are org-level; humans go to the contact_url to find the right email
        contact_url = s.get('contact_page_url') or s.get('content_url') or ''
        suggested_roles = s.get('suggested_roles') or []
        lead, created, conflict = _persist_candidate(
            {
                'organization': s.get('organization', ''),
                'role': 'Organization',
                'specialty': ', '.join(profile.specialty_tags or [])[:255],
                'contact_url': contact_url,
                'geography': {
                    'notes': s.get('description', ''),
                    'suggested_roles': suggested_roles,
                },
            },
            default_source=Lead.SOURCE_AI_SUGGESTED,
            default_enrichment=Lead.ENRICHMENT_NEEDED,
        )
        (result.created if created else result.reused).append(lead.pk)
        if conflict:
            result.conflicts.append(lead.pk)
    return result


@transaction.atomic
def find_contact_from_org_page(org_lead: Lead, *, user=None) -> dict:
    """Fetch the org's contact_url (plus common fallback paths on the same
    domain), ask Claude to extract both named contacts AND generic emails, and
    persist each as a new Lead.

    Returns a summary dict with both counts so the UI can distinguish
    "named contacts" vs "just a generic inbox."
    """
    if not org_lead.contact_url:
        return {'ok': False, 'error': 'This lead has no contact URL — edit the lead and add one.'}

    try:
        payload = ai_sourcing.extract_contacts_from_url(
            url=org_lead.contact_url,
            org_name=org_lead.organization,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': str(exc)}

    contacts = payload.get('contacts') or []
    fallbacks = payload.get('fallback_emails') or []
    fetched_urls = payload.get('fetched_urls') or []

    if not contacts and not fallbacks:
        return {
            'ok': True,
            'created_count': 0,
            'contacts_count': 0,
            'fallback_count': 0,
            'fetched_urls': fetched_urls,
            'note': 'No names or emails found on this domain. Try "Web search" or edit the URL.',
        }

    all_emails = [c.get('email') for c in contacts if c.get('email')] + [f['email'] for f in fallbacks]
    opted_out = set(OptOut.objects.filter(email__in=all_emails).values_list('email', flat=True))

    created_pks = []
    # Named contacts
    for c in contacts:
        email = c.get('email')
        if email and email in opted_out:
            continue
        lead, created, _ = _persist_candidate(
            {
                'first_name': c['first_name'],
                'last_name': c['last_name'],
                'role': c['role'] or 'Contact',
                'email': email,
                'phone': c['phone'],
                'organization': org_lead.organization,
                'specialty': org_lead.specialty,
                'contact_url': org_lead.contact_url,
                'geography': {'notes': c.get('notes', '')},
            },
            default_source=Lead.SOURCE_AI_SUGGESTED,
            default_enrichment=Lead.ENRICHMENT_COMPLETE if email else Lead.ENRICHMENT_NEEDED,
        )
        if created:
            created_pks.append(lead.pk)

    # Fallback generic emails — separate Lead rows, marked as admin inbox
    for fb in fallbacks:
        if fb['email'] in opted_out:
            continue
        lead, created, _ = _persist_candidate(
            {
                'first_name': '',
                'last_name': '',
                'role': f'General inquiries ({fb.get("context") or "admin inbox"})',
                'email': fb['email'],
                'organization': org_lead.organization,
                'specialty': org_lead.specialty,
                'contact_url': org_lead.contact_url,
                'geography': {'notes': 'Generic admin/info email — use to request redirect to the right contact'},
            },
            default_source=Lead.SOURCE_AI_SUGGESTED,
            default_enrichment=Lead.ENRICHMENT_COMPLETE,
        )
        if created:
            created_pks.append(lead.pk)

    return {
        'ok': True,
        'created_count': len(created_pks),
        'contacts_count': len(contacts),
        'fallback_count': len(fallbacks),
        'fetched_urls': fetched_urls,
    }


@transaction.atomic
def import_from_monday_board(
    project: Project,
    *,
    board_id: str,
    column_map: dict,
    user,
) -> SourcingResult:
    """Fetch items from a Monday board and import them as Leads.
    column_map keys: email, first_name, last_name, organization, role, phone, specialty.
    """
    result = SourcingResult(source='monday_import')
    try:
        payload = monday_client.list_board_items(user, board_id)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f'Monday API error: {exc}')
        return result

    items = payload.get('items') or []
    result.candidates_found = len(items)
    opted_out = set(OptOut.objects.values_list('email', flat=True))

    for item in items:
        cvs = {cv.get('column', {}).get('id'): (cv.get('text') or '').strip() for cv in item.get('column_values') or []}

        email_raw = cvs.get(column_map.get('email', '')) or ''
        email_norm = email_raw.strip().lower() or None
        if email_norm and email_norm in opted_out:
            result.skipped_opted_out += 1
            continue

        first = cvs.get(column_map.get('first_name', '')) or ''
        last = cvs.get(column_map.get('last_name', '')) or ''
        # If neither first/last columns mapped, fall back to item.name split
        if not first and not last and item.get('name'):
            parts = item['name'].strip().split(' ', 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ''

        candidate = {
            'first_name': first,
            'last_name': last,
            'email': email_norm,
            'phone': cvs.get(column_map.get('phone', '')) or '',
            'organization': cvs.get(column_map.get('organization', '')) or '',
            'role': cvs.get(column_map.get('role', '')) or '',
            'specialty': cvs.get(column_map.get('specialty', '')) or '',
            'geography': {'monday_board_id': board_id, 'monday_item_id': item.get('id')},
        }

        lead, created, conflict = _persist_candidate(
            candidate,
            default_source=Lead.SOURCE_MONDAY,
            default_enrichment=Lead.ENRICHMENT_COMPLETE if email_norm else Lead.ENRICHMENT_NEEDED,
        )
        # Remember the Monday item ID on a newly-created lead so we can two-way sync later
        if created and item.get('id'):
            geo = lead.geography or {}
            geo['monday_item_id'] = item['id']
            lead.geography = geo
            lead.save(update_fields=['geography', 'updated_at'])
        (result.created if created else result.reused).append(lead.pk)
        if conflict:
            result.conflicts.append(lead.pk)
    return result


@transaction.atomic
def find_org_contacts_via_web(org_lead: Lead, *, user=None) -> dict:
    """Use Claude + web_search to find contacts for an advocacy/support-group org.
    Persists named contacts AND generic admin emails (info@, contact@) as Leads.
    Validates every source_url before saving — never keep a 404 link.
    If the incoming org_lead's own contact_url is broken, it is cleared too.
    """
    target_roles = (org_lead.geography or {}).get('suggested_roles') or []

    # First: if the org_lead's existing contact_url is broken, clear it up front.
    if org_lead.contact_url and not _head_check(org_lead.contact_url):
        org_lead.contact_url = ''
        org_lead.save(update_fields=['contact_url', 'updated_at'])

    try:
        result = ai_sourcing.find_org_contacts_via_web(
            org_name=org_lead.organization,
            website_url=org_lead.contact_url,
            target_roles=target_roles,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': str(exc)}

    contacts = result.get('contacts') or []
    fallbacks = result.get('fallback_emails') or []

    # Validate every source URL returned by Claude (HEAD-check in parallel).
    all_source_urls = [c.get('source_url') for c in contacts if c.get('source_url')]
    all_source_urls += [f.get('source_url') for f in fallbacks if f.get('source_url')]
    url_ok = validate_urls(all_source_urls)

    all_emails = [c.get('email') for c in contacts if c.get('email')] + [f['email'] for f in fallbacks]
    opted_out = set(OptOut.objects.filter(email__in=all_emails).values_list('email', flat=True))

    created_pks = []
    broken_url_count = 0
    # Named contacts
    for c in contacts:
        email = c.get('email')
        if email and email in opted_out:
            continue
        src = (c.get('source_url') or '').strip()
        if src and not url_ok.get(src, False):
            broken_url_count += 1
            src = ''  # keep the contact but drop the broken URL
        candidate = {
            'first_name': c['first_name'],
            'last_name': c['last_name'],
            'role': c['role'] or 'Contact',
            'email': email,
            'organization': org_lead.organization,
            'specialty': org_lead.specialty,
            'contact_url': src or org_lead.contact_url,
            'linkedin_url': c.get('linkedin_url', ''),
            'geography': {'notes': c.get('notes', '')},
        }
        lead, created, _ = _persist_candidate(
            candidate,
            default_source=Lead.SOURCE_AI_SUGGESTED,
            default_enrichment=Lead.ENRICHMENT_COMPLETE if email else Lead.ENRICHMENT_NEEDED,
        )
        if created:
            created_pks.append(lead.pk)
    # Fallback generic emails (no name, just generic admin inbox)
    for fb in fallbacks:
        if fb['email'] in opted_out:
            continue
        src = (fb.get('source_url') or '').strip()
        if src and not url_ok.get(src, False):
            broken_url_count += 1
            src = ''
        candidate = {
            'first_name': '',
            'last_name': '',
            'role': f'General inquiries ({fb.get("context") or "admin inbox"})',
            'email': fb['email'],
            'organization': org_lead.organization,
            'specialty': org_lead.specialty,
            'contact_url': src or org_lead.contact_url,
            'geography': {'notes': 'Generic admin/info email — use to request redirect to the right contact'},
        }
        lead, created, _ = _persist_candidate(
            candidate,
            default_source=Lead.SOURCE_AI_SUGGESTED,
            default_enrichment=Lead.ENRICHMENT_COMPLETE,
        )
        if created:
            created_pks.append(lead.pk)

    return {
        'ok': True,
        'created_count': len(created_pks),
        'contacts_count': len(contacts),
        'fallback_count': len(fallbacks),
        'broken_urls_dropped': broken_url_count,
    }


@transaction.atomic
def enrich_clinician_via_web(lead: Lead, *, user=None) -> dict:
    """Use Claude + web_search to find a clinician's email from hospital/faculty pages."""
    if not lead.first_name and not lead.last_name:
        return {'ok': False, 'error': 'Lead needs a first or last name to search.'}

    geo = lead.geography or {}
    try:
        result = ai_sourcing.find_clinician_email_via_web(
            first_name=lead.first_name,
            last_name=lead.last_name,
            specialty=lead.specialty,
            city=geo.get('city', ''),
            state=geo.get('state', ''),
            npi=lead.npi or '',
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': str(exc)}

    update_fields = set()
    if result['email'] and not lead.email:
        email = result['email']
        if OptOut.objects.filter(email=email).exists():
            return {'ok': True, 'email': email, 'note': 'Email found but is opted-out — not saved.'}
        lead.email = email
        update_fields.add('email')
    if result['affiliation'] and not lead.organization:
        lead.organization = result['affiliation']
        update_fields.add('organization')
    if result['role'] and not lead.role:
        lead.role = result['role']
        update_fields.add('role')
    if result['source_url'] and not lead.contact_url:
        lead.contact_url = result['source_url']
        update_fields.add('contact_url')
    if result.get('linkedin_url') and not lead.linkedin_url:
        lead.linkedin_url = result['linkedin_url']
        update_fields.add('linkedin_url')

    lead.enrichment_status = Lead.ENRICHMENT_COMPLETE if lead.email else Lead.ENRICHMENT_FAILED
    update_fields.add('enrichment_status')
    update_fields.add('updated_at')
    lead.save(update_fields=list(update_fields))

    return {
        'ok': True,
        'email': lead.email,
        'affiliation': result['affiliation'],
        'role': result['role'],
        'source_url': result['source_url'],
        'confidence': result['confidence'],
        'note': result.get('notes', ''),
    }


def enrich_lead_with_apollo(lead: Lead, *, user=None) -> dict:
    """Call Apollo people/match to fill in email/phone/title on a Lead."""
    if not apollo.is_configured():
        return {'ok': False, 'error': 'Apollo not configured'}
    try:
        match = apollo.enrich_person(
            first_name=lead.first_name,
            last_name=lead.last_name,
            organization=lead.organization,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': str(exc)}

    updated_fields = ['enrichment_status', 'updated_at']
    if match.get('email') and not lead.email:
        email = match['email'].strip().lower()
        if OptOut.objects.filter(email=email).exists():
            return {'ok': True, 'email': email, 'note': 'Email resolved but is opted-out — not saved.'}
        lead.email = email
        updated_fields.append('email')
    if match.get('phone') and not lead.phone:
        lead.phone = match['phone']
        updated_fields.append('phone')
    if match.get('title') and not lead.role:
        lead.role = match['title']
        updated_fields.append('role')
    if match.get('linkedin_url') and not lead.linkedin_url:
        lead.linkedin_url = match['linkedin_url']
        updated_fields.append('linkedin_url')

    lead.enrichment_status = Lead.ENRICHMENT_COMPLETE if lead.email else Lead.ENRICHMENT_FAILED
    lead.save(update_fields=list(set(updated_fields)))

    if not lead.email:
        return {
            'ok': True,
            'email': '',
            'note': (
                f'Apollo matched {match.get("title", "a person")} at this lead but did not '
                f'reveal an email address. This usually means the lead needs a more specific '
                f'organization/domain, or your Apollo plan does not include email reveal.'
            ),
        }
    return {'ok': True, 'email': lead.email, 'updated': 'email' in updated_fields}
