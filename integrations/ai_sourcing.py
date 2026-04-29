"""AI-assisted lead discovery — Claude proposes candidate orgs / partners.

Uses Claude's tool-use feature so the output is guaranteed to match a JSON schema
(no prose-parsing flakiness). All suggestions are human-reviewed before import.
"""
from __future__ import annotations

import json
import logging

from ai_manager.services import AIService

log = logging.getLogger(__name__)


# ──────────────────────── support-group suggestion ──────────────────────────

SUPPORT_GROUP_SYSTEM_PROMPT = (
    "You help a clinical-trial outreach team find legitimate US-based partner "
    "organizations and provider institutions for a specific study. You return "
    "a list ordered from most indication-specific to most general.\n\n"
    "Preferred (top of list): foundations and registries whose mission directly "
    "targets the stated indication. Include these first even if they're smaller. "
    "If the study is about HER2+ metastatic breast cancer, the top entries are "
    "HER2-focused or metastatic-breast-cancer-focused foundations.\n\n"
    "Secondary (further down the list): broader therapeutic-area orgs with relevant "
    "programs — e.g., the American Cancer Society for any oncology study, the "
    "Leukemia & Lymphoma Society for any blood-cancer study. Include these so the "
    "team has options; mark them clearly with a broader description.\n\n"
    "Exclude: organizations unrelated to the indication's therapeutic area. No "
    "children's tumor foundations for adult breast cancer studies, no heart-disease "
    "charities for oncology studies, etc.\n\n"
    "For national projects, aim for roughly 40-60 organizations if the category "
    "set is broad enough. For narrower projects, still aim for at least 25-40. "
    "Do not stop at a short list unless the category is truly tiny. If the "
    "indication is narrow, fill the list with broader-therapeutic-area orgs and "
    "relevant provider institutions instead of returning nothing.\n\n"
    "Only include real US organizations or provider institutions with verifiable "
    "public websites. Do not invent names or URLs. If the requested categories "
    "include clinics, hospitals, academic medical centers, metabolic/genetic "
    "programs, or provider groups, include those directly instead of filtering "
    "only for nonprofits.\n\n"
    "For each org, propose 1-3 likely contact roles at that specific type of org — "
    "align these with the study's target_contact_roles where possible."
)

SUPPORT_GROUP_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'orgs': {
            'type': 'array',
            'description': '25-60 suggested US partner orgs, provider institutions, clinics, or support groups',
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
    exclude_org_names: list[str] | None = None,
    limit: int = 30,
    user=None,
) -> list[dict]:
    target_org_types = target_org_types or []
    target_contact_roles = target_contact_roles or []
    exclude_org_names = exclude_org_names or []
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
    exclusion_section = ''
    if exclude_org_names:
        exclusion_section = (
            "\nAlready sourced for this project — do not return these orgs again:\n"
            + '\n'.join(f'- {name}' for name in exclude_org_names[:100])
        )

    prompt = (
        f"Propose up to {min(limit, 60)} US-based partner organizations or provider institutions for this clinical trial.\n\n"
        f"{indication_line}\n"
        f"{pop_line}\n"
        f"Therapeutic specialties (broad): {specialty_str}\n"
        f"Target org categories (ranked):\n{org_types_str}\n"
        f"Target contact roles: {roles_str}\n"
        f"Geography: {geo_str}{asset_section}{exclusion_section}\n\n"
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
    "You extract outreach contacts from one or more pages on an organization's "
    "website. The caller wants to reach a real person at this org about a "
    "clinical-trial partnership.\n\n"
    "Return EVERY named person mentioned on the pages, even if they appear only "
    "in a single prose sentence. Example: 'Please contact Abbey Hauser, Associate "
    "Director of Community Engagement, at ahauser@foundation.org' is a named "
    "contact with email, title, and context — extract it. Don't require a "
    "formal staff-directory layout. If someone is named, include them.\n\n"
    "Also ALWAYS return any generic org emails you spot (info@, contact@, "
    "admin@, press@, etc.) as fallback_emails. These are first-class results — "
    "an operator can email them to request redirect to the right person. "
    "Include at least one fallback email if any public email is visible on the "
    "page.\n\n"
    "Do NOT invent names or emails. If a page genuinely has no names and no "
    "emails (just a contact form), return empty arrays for both."
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
        'fallback_emails': {
            'type': 'array',
            'description': 'Generic org emails (info@, contact@, press@, etc.)',
            'items': {
                'type': 'object',
                'properties': {
                    'email': {'type': 'string'},
                    'context': {'type': 'string', 'description': 'What inbox it serves per the page'},
                },
                'required': ['email'],
            },
        },
    },
    'required': ['contacts', 'fallback_emails'],
}


_BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
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
}


def _candidate_contact_urls(seed_url: str) -> list[str]:
    """Given a seed URL, return the seed plus common contact/team page paths
    on the same domain. We use these when the seed alone doesn't yield contacts."""
    import requests  # noqa: F401 — ensures import at top-level if stripped
    from urllib.parse import urlparse

    if not seed_url or not seed_url.startswith(('http://', 'https://')):
        return [seed_url] if seed_url else []
    parsed = urlparse(seed_url)
    if not parsed.netloc:
        return [seed_url]
    base = f"{parsed.scheme}://{parsed.netloc}"
    common = [
        f"{base}/",
        f"{base}/contact",
        f"{base}/contact-us",
        f"{base}/about",
        f"{base}/about-us",
        f"{base}/team",
        f"{base}/our-team",
        f"{base}/staff",
        f"{base}/leadership",
        f"{base}/people",
    ]
    ordered = [seed_url] + [u for u in common if u != seed_url]
    # dedup preserving order
    seen: set[str] = set()
    return [u for u in ordered if not (u in seen or seen.add(u))]


def _fetch_page_text(url: str, timeout: int = 12) -> tuple[str, int | None]:
    """Return (text, status_code) for a URL, or ('', status_code) on failure."""
    import requests
    from django.utils.html import strip_tags

    try:
        r = requests.get(url, timeout=timeout, headers=_BROWSER_HEADERS, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        log.info('scrape %s: %s', url, exc)
        return '', None
    if r.status_code >= 400:
        return '', r.status_code
    raw_html = r.text[:600_000]
    text = strip_tags(raw_html)
    return ' '.join(text.split()), r.status_code


def extract_contacts_from_url(*, url: str, org_name: str, user=None) -> dict:
    """Fetch the seed URL and up to 4 fallback paths on the same domain,
    concatenate the text, ask Claude to extract named contacts + fallback emails.

    Returns {contacts: [...], fallback_emails: [...], fetched_urls: [...],
             failed_urls: [...]}.
    """
    seed_fetched, seed_status = _fetch_page_text(url)
    fetched: list[tuple[str, str]] = []  # (url, text)
    failed: list[tuple[str, int | None]] = []
    if seed_fetched.strip():
        fetched.append((url, seed_fetched))
    elif seed_status is not None:
        failed.append((url, seed_status))

    # If we didn't get enough from the seed, try fallback paths on the same domain.
    total_chars = sum(len(t) for _, t in fetched)
    if total_chars < 4_000:
        for candidate in _candidate_contact_urls(url)[1:5]:
            text, status = _fetch_page_text(candidate)
            if text.strip():
                fetched.append((candidate, text))
            elif status is not None:
                failed.append((candidate, status))
            if sum(len(t) for _, t in fetched) >= 60_000:
                break

    if not fetched:
        # All URLs failed. Bubble up a clear error.
        status_summary = ', '.join(f'{u}→{s}' for u, s in failed[:3]) or 'unreachable'
        raise RuntimeError(
            f'Could not load any page on the org domain ({status_summary}). '
            f'Edit the lead with a working URL, or try Web search.'
        )

    # Concatenate, cap at 80k chars (well under Claude's context).
    combined_sections = []
    remaining = 80_000
    for u, t in fetched:
        snippet = t[:remaining]
        combined_sections.append(f'[{u}]\n{snippet}')
        remaining -= len(snippet)
        if remaining <= 0:
            break
    combined = '\n\n---\n\n'.join(combined_sections)

    prompt = (
        f"Organization: {org_name}\n"
        f"Pages fetched (seed URL first, then common contact/team fallbacks on the same domain):\n\n"
        f"{combined}\n\n"
        f"Use return_contacts to return ALL named people found anywhere in this text "
        f"(including prose mentions like 'contact Abbey Hauser at ...') PLUS every "
        f"generic email (info@, contact@, press@) as fallback_emails."
    )
    result = AIService.call_structured(
        prompt=prompt,
        system_prompt=CONTACT_EXTRACT_SYSTEM_PROMPT,
        tool_name='return_contacts',
        tool_description='Return named contacts and fallback generic emails from the page text.',
        tool_schema=CONTACT_EXTRACT_TOOL_SCHEMA,
        function_name='extract_contacts_from_url',
        user=user,
        max_tokens=3000,
    )
    contacts_raw = result.get('contacts') if isinstance(result, dict) else []
    fallbacks_raw = result.get('fallback_emails') if isinstance(result, dict) else []

    contacts = []
    for c in contacts_raw or []:
        if not isinstance(c, dict) or not c.get('name'):
            continue
        name_parts = c.get('name', '').strip().split(' ', 1)
        contacts.append({
            'first_name': name_parts[0] if name_parts else '',
            'last_name': name_parts[1] if len(name_parts) > 1 else '',
            'role': (c.get('title') or '').strip(),
            'email': (c.get('email') or '').strip().lower() or None,
            'phone': (c.get('phone') or '').strip(),
            'notes': (c.get('notes') or '').strip(),
        })
    fallbacks = []
    for fb in fallbacks_raw or []:
        if not isinstance(fb, dict) or not fb.get('email'):
            continue
        fallbacks.append({
            'email': fb.get('email', '').strip().lower(),
            'context': (fb.get('context') or '').strip(),
        })

    log.info(
        'extract_contacts_from_url: fetched=%d urls, %d contacts, %d fallbacks',
        len(fetched), len(contacts), len(fallbacks),
    )
    return {
        'contacts': contacts,
        'fallback_emails': fallbacks,
        'fetched_urls': [u for u, _ in fetched],
        'failed_urls': [f'{u} ({s})' for u, s in failed],
    }


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
    "3. target_org_types — 3-6 categories of organizations to reach, ordered from "
    "most indication-specific to most general. Examples: \"foundations specific "
    "to [indication]\", \"NCI-designated comprehensive cancer centers\", "
    "\"academic medical centers with [specialty] programs\", \"community "
    "oncology networks\", \"patient registries for [indication]\", "
    "\"metabolic/genetic clinics\", \"pediatric genetics programs\", "
    "\"rare-disease centers of excellence\". Be "
    "indication-specific when possible.\n"
    "4. target_contact_roles — 3-6 specific titles of people to reach. Examples: "
    "\"Principal Investigator\", \"Clinical Research Coordinator\", "
    "\"Director of Clinical Research\", \"Patient Navigator\", \"Director of "
    "Patient Services\", \"Medical Director\". Pick roles that match the org "
    "types — clinical roles at medical centers, program roles at advocacy orgs.\n"
    "5. specialty_tags — 3-6 REAL CMS NPI Registry taxonomy descriptions (the NPI "
    "API will reject free-text topics like 'clinical research' or 'hematologic "
    "malignancies'). Valid examples: 'Medical Oncology', 'Radiation Oncology', "
    "'Hematology & Oncology', 'Cardiology', 'Family Medicine', 'Pediatrics', "
    "'Neurology', 'Dermatology', 'Endocrinology, Diabetes & Metabolism', "
    "'Gastroenterology', 'Nephrology', 'Pulmonary Disease', 'Rheumatology', "
    "'Psychiatry', 'Clinical Genetics', 'Clinical Biochemical Genetics', "
    "'Genetic Counselor'. For genetics/metabolic/rare-disease studies, include "
    "the relevant genetics and pediatric tags instead of falling back to only a "
    "single broad physician specialty.\n"
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
            'description': '3-6 org categories, most indication-specific first',
        },
        'target_contact_roles': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': '3-6 specific titles to reach at those orgs',
        },
        'specialty_tags': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': '3-6 real CMS NPI taxonomy descriptions',
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


