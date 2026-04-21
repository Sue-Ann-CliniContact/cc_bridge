"""AI-assisted lead discovery — Claude proposes candidate orgs / partners.

Uses Claude's tool-use feature so the output is guaranteed to match a JSON schema
(no prose-parsing flakiness). All suggestions are human-reviewed before import.
"""
from __future__ import annotations

import logging

from ai_manager.services import AIService

log = logging.getLogger(__name__)


# ──────────────────────── support-group suggestion ──────────────────────────

SUPPORT_GROUP_SYSTEM_PROMPT = (
    "You help a clinical-trial outreach team find legitimate US-based patient "
    "advocacy organizations and support groups for a given therapeutic area. "
    "These are public, well-known non-profits and condition-specific foundations — "
    "you are NOT inventing anything, they have public websites and mission "
    "statements that any search engine can verify. Examples of what you should "
    "include (when relevant to the therapeutic area): American Cancer Society, "
    "Leukemia & Lymphoma Society, National Kidney Foundation, American Heart "
    "Association, JDRF, Crohn's & Colitis Foundation, Cystic Fibrosis "
    "Foundation, Michael J. Fox Foundation, etc. Include between 15 and 30 orgs. "
    "Do not include hospitals, clinics, or commercial CROs."
)

SUPPORT_GROUP_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'orgs': {
            'type': 'array',
            'description': '15–30 suggested US patient advocacy orgs / support groups',
            'items': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'description': 'Organization name'},
                    'website': {'type': 'string', 'description': 'Homepage URL if known'},
                    'contact_page_url': {'type': 'string', 'description': 'Contact / outreach URL if known'},
                    'description': {'type': 'string', 'description': 'One-sentence description'},
                    'suggested_roles': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': '1–3 likely contact titles at this org for clinical-trial partnership (e.g. "Director of Patient Services", "VP of Medical Affairs", "Executive Director"). Helps the team know who to look for.',
                    },
                },
                'required': ['name'],
            },
        },
    },
    'required': ['orgs'],
}


def suggest_support_groups(*, specialty_tags: list[str], geography: dict, limit: int = 30, user=None) -> list[dict]:
    specialty_str = ', '.join(specialty_tags) if specialty_tags else '(not specified — pick broadly applicable orgs)'
    geo_str = _format_geography(geography)

    prompt = (
        f"Propose {min(limit, 30)} US-based patient support groups or advocacy "
        f"organizations for a clinical trial outreach project.\n\n"
        f"Therapeutic areas / specialties: {specialty_str}\n"
        f"Geography: {geo_str}\n\n"
        f"Use the return_support_groups tool to return your list."
    )

    result = AIService.call_structured(
        prompt=prompt,
        system_prompt=SUPPORT_GROUP_SYSTEM_PROMPT,
        tool_name='return_support_groups',
        tool_description='Return a list of suggested US patient advocacy organizations.',
        tool_schema=SUPPORT_GROUP_TOOL_SCHEMA,
        function_name='suggest_support_groups',
        user=user,
        max_tokens=4096,
    )
    orgs = result.get('orgs') if isinstance(result, dict) else []
    log.info('AI suggested %d support groups for specialties=%s', len(orgs or []), specialty_tags)

    normalized = []
    for org in orgs or []:
        if not isinstance(org, dict) or not org.get('name'):
            continue
        normalized.append({
            'organization': org.get('name', '').strip(),
            'content_url': org.get('website', '').strip(),
            'contact_page_url': org.get('contact_page_url', '').strip(),
            'description': org.get('description', '').strip(),
            'suggested_roles': [r.strip() for r in (org.get('suggested_roles') or []) if r and str(r).strip()][:3],
        })
    return normalized


# ──────────────────────── page-level contact extraction ──────────────────────

