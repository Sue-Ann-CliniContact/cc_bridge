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
        })
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