# ──────────────────────── web-search-based contact finding ──────────────────

ORG_CONTACTS_WEB_SYSTEM_PROMPT = (
    "You research a single US organization and return real, verifiable staff "
    "contacts suitable for clinical-trial partnership outreach. Use web_search "
    "aggressively — you have 10 searches available and you should use most of "
    "them before giving up.\n\n"
    "Search strategy (run at least 4 of these before concluding no one is "
    "findable):\n"
    "  1. '{org_name} staff' or '{org_name} leadership'\n"
    "  2. '{org_name} executive director'\n"
    "  3. '{org_name} director of patient services'\n"
    "  4. '{org_name} board of directors'\n"
    "  5. '{org_name} medical director' OR '{org_name} clinical research'\n"
    "  6. '{org_name} about us' OR '{org_name} team'\n"
    "  7. '{org_name} press release' (to catch named people quoted in news)\n"
    "  8. 'site:linkedin.com/in {org_name}' — LinkedIn profiles are THE best "
    "signal for current titles and current employment. Always run at least one "
    "LinkedIn search. Capture the linkedin_url for every contact you find.\n\n"
    "For every named person you return, include a source_url that is a real, "
    "current page (not a 404). Prefer URLs on the org's own domain or a major "
    "verified directory. Never invent names or emails. If a name is on a press "
    "page but no email is listed, return the name + source_url with empty email.\n\n"
    "ALWAYS include at least one fallback generic email (info@, contact@, "
    "admin@, etc.) if the org has any public email at all. Fallback emails are "
    "first-class results — the outreach team uses them to request a redirect "
    "to the right person. Include a source_url for each fallback too.\n\n"
    "If after thorough searching you truly find no named contact, return empty "
    "contacts but populate fallback_emails with every generic email you spotted."
)

