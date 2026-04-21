"""AI-assisted lead discovery — Claude proposes candidate orgs / partners.

Used mainly for support groups and advocacy organizations where there is no
directory API. The output is always human-reviewed before import.
"""
from __future__ import annotations

import json
import re

from ai_manager.services import AIService


SUPPORT_GROUP_SYSTEM_PROMPT = (
    "You help a clinical-trial outreach team find legitimate US-based patient "
    "advocacy organizations and support groups for a given therapeutic area. "
    "You propose real, verifiable organizations — national non-profits, condition-"
    "specific foundations, and well-known support communities. You never invent "
    "an org or URL. If you are uncertain whether an org exists, you omit it.\n\n"
    "Output strictly as JSON matching this schema:\n"
    '{"orgs": [{"name": "...", "website": "https://...", '
    '"contact_page_url": "https://...", "description": "one sentence"}]}\n'
    "No other text. No markdown. No trailing commentary."
)


def suggest_support_groups(*, specialty_tags: list[str], geography: dict, limit: int = 30, user=None) -> list[dict]:
    """Ask Claude to propose support groups / advocacy orgs for a partner profile.

    Returns a list of dicts with name/website/contact_page_url/description.
    Caller is expected to present these as AI-suggested (source='ai_suggested'),
    require human review before import.
    """
    specialty_str = ', '.join(specialty_tags) if specialty_tags else '(not specified)'
    geo_str = _format_geography(geography)

    prompt = (
        f"Propose up to {limit} US-based patient support groups or advocacy "
        f"organizations for a clinical trial outreach project with these filters:\n\n"
        f"Therapeutic areas / specialties: {specialty_str}\n"
        f"Geography: {geo_str}\n\n"
        f"Include only organizations you are confident exist. Prefer national "
        f"non-profits and condition-specific foundations. Do not include hospitals, "
        f"clinics, or commercial CROs. Return JSON only."
    )

    raw = AIService.complete(
        prompt=prompt,
        system_prompt=SUPPORT_GROUP_SYSTEM_PROMPT,
        function_name='suggest_support_groups',
        user=user,
        max_tokens=2048,
    )
    return _parse_orgs(raw)


PROFILE_SYSTEM_PROMPT = (
    "You propose a partner-outreach targeting profile for a clinical trial. "
    "Given the project's study materials, you pick a partner type, therapeutic "
    "specialties (free-text tags that map to NPI taxonomy descriptions), ICD-10 "
    "codes if clearly inferable, a geography scope, and a realistic target "
    "population size. You prefer broader targeting unless the materials strongly "
    "imply a narrow niche. You never invent clinical details not supported by "
    "the source material.\n\n"
    "Output strictly as JSON:\n"
    '{"partner_type": "clinician|support_group|research_coordinator|investigator",\n'
    ' "specialty_tags": ["...","..."],\n'
    ' "icd10_codes": ["..."],\n'
    ' "geography": {"type": "national|state|zip_radius", "states": ["NY"], "zip": "", "radius_miles": 50},\n'
    ' "target_size": 100,\n'
    ' "rationale": "one short sentence" }\n'
    "No markdown. No trailing commentary."
)


def suggest_partner_profile(*, project_name: str, study_code: str, asset_texts: list[str], user=None) -> dict:
    """Ask Claude to propose a PartnerProfile from project info + uploaded assets."""
    joined_assets = '\n\n---\n\n'.join(t.strip() for t in asset_texts if t and t.strip()) or '(no assets uploaded)'

    prompt = (
        f"Project: {project_name}\n"
        f"Study code: {study_code}\n\n"
        f"Uploaded study materials (email copy, flyer text, study summary — any subset):\n"
        f"{joined_assets[:6000]}\n\n"
        f"Propose a partner-outreach profile that matches what's in these materials. "
        f"Return JSON only."
    )
    raw = AIService.complete(
        prompt=prompt,
        system_prompt=PROFILE_SYSTEM_PROMPT,
        function_name='suggest_partner_profile',
        user=user,
        max_tokens=1024,
    )
    return _parse_profile(raw)


def _parse_profile(raw: str) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    if not isinstance(data, dict):
        return {}

    return {
        'partner_type': (data.get('partner_type') or '').strip(),
        'specialty_tags': [t.strip() for t in (data.get('specialty_tags') or []) if t and str(t).strip()],
        'icd10_codes': [c.strip() for c in (data.get('icd10_codes') or []) if c and str(c).strip()],
        'geography': data.get('geography') or {'type': 'national'},
        'target_size': int(data.get('target_size') or 100),
        'rationale': (data.get('rationale') or '').strip(),
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


def _parse_orgs(raw: str) -> list[dict]:
    """Extract the JSON block from Claude's response and normalize fields."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    orgs = data.get('orgs') if isinstance(data, dict) else []
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