CONTACT_EXTRACT_SYSTEM_PROMPT = (
    "You extract outreach-worthy contacts from a webpage's text. The caller is a "
    "clinical-trial outreach team trying to find a real person at this organization "
    "to contact about a partnership. You prefer named individuals over generic "
    "inboxes (info@, contact@). For each named person you find, return their name, "
    "title/role, email if on the page, phone if on the page, and a one-line note. "
    "If the page only has a contact form with no names, return an empty list — "
    "the team will handle those manually. Do not invent contacts not present in the text."
)

CONTACT_EXTRACT_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'contacts': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'description': 'Full name (first + last)'},
                    'title': {'type': 'string', 'description': 'Role / title at the organization'},
                    'email': {'type': 'string', 'description': 'Email on the page, if present'},
                    'phone': {'type': 'string', 'description': 'Phone number on the page, if present'},
                    'notes': {'type': 'string', 'description': 'One short line of context from the page'},
                },
                'required': ['name'],
            },
        },
    },
    'required': ['contacts'],
}


def extract_contacts_from_url(*, url: str, org_name: str, user=None) -> list[dict]:
    """Fetch a page, strip to text, ask Claude to extract outreach contacts.
    Returns a possibly-empty list of contact dicts."""
    import requests
    from django.utils.html import strip_tags

    try:
        # Browser-style headers — some sites reject the default requests UA outright.
        r = requests.get(
            url,
            timeout=15,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/122.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Upgrade-Insecure-Requests': '1',
            },
            allow_redirects=True,
        )
        r.raise_for_status()
    except requests.HTTPError as exc:
        status = getattr(exc.response, 'status_code', None)
        if status in (401, 403, 429):
            raise RuntimeError(
                f'{url} blocked the fetch (HTTP {status}). This site rejects crawler requests. '
                f'Open it manually and add a contact via "Edit lead."'
            ) from exc
        raise RuntimeError(f'Could not load page (HTTP {status}): {exc}') from exc
    except Exception as exc:  # noqa: BLE001
        log.warning('Contact extract: failed to fetch %s: %s', url, exc)
        raise RuntimeError(f'Could not load page ({exc})') from exc

    # Cap to a sane size before stripping — some pages are huge
    raw_html = r.text[:600_000]
    text = strip_tags(raw_html)
    text = ' '.join(text.split())[:20_000]

    if not text.strip():
        return []

    prompt = (
        f"Organization: {org_name}\n"
        f"Page URL: {url}\n\n"
        f"Page text (extracted from HTML):\n---\n{text}\n---\n\n"
        f"Use the return_contacts tool to return any named contacts suitable for "
        f"clinical-trial partnership outreach. Return an empty list if the page "
        f"only has generic email forms or no named people."
    )
    result = AIService.call_structured(
        prompt=prompt,
        system_prompt=CONTACT_EXTRACT_SYSTEM_PROMPT,
        tool_name='return_contacts',
        tool_description='Return named outreach contacts extracted from the page text.',
        tool_schema=CONTACT_EXTRACT_TOOL_SCHEMA,
        function_name='extract_contacts_from_url',
        user=user,
        max_tokens=2048,
    )
    contacts = result.get('contacts') if isinstance(result, dict) else []
    normalized = []
    for c in contacts or []:
        if not isinstance(c, dict) or not c.get('name'):
            continue
        name_parts = c.get('name', '').strip().split(' ', 1)
        normalized.append({
            'first_name': name_parts[0] if name_parts else '',
            'last_name': name_parts[1] if len(name_parts) > 1 else '',
            'role': (c.get('title') or '').strip(),
            'email': (c.get('email') or '').strip().lower() or None,
            'phone': (c.get('phone') or '').strip(),
            'notes': (c.get('notes') or '').strip(),
        })
    log.info('extract_contacts_from_url: %s → %d contacts', url, len(normalized))
    return normalized


# ──────────────────────── partner-profile suggestion ─────────────────────────