ORG_CONTACTS_WEB_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'primary_domain': {
            'type': 'string',
            'description': "The org's main web domain (e.g. 'everylifefoundation.org'). Used by downstream tools to find all emails at this domain.",
        },
        'contacts': {
            'type': 'array',
            'description': 'Named staff suitable for outreach',
            'items': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'title': {'type': 'string'},
                    'email': {'type': 'string', 'description': 'Email if publicly listed — otherwise empty'},
                    'linkedin_url': {'type': 'string', 'description': "LinkedIn profile URL if found (e.g. https://www.linkedin.com/in/...)"},
                    'source_url': {'type': 'string', 'description': "URL where the person's name/role was verified"},
                    'notes': {'type': 'string', 'description': 'One-line context'},
                },
                'required': ['name'],
            },
        },
        'fallback_emails': {
            'type': 'array',
            'description': 'Generic org emails (info@, contact@) as backup',
            'items': {
                'type': 'object',
                'properties': {
                    'email': {'type': 'string'},
                    'context': {'type': 'string', 'description': 'What this inbox handles per the site'},
                    'source_url': {'type': 'string'},
                },
                'required': ['email'],
            },
        },
    },
    'required': ['contacts', 'fallback_emails'],
}


def find_org_contacts_via_web(
    *,
    org_name: str,
    website_url: str = '',
    target_roles: list[str] | None = None,
    user=None,
) -> dict:
    """Search the web for named contacts at an org. Returns {contacts, fallback_emails}."""
    target_roles = target_roles or []
    roles_str = (
        ', '.join(target_roles)
        if target_roles
        else 'Director / VP of patient services or outreach, Executive Director, Director of Clinical Research, Patient Navigator, Chief Medical Officer'
    )
    site_hint = f' The org website is {website_url} — start there.' if website_url else ''

    prompt = (
        f"Organization: {org_name}.{site_hint}\n\n"
        f"Preferred contact roles: {roles_str}\n\n"
        f"Use web_search (up to 5 searches) to find the org's staff/leadership "
        f"or an authoritative page listing their people. Return results via "
        f"return_org_contacts — include fallback admin emails as backup."
    )
    result = AIService.call_structured_with_web_search(
        prompt=prompt,
        system_prompt=ORG_CONTACTS_WEB_SYSTEM_PROMPT,
        tool_name='return_org_contacts',
        tool_description='Return named staff contacts and fallback admin emails.',
        tool_schema=ORG_CONTACTS_WEB_TOOL_SCHEMA,
        function_name='find_org_contacts_via_web',
        user=user,
        max_tokens=4096,
        max_web_searches=10,
    )

    contacts = []
    for c in (result.get('contacts') or []):
        if not isinstance(c, dict) or not c.get('name'):
            continue
        name_parts = c.get('name', '').strip().split(' ', 1)
        contacts.append({
            'first_name': name_parts[0] if name_parts else '',
            'last_name': name_parts[1] if len(name_parts) > 1 else '',
            'role': (c.get('title') or '').strip(),
            'email': (c.get('email') or '').strip().lower() or None,
            'linkedin_url': (c.get('linkedin_url') or '').strip(),
            'source_url': (c.get('source_url') or '').strip(),
            'notes': (c.get('notes') or '').strip(),
        })

    fallbacks = []
    for fb in (result.get('fallback_emails') or []):
        if not isinstance(fb, dict) or not fb.get('email'):
            continue
        fallbacks.append({
            'email': fb.get('email', '').strip().lower(),
            'context': (fb.get('context') or '').strip(),
            'source_url': (fb.get('source_url') or '').strip(),
        })

    primary_domain = (result.get('primary_domain') or '').strip().lower()
    if primary_domain.startswith('http'):
        from urllib.parse import urlparse
        primary_domain = urlparse(primary_domain).netloc or primary_domain
    primary_domain = primary_domain.lstrip('www.').rstrip('/')

    log.info(
        'find_org_contacts_via_web: org=%r → %d contacts, %d fallback emails, domain=%r',
        org_name, len(contacts), len(fallbacks), primary_domain,
    )
    return {'contacts': contacts, 'fallback_emails': fallbacks, 'primary_domain': primary_domain}


