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
    """Search the CMS NPI Registry. Returns normalized lead dicts."""
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

    r = requests.get(NPI_API_URL, params=params, timeout=20)
    r.raise_for_status()
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
