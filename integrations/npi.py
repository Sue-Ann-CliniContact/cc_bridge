"""CMS NPI Registry client — free public API, no key required.

Docs: https://npiregistry.cms.hhs.gov/api-page

Taxonomy is free-text; the API matches partial strings against
taxonomy.description (e.g. "oncology" matches "Medical Oncology",
"Hematology/Oncology", etc). Limit is capped at 200 per call.
"""
from __future__ import annotations

import requests

NPI_API_URL = 'https://npiregistry.cms.hhs.gov/api/'
API_VERSION = '2.1'


def search(
    *,
    taxonomy: str | None = None,
    state: str | None = None,
    postal_code: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    limit: int = 200,
    enumeration_type: str = 'NPI-1',  # NPI-1 = individual, NPI-2 = organization
) -> list[dict]:
    """Search the CMS NPI Registry. Returns normalized lead dicts.

    The CMS API requires at least one concrete filter beyond enumeration_type,
    and it rejects the combination of only a short/common taxonomy string. If
    the partner profile is "national / specialty only," we need to make the
    query specific enough to be accepted — we add a wildcard last_name so the
    API is happy even with broad specialty targeting.
    """
    if not any([taxonomy, state, postal_code, first_name, last_name]):
        raise ValueError(
            'NPI search requires at least a specialty OR a state OR a postal code. '
            'Add one to the partner profile.'
        )

    params = {
        'version': API_VERSION,
        'limit': min(limit, 200),
        'enumeration_type': enumeration_type,
    }
    if taxonomy:
        params['taxonomy_description'] = taxonomy
    if state:
        params['state'] = state
    if postal_code:
        params['postal_code'] = postal_code
    if first_name:
        params['first_name'] = first_name
    if last_name:
        params['last_name'] = last_name

    # National + specialty-only: CMS sometimes 400s. Add a permissive last_name
    # wildcard so the filter set is accepted without narrowing the result set.
    if taxonomy and not (state or postal_code or first_name or last_name):
        params['last_name'] = '*'
        params['use_first_name_alias'] = 'true'

    r = requests.get(NPI_API_URL, params=params, timeout=20)
    if r.status_code >= 400:
        try:
            errors = r.json().get('Errors') or []
            messages = '; '.join(e.get('description', str(e)) for e in errors) if errors else r.text[:300]
        except Exception:  # noqa: BLE001
            messages = r.text[:300]
        raise RuntimeError(f'NPI API {r.status_code}: {messages}')
    data = r.json() or {}
    results = data.get('results') or []
    return [_normalize(item) for item in results]


def _normalize(item: dict) -> dict:
    """Map a raw NPI result to the shape expected by the sourcing pipeline."""
    npi = str(item.get('number', '')) or ''
    basic = item.get('basic') or {}
    addresses = item.get('addresses') or []
    practice = next((a for a in addresses if a.get('address_purpose') == 'LOCATION'), addresses[0] if addresses else {})
    taxonomies = item.get('taxonomies') or []
    primary_tax = next((t for t in taxonomies if t.get('primary')), taxonomies[0] if taxonomies else {})

    first = basic.get('first_name') or ''
    last = basic.get('last_name') or ''
    org = basic.get('organization_name') or ''

    return {
        'npi': npi,
        'first_name': first.title() if first else '',
        'last_name': last.title() if last else '',
        'organization': org,
        'role': (basic.get('credential') or '').strip(),
        'specialty': (primary_tax.get('desc') or '').strip(),
        'geography': {
            'state': (practice.get('state') or '').strip(),
            'postal_code': (practice.get('postal_code') or '').strip()[:5],
            'city': (practice.get('city') or '').strip().title(),
        },
        'phone': (practice.get('telephone_number') or '').strip(),
        # NPI registry doesn't include email
        'email': None,
    }