CLINICIAN_EMAIL_WEB_SYSTEM_PROMPT = (
    "You research a specific US-licensed clinician to find their professional "
    "work email AND — equally importantly — their current institution's "
    "primary domain (e.g. 'mountsinai.org', 'mskcc.org'). The institution "
    "domain is almost as valuable as the email because it unlocks downstream "
    "tooling (Apollo domain match, Hunter.io's find-all-emails-at-domain).\n\n"
    "Priority order of searches (use up to 10):\n"
    "  1. Google the name + specialty + city/state — top result is usually the "
    "institution profile page.\n"
    "  2. site:linkedin.com/in {name} {city} — confirms current employer.\n"
    "  3. The institution's own faculty / 'find a doctor' page to capture the "
    "canonical affiliation + domain.\n"
    "  4. AMA / Doximity / US News doctor profile as fallback sources.\n\n"
    "Do NOT guess emails from patterns (firstname.lastname@domain). Only return "
    "an email if it is explicitly posted on the institution website or a "
    "reputable directory. Empty email is better than wrong email.\n\n"
    "ALWAYS return organization_domain when you can identify the current "
    "employer's website — even if no email is posted, the domain unlocks other "
    "tools. ALWAYS return linkedin_url when you find a matching profile."
)

CLINICIAN_EMAIL_WEB_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'email': {'type': 'string', 'description': 'Verified work email or empty'},
        'affiliation': {'type': 'string', 'description': 'Hospital/practice/institution name'},
        'organization_domain': {
            'type': 'string',
            'description': "The institution's primary web domain (e.g. 'mountsinai.org'). CRITICAL output — feeds downstream tools.",
        },
        'role': {'type': 'string', 'description': 'Title at that affiliation'},
        'linkedin_url': {'type': 'string', 'description': 'LinkedIn profile URL if found'},
        'source_url': {'type': 'string', 'description': 'Where you found this info'},
        'confidence': {'type': 'string', 'enum': ['high', 'medium', 'low', 'not_found']},
        'notes': {'type': 'string'},
    },
    'required': ['confidence'],
}


