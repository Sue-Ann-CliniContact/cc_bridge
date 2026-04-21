"""Lead-sourcing orchestration.

Each `source_from_*` fn takes a Project, pulls candidates from an external
source, filters against OptOut, dedups against the global Lead table, and
persists new Leads. Returns a summary dict the UI can render as a flash
message. No ProjectLead rows are created here — selection-to-project happens
in a separate step after human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from core.models import Lead, OptOut, PartnerProfile, Project
from integrations import ai_sourcing, apollo, monday_client, npi


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
    """Fetch the org's contact_url, ask Claude to extract named contacts, and
    persist each as a new Lead. If an existing Lead matches by email, dedup as usual.

    Returns a summary the UI can render: {created_count, contacts: [{name, email, ...}]}.
    """
    if not org_lead.contact_url:
        return {'ok': False, 'error': 'This lead has no contact URL.'}

    try:
        contacts = ai_sourcing.extract_contacts_from_url(
            url=org_lead.contact_url,
            org_name=org_lead.organization,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': str(exc)}

    if not contacts:
        return {
            'ok': True,
            'created_count': 0,
            'contacts': [],
            'note': 'Page has no named contacts (only generic forms) — try a staff / about page.',
        }

    opted_out = set(OptOut.objects.filter(
        email__in=[c['email'] for c in contacts if c.get('email')]
    ).values_list('email', flat=True))

    created_pks = []
    preview = []
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
        preview.append({
            'lead_id': lead.pk,
            'name': f"{lead.first_name} {lead.last_name}".strip(),
            'role': lead.role,
            'email': lead.email or '',
        })
    return {'ok': True, 'created_count': len(created_pks), 'contacts': preview}


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

    updated = False
    if match.get('email') and not lead.email:
        email = match['email'].strip().lower()
        if OptOut.objects.filter(email=email).exists():
            return {'ok': True, 'email': email, 'note': 'Email resolved but is opted-out — not saved.'}
        lead.email = email
        updated = True
    if match.get('phone') and not lead.phone:
        lead.phone = match['phone']
        updated = True
    if match.get('title') and not lead.role:
        lead.role = match['title']
        updated = True

    lead.enrichment_status = Lead.ENRICHMENT_COMPLETE if lead.email else Lead.ENRICHMENT_FAILED
    if updated:
        lead.save(update_fields=['email', 'phone', 'role', 'enrichment_status', 'updated_at'])
    return {'ok': True, 'email': lead.email, 'updated': updated}
