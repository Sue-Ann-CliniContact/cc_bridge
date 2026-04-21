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
from integrations import ai_sourcing, apollo, npi


@dataclass
class SourcingResult:
    source: str
    candidates_found: int = 0
    created: list[int] = field(default_factory=list)  # lead IDs
    reused: list[int] = field(default_factory=list)
    skipped_opted_out: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self):
        return {
            'source': self.source,
            'candidates_found': self.candidates_found,
            'created_count': len(self.created),
            'reused_count': len(self.reused),
            'skipped_opted_out': self.skipped_opted_out,
            'errors': self.errors,
        }


def _opted_out_emails(emails: list[str]) -> set[str]:
    if not emails:
        return set()
    return set(OptOut.objects.filter(email__in=emails).values_list('email', flat=True))


def _persist_candidate(candidate: dict, default_source: str, default_enrichment: str = Lead.ENRICHMENT_COMPLETE) -> tuple[Lead, bool]:
    """Look up an existing Lead by email or npi; create if absent.
    Returns (lead, created)."""
    email = (candidate.get('email') or '').strip().lower() or None
    npi_val = (candidate.get('npi') or '').strip() or None

    existing = None
    if email:
        existing = Lead.objects.filter(email__iexact=email).first()
    if not existing and npi_val:
        existing = Lead.objects.filter(npi=npi_val).first()

    if existing:
        return existing, False

    lead = Lead.objects.create(
        first_name=candidate.get('first_name', ''),
        last_name=candidate.get('last_name', ''),
        email=email,
        phone=candidate.get('phone', ''),
        npi=npi_val,
        organization=candidate.get('organization', ''),
        role=candidate.get('role', ''),
        specialty=candidate.get('specialty', ''),
        geography=candidate.get('geography', {}) or {},
        source=default_source,
        enrichment_status=default_enrichment,
    )
    return lead, True


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

    taxonomy = profile.specialty_tags[0] if profile.specialty_tags else None
    geography = profile.geography or {}
    state = (geography.get('states') or [None])[0] if geography.get('type') == 'state' else None
    postal_code = geography.get('zip') if geography.get('type') == 'zip_radius' else None

    try:
        raw_candidates = npi.search(
            taxonomy=taxonomy,
            state=state,
            postal_code=postal_code,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 — surface network/HTTP errors to the UI
        result.errors.append(f'NPI API error: {exc}')
        return result

    result.candidates_found = len(raw_candidates)
    # NPI rarely returns email; mark new leads as needing enrichment
    for cand in raw_candidates:
        lead, created = _persist_candidate(cand, default_source=Lead.SOURCE_NPI, default_enrichment=Lead.ENRICHMENT_NEEDED)
        (result.created if created else result.reused).append(lead.pk)
    return result


@transaction.atomic
def source_from_ai(project: Project, *, limit: int = 30, user=None) -> SourcingResult:
    result = SourcingResult(source='ai_suggested')
    profile: PartnerProfile | None = getattr(project, 'partner_profile', None)
    if not profile:
        result.errors.append('Define a Partner Profile before sourcing.')
        return result

    try:
        suggestions = ai_sourcing.suggest_support_groups(
            specialty_tags=profile.specialty_tags or [],
            geography=profile.geography or {},
            limit=limit,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f'AI error: {exc}')
        return result

    result.candidates_found = len(suggestions)
    for s in suggestions:
        # AI suggestions are org-level; we persist without email (human must find it)
        lead, created = _persist_candidate(
            {
                'organization': s.get('organization', ''),
                'role': 'Organization',
                'specialty': ', '.join(profile.specialty_tags or [])[:255],
                'geography': {'notes': s.get('description', '')},
            },
            default_source=Lead.SOURCE_AI_SUGGESTED,
            default_enrichment=Lead.ENRICHMENT_NEEDED,
        )
        (result.created if created else result.reused).append(lead.pk)
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