def find_clinician_email_via_web(
    *,
    first_name: str,
    last_name: str,
    specialty: str = '',
    city: str = '',
    state: str = '',
    postal_code: str = '',
    npi: str = '',
    user=None,
) -> dict:
    location_bits = [x for x in [city, state, postal_code] if x]
    location = ', '.join(location_bits) or '(location unknown)'
    prompt = (
        f"Find the work email AND current institution domain for this US-licensed clinician:\n"
        f"Name: {first_name} {last_name}\n"
        f"Specialty: {specialty or '(unknown)'}\n"
        f"Location: {location}\n"
        f"NPI: {npi or '(not provided)'}\n\n"
        f"Use web_search aggressively. Return the institution's primary domain "
        f"(e.g. 'mountsinai.org') even if you can't find a publicly posted email — "
        f"the domain alone is high-value for downstream enrichment."
    )
    result = AIService.call_structured_with_web_search(
        prompt=prompt,
        system_prompt=CLINICIAN_EMAIL_WEB_SYSTEM_PROMPT,
        tool_name='return_clinician_email',
        tool_description='Return the clinician email, affiliation, institution domain, and source URL.',
        tool_schema=CLINICIAN_EMAIL_WEB_TOOL_SCHEMA,
        function_name='find_clinician_email_via_web',
        user=user,
        max_tokens=2048,
        max_web_searches=10,
    )
    return {
        'email': (result.get('email') or '').strip().lower(),
        'affiliation': (result.get('affiliation') or '').strip(),
        'organization_domain': (result.get('organization_domain') or '').strip().lower().lstrip('https://').lstrip('http://').rstrip('/'),
        'role': (result.get('role') or '').strip(),
        'linkedin_url': (result.get('linkedin_url') or '').strip(),
        'source_url': (result.get('source_url') or '').strip(),
        'confidence': (result.get('confidence') or 'not_found').strip(),
        'notes': (result.get('notes') or '').strip(),
    }


# ──────────────────────── email-sequence drafting ───────────────────────────

EMAIL_SEQUENCE_SYSTEM_PROMPT = (
    "You draft a short, professional email sequence for a clinical-trial outreach "
    "campaign. You write as the CliniContact team reaching out to clinicians, "
    "research coordinators, or patient-advocacy organizations inviting them to "
    "refer eligible patients to a study.\n\n"
    "Ground rules (non-negotiable):\n"
    "- Base every sentence on the approved study materials provided. Never "
    "fabricate clinical claims, inclusion criteria, phase numbers, efficacy, or "
    "safety statistics that are not in the source.\n"
    "- Tone: respectful of the recipient's time, plain-spoken, no sales hype, no "
    "exclamation points, no fake urgency.\n"
    "- The initial outreach goal is to confirm whether the recipient is open to "
    "collaborating and referring potential participants — NOT to send people "
    "straight to the participant landing page.\n"
    "- Always include a clear, specific CTA. Preferred CTAs: offer to send the "
    "flyer, offer to connect them with the study team, or ask whether they would "
    "be open to learning more about referring eligible participants.\n"
    "- Do NOT direct the recipient to the participant referral landing page or "
    "screener form in the initial outreach. Only mention that those materials "
    "can be shared after they confirm interest.\n"
    "- You may mention {{landing_page_url}} only as a later-stage placeholder for "
    "when an interested collaborator asks for the referral page; do not make it "
    "the main CTA in these first-touch drafts.\n"
    "- Personalization must match the recipient type. For named clinicians or professors, prefer {{formal_salutation}}. "
    "For named non-physician contacts, use {{greeting_name}}. These greeting variables already include words like "
    "'Dear', 'Hi', or 'Hello', so use them as the complete first line followed by a comma. For generic or "
    "organization-level inboxes, address the organization or team and do not pretend you know an individual.\n"
    "- You MAY use {{formal_salutation}}, {{greeting_name}}, or {{organization_name}} once near the opening; avoid "
    "stuffing multiple merge vars throughout the email.\n"
    "- Write each body as 3-5 short paragraphs separated by blank lines. Do not "
    "return a single dense paragraph.\n"
    "- Do not include a full signature block or 'The CliniContact team'. If a "
    "closing is needed, end with one simple line like 'Best,' only. Bridge will "
    "append the sender signature when it sends to Instantly.\n\n"
    "Return a 3-step sequence via the return_email_sequence tool:\n"
    "Step 1 (delay 0): cold intro. Subject ≤ 60 chars. Body 120-180 words. "
    "Name the indication, describe who the study is for, and ask whether they "
    "would be open to discussing collaboration or receiving the flyer.\n"
    "Step 2 (delay 4 days): brief follow-up. Subject short, references step 1. "
    "Body ≤ 100 words. No new clinical info — a respectful nudge that offers to "
    "send materials or connect them with the study team.\n"
    "Step 3 (delay 8 days): value-add close. Subject distinct. Body 100-140 "
    "words. Re-state the patient benefit in one line, soft CTA, and note that if "
    "they are interested in referring patients the team can send the referral "
    "landing page and screener form."
)

