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
    "You help a clinical-trial outreach team find legitimate US-based partner "
    "organizations for a specific study. The study indication is stated explicitly "
    "— your job is to propose orgs whose mission aligns with THAT indication, not "
    "the broader therapeutic area.\n\n"
    "Hard rule: every org you return must be directly relevant to the stated "
    "indication and target_org_types. If the study is about HER2+ metastatic breast "
    "cancer, you do NOT return 'Children's Tumor Foundation' (neurofibromatosis) or "
    "generic 'American Cancer Society' unless that org has a program specifically "
    "serving this patient population. Prefer small, indication-specific foundations "
    "and condition-specific patient registries over giant umbrella charities.\n\n"
    "Only include real, well-known US organizations with verifiable public websites "
    "and missions. Do not invent names or URLs. Do not include hospitals, clinics, "
    "or commercial CROs unless they run a relevant patient-support program.\n\n"
    "For each org, also propose 1-3 likely contact roles at that specific type of "
    "org — align these with the study's target_contact_roles where possible."
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


def suggest_support_groups(
    *,
    specialty_tags: list[str],
    geography: dict,
    study_indication: str = '',
    patient_population_description: str = '',
    target_org_types: list[str] | None = None,
    target_contact_roles: list[str] | None = None,
    asset_texts: list[str] | None = None,
    limit: int = 30,
    user=None,
) -> list[dict]:
    target_org_types = target_org_types or []
    target_contact_roles = target_contact_roles or []
    specialty_str = ', '.join(specialty_tags) if specialty_tags else '(not specified)'
    geo_str = _format_geography(geography)
    org_types_str = '\n'.join(f'- {t}' for t in target_org_types) if target_org_types else '- (no preference stated)'
    roles_str = ', '.join(target_contact_roles) if target_contact_roles else '(not specified)'
    indication_line = (
        f"Study indication (REQUIRED MATCH): {study_indication}"
        if study_indication
        else "Study indication: (not specified — fall back to specialty_tags for matching)"
    )
    pop_line = (
        f"Patient population: {patient_population_description}"
        if patient_population_description
        else ""
    )
    asset_section = ''
    if asset_texts:
        joined_assets = '\n\n---\n\n'.join(t.strip() for t in asset_texts if t and t.strip())
        if joined_assets.strip():
            asset_section = (
                f"\n\nStudy materials (for additional grounding — do not propose orgs "
                f"unrelated to this indication):\n{joined_assets[:4000]}"
            )

    prompt = (
        f"Propose up to {min(limit, 30)} US-based partner organizations for this clinical trial.\n\n"
        f"{indication_line}\n"
        f"{pop_line}\n"
        f"Therapeutic specialties (broad): {specialty_str}\n"
        f"Target org categories (ranked):\n{org_types_str}\n"
        f"Target contact roles: {roles_str}\n"
        f"Geography: {geo_str}{asset_section}\n\n"
        f"Use the return_support_groups tool. Every org must serve the specific "
        f"indication above. Order from most indication-specific to most general."
    )

    result = AIService.call_structured(
        prompt=prompt,
        system_prompt=SUPPORT_GROUP_SYSTEM_PROMPT,
        tool_name='return_support_groups',
        tool_description='Return a list of US partner organizations specific to the study indication.',
        tool_schema=SUPPORT_GROUP_TOOL_SCHEMA,
        function_name='suggest_support_groups',
        user=user,
        max_tokens=4096,
    )
    orgs = result.get('orgs') if isinstance(result, dict) else []
    log.info(
        'AI suggested %d orgs for indication=%r org_types=%s',
        len(orgs or []), study_indication[:80], target_org_types,
    )

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
    "You are a clinical-trial partner-outreach strategist. You read the uploaded "
    "study materials closely and propose a targeting profile grounded in what the "
    "study actually recruits for — not a generic profile for the broader therapeutic "
    "area. If the study is about HER2+ metastatic breast cancer, you propose orgs, "
    "specialties, and roles specific to HER2+ metastatic breast cancer — not "
    "\"all of oncology.\"\n\n"
    "You return seven things:\n"
    "1. study_indication — the specific condition the study targets, in one phrase. "
    "Include stage, biomarker, or line-of-therapy if the materials state it.\n"
    "2. patient_population_description — 2-4 sentences describing the patients "
    "(disease stage, key inclusion criteria, age, demographics).\n"
    "3. target_org_types — 2-5 categories of organizations to reach, ordered from "
    "most indication-specific to most general. Examples: \"foundations specific "
    "to [indication]\", \"NCI-designated comprehensive cancer centers\", "
    "\"academic medical centers with [specialty] programs\", \"community "
    "oncology networks\", \"patient registries for [indication]\". Be "
    "indication-specific when possible.\n"
    "4. target_contact_roles — 3-6 specific titles of people to reach. Examples: "
    "\"Principal Investigator\", \"Clinical Research Coordinator\", "
    "\"Director of Clinical Research\", \"Patient Navigator\", \"Director of "
    "Patient Services\", \"Medical Director\". Pick roles that match the org "
    "types — clinical roles at medical centers, program roles at advocacy orgs.\n"
    "5. specialty_tags — 1-3 REAL CMS NPI Registry taxonomy descriptions (the NPI "
    "API will reject free-text topics like 'clinical research' or 'hematologic "
    "malignancies'). Valid examples: 'Medical Oncology', 'Radiation Oncology', "
    "'Hematology & Oncology', 'Cardiology', 'Family Medicine', 'Pediatrics', "
    "'Neurology', 'Dermatology', 'Endocrinology, Diabetes & Metabolism', "
    "'Gastroenterology', 'Nephrology', 'Pulmonary Disease', 'Rheumatology', "
    "'Psychiatry'. If no single CMS taxonomy matches the indication well, pick "
    "the closest physician specialty.\n"
    "6. icd10_codes — 1-3 codes inferable from the materials (optional).\n"
    "7. geography — national unless the materials name specific sites/states.\n\n"
    "One-sentence rationale ties the profile to what you read in the materials."
)