PROFILE_SYSTEM_PROMPT = (
    "You propose a partner-outreach targeting profile for a clinical trial. "
    "Given the project's study materials, pick a partner type, therapeutic "
    "specialties, ICD-10 codes if clearly inferable, a geography scope, and a "
    "realistic target population size.\n\n"
    "CRITICAL — specialty_tags must be real CMS NPI Registry taxonomy descriptions, "
    "not free-text topics. The NPI API will reject generic terms like 'clinical "
    "research' or 'hematologic malignancies'. Use actual CMS specialty names. "
    "Examples of valid values: 'Medical Oncology', 'Radiation Oncology', "
    "'Hematology & Oncology', 'Internal Medicine', 'Family Medicine', 'Pediatrics', "
    "'Cardiovascular Disease', 'Cardiology', 'Neurology', 'Dermatology', "
    "'Endocrinology, Diabetes & Metabolism', 'Gastroenterology', 'Nephrology', "
    "'Pulmonary Disease', 'Rheumatology', 'Psychiatry'. Include 1-3 tags that best "
    "match the trial's therapeutic area.\n\n"
    "Prefer broader targeting unless the materials strongly imply a narrow niche. "
    "Never invent clinical details not supported by the source material."
)

PROFILE_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'partner_type': {
            'type': 'string',
            'enum': ['clinician', 'support_group', 'research_coordinator', 'investigator'],
        },
        'specialty_tags': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': 'Free-text specialty tags that map to NPI taxonomy descriptions (e.g. "Medical Oncology")',
        },
        'icd10_codes': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': 'ICD-10 codes inferable from study materials (optional)',
        },
        'geography': {
            'type': 'object',
            'properties': {
                'type': {'type': 'string', 'enum': ['national', 'state', 'zip_radius']},
                'states': {'type': 'array', 'items': {'type': 'string'}},
                'zip': {'type': 'string'},
                'radius_miles': {'type': 'integer'},
            },
            'required': ['type'],
        },
        'target_size': {'type': 'integer', 'description': 'Approximate number of leads to source'},
        'rationale': {'type': 'string', 'description': 'One short sentence on why this profile'},
    },
    'required': ['partner_type', 'specialty_tags', 'geography', 'target_size'],
}


def suggest_partner_profile(*, project_name: str, study_code: str, asset_texts: list[str], user=None) -> dict:
    joined_assets = '\n\n---\n\n'.join(t.strip() for t in asset_texts if t and t.strip()) or '(no assets uploaded)'
    prompt = (
        f"Project: {project_name}\n"
        f"Study code: {study_code}\n\n"
        f"Uploaded study materials:\n{joined_assets[:6000]}\n\n"
        f"Use the return_partner_profile tool to propose a targeting profile."
    )
    result = AIService.call_structured(
        prompt=prompt,
        system_prompt=PROFILE_SYSTEM_PROMPT,
        tool_name='return_partner_profile',
        tool_description='Return a partner-outreach targeting profile.',
        tool_schema=PROFILE_TOOL_SCHEMA,
        function_name='suggest_partner_profile',
        user=user,
        max_tokens=1024,
    )
    if not isinstance(result, dict):
        return {}
    return {
        'partner_type': (result.get('partner_type') or '').strip(),
        'specialty_tags': [t.strip() for t in (result.get('specialty_tags') or []) if t and str(t).strip()],
        'icd10_codes': [c.strip() for c in (result.get('icd10_codes') or []) if c and str(c).strip()],
        'geography': result.get('geography') or {'type': 'national'},
        'target_size': int(result.get('target_size') or 100),
        'rationale': (result.get('rationale') or '').strip(),
    }


def _format_geography(geography: dict) -> str:
    if not geography:
        return 'United States (national)'
    mode = geography.get('type', 'national')
    if mode == 'state':
        states = geography.get('states') or []
        return f"US states: {', '.join(states) if states else '(not specified)'}"
    if mode == 'zip_radius':
        zip_code = geography.get('zip', '')
        radius = geography.get('radius_miles', 50)
        return f"Within {radius} miles of ZIP {zip_code}"
    return 'United States (national)'