EMAIL_SEQUENCE_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'steps': {
            'type': 'array',
            'description': 'Ordered 3-step sequence',
            'items': {
                'type': 'object',
                'properties': {
                    'step_num': {'type': 'integer', 'description': '1, 2, or 3'},
                    'delay_days': {'type': 'integer', 'description': 'Days after previous step (step 1 = 0)'},
                    'subject': {'type': 'string'},
                    'body': {'type': 'string', 'description': 'Plaintext body with optional {{formal_salutation}}, {{greeting_name}}, {{organization_name}}, and later-stage {{landing_page_url}} placeholder'},
                    'rationale': {'type': 'string', 'description': 'One short sentence on what this step does'},
                },
                'required': ['step_num', 'subject', 'body', 'delay_days'],
            },
        },
    },
    'required': ['steps'],
}


def draft_email_sequence(
    *,
    project_name: str,
    study_code: str,
    asset_texts: list[str],
    profile,
    landing_page_url: str = '',
    user=None,
) -> list[dict]:
    """Ask Claude to draft a 3-step outreach sequence grounded in the study assets."""
    profile_ctx = (
        f"Study indication: {profile.study_indication or '(not set)'}\n"
        f"Patient population: {profile.patient_population_description or '(not set)'}\n"
        f"Partner type receiving these emails: {profile.get_partner_type_display()}\n"
        f"Target contact roles: {', '.join(profile.target_contact_roles or []) or '(not set)'}\n"
        f"Available merge vars: {{{{formal_salutation}}}} (complete greeting such as Dear Dr. Smith or Hi Jane), "
        f"{{{{greeting_name}}}} (complete friendly greeting), {{{{organization_name}}}}, {{{{landing_page_url}}}}"
    )
    joined_assets = '\n\n---\n\n'.join(t.strip() for t in asset_texts if t and t.strip()) or '(no assets uploaded)'
    landing_line = (
        f"Referral landing page URL available for later-stage follow-up ({{{{landing_page_url}}}}): {landing_page_url}"
        if landing_page_url
        else "Referral landing page URL not configured. Do not rely on {{landing_page_url}} in the initial outreach."
    )

    prompt = (
        f"Project: {project_name}\n"
        f"Study code: {study_code}\n\n"
        f"Targeting context:\n{profile_ctx}\n\n"
        f"{landing_line}\n"
        f"Important personalization rule: use {{{{formal_salutation}}}}, as its own first line followed by a comma, "
        f"for named clinicians/professors; use {{{{greeting_name}}}}, as its own first line followed by a comma, "
        f"for named non-physician contacts; use organization/team wording for generic inboxes or organization-only records.\n"
        f"Important: the first-touch emails should ask whether the recipient is interested in collaborating, receiving the flyer, or speaking with the study team. "
        f"Only mention sending the referral landing page / screener after they confirm interest.\n\n"
        f"Approved study materials (your only source of clinical claims):\n---\n{joined_assets[:8000]}\n---\n\n"
        f"Draft the 3-step sequence via return_email_sequence."
    )
    result = AIService.call_structured(
        prompt=prompt,
        system_prompt=EMAIL_SEQUENCE_SYSTEM_PROMPT,
        tool_name='return_email_sequence',
        tool_description='Return a 3-step outreach email sequence drafted from the study materials.',
        tool_schema=EMAIL_SEQUENCE_TOOL_SCHEMA,
        function_name='draft_email_sequence',
        user=user,
        max_tokens=3000,
    )
    normalized = _normalize_email_steps(result)
    if not _sequence_is_complete(normalized):
        repair_prompt = (
            f"{prompt}\n\n"
            "The prior draft came back incomplete. Repair it so that all 3 steps have a non-empty subject and a non-empty body.\n"
            f"Prior incomplete tool output:\n{json.dumps(result, indent=2)}\n\n"
            "Return the corrected 3-step sequence via return_email_sequence. Do not leave any subject or body blank."
        )
        repaired = AIService.call_structured(
            prompt=repair_prompt,
            system_prompt=EMAIL_SEQUENCE_SYSTEM_PROMPT,
            tool_name='return_email_sequence',
            tool_description='Return a corrected 3-step outreach email sequence drafted from the study materials.',
            tool_schema=EMAIL_SEQUENCE_TOOL_SCHEMA,
            function_name='draft_email_sequence_repair',
            user=user,
            max_tokens=3200,
        )
        normalized = _normalize_email_steps(repaired)

    if not _sequence_is_complete(normalized):
        log.warning('AI returned incomplete email sequence for %s; using fallback draft', study_code)
        normalized = _fallback_email_sequence(
            study_code=study_code,
            study_indication=profile.study_indication or '',
            patient_population_description=profile.patient_population_description or '',
        )
    log.info('Drafted email sequence: %d steps for %s', len(normalized), study_code)
    return normalized