PROFILE_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'partner_type': {
            'type': 'string',
            'enum': ['clinician', 'support_group', 'research_coordinator', 'investigator'],
        },
        'study_indication': {
            'type': 'string',
            'description': 'The specific condition the study recruits for — one phrase with stage/biomarker/line if stated',
        },
        'patient_population_description': {
            'type': 'string',
            'description': '2-4 sentences describing the target patients',
        },
        'target_org_types': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': '2-5 org categories, most indication-specific first',
        },
        'target_contact_roles': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': '3-6 specific titles to reach at those orgs',
        },
        'specialty_tags': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': '1-3 real CMS NPI taxonomy descriptions',
        },
        'icd10_codes': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': 'ICD-10 codes inferable from materials (optional)',
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
        'rationale': {'type': 'string', 'description': 'One sentence tying the profile to what you read'},
    },
    'required': [
        'partner_type', 'study_indication', 'patient_population_description',
        'target_org_types', 'target_contact_roles', 'specialty_tags', 'geography', 'target_size',
    ],
}


def suggest_partner_profile(*, project_name: str, study_code: str, asset_texts: list[str], user=None) -> dict:
    joined_assets = '\n\n---\n\n'.join(t.strip() for t in asset_texts if t and t.strip()) or '(no assets uploaded — infer conservatively from project name/code only)'
    prompt = (
        f"Project: {project_name}\n"
        f"Study code: {study_code}\n\n"
        f"Uploaded study materials:\n{joined_assets[:8000]}\n\n"
        f"Use the return_partner_profile tool. Be specific to this study's indication, "
        f"not the broader therapeutic area."
    )
    result = AIService.call_structured(
        prompt=prompt,
        system_prompt=PROFILE_SYSTEM_PROMPT,
        tool_name='return_partner_profile',
        tool_description='Return an indication-specific partner-outreach targeting profile.',
        tool_schema=PROFILE_TOOL_SCHEMA,
        function_name='suggest_partner_profile',
        user=user,
        max_tokens=2048,
    )
    if not isinstance(result, dict):
        return {}
    return {
        'partner_type': (result.get('partner_type') or '').strip(),
        'study_indication': (result.get('study_indication') or '').strip(),
        'patient_population_description': (result.get('patient_population_description') or '').strip(),
        'target_org_types': [t.strip() for t in (result.get('target_org_types') or []) if t and str(t).strip()],
        'target_contact_roles': [t.strip() for t in (result.get('target_contact_roles') or []) if t and str(t).strip()],
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