def _normalize_email_steps(result: dict | None) -> list[dict]:
    steps = result.get('steps') if isinstance(result, dict) else []
    normalized = []
    for i, s in enumerate(steps or []):
        if not isinstance(s, dict):
            continue
        normalized.append({
            'step_num': int(s.get('step_num') or (i + 1)),
            'delay_days': int(s.get('delay_days') or (0 if i == 0 else 4 * i)),
            'subject': (s.get('subject') or '').strip(),
            'body': (s.get('body') or '').strip(),
            'rationale': (s.get('rationale') or '').strip(),
            'approved': False,
        })
    return normalized


def _sequence_is_complete(steps: list[dict]) -> bool:
    if len(steps) < 3:
        return False
    required_steps = {1, 2, 3}
    seen_steps = {int(step.get('step_num') or 0) for step in steps}
    if not required_steps.issubset(seen_steps):
        return False
    return all((step.get('subject') or '').strip() and (step.get('body') or '').strip() for step in steps[:3])


def _fallback_email_sequence(*, study_code: str, study_indication: str, patient_population_description: str) -> list[dict]:
    indication = study_indication.strip() or 'the study indication'
    population = patient_population_description.strip() or (
        'We are reaching out to identify providers and organizations who may work with patients that could be relevant for this study.'
    )
    return [
        {
            'step_num': 1,
            'delay_days': 0,
            'subject': f'Collaboration on {study_code}',
            'body': (
                "{{formal_salutation}},\n\n"
                f"I am reaching out from CliniContact regarding {study_code}, a study focused on {indication}. "
                f"{population} We are looking to connect with clinicians and organizations who may be open to learning more about the study and, if appropriate, referring potentially eligible participants.\n\n"
                "If this is relevant to your practice or organization, I would be happy to send the study flyer or connect you with the study team for a brief discussion.\n\n"
                "Best,"
            ),
            'rationale': 'Cold introduction focused on collaboration and referral interest.',
            'approved': False,
        },
        {
            'step_num': 2,
            'delay_days': 4,
            'subject': f'Following up on {study_code}',
            'body': (
                "{{formal_salutation}},\n\n"
                f"I wanted to follow up on my note about {study_code}. If it would be helpful, I can send the study flyer or connect you with the study team so you can quickly assess whether this could be relevant for the patients you support.\n\n"
                "Please let me know if you would like me to share those materials.\n\n"
                "Best,"
            ),
            'rationale': 'Brief follow-up that offers materials without adding new claims.',
            'approved': False,
        },
        {
            'step_num': 3,
            'delay_days': 8,
            'subject': f'Last follow-up on {study_code}',
            'body': (
                "{{formal_salutation}},\n\n"
                f"I am sending one last follow-up regarding {study_code}, which is focused on {indication}. If you think this may be relevant for your patients or community, I would be glad to send the flyer or connect you with the study team. "
                "If there is someone else on your team who would be better for this conversation, I would also appreciate being pointed in the right direction.\n\n"
                "Best,"
            ),
            'rationale': 'Soft close that still leaves a clear next step.',
            'approved': False,
        },
    ]


# ──────────────────────── helpers ───────────────────────────────────────────

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
