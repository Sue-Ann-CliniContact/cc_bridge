"""CMS NPI Registry client — free public API, no key required.

Docs: https://npiregistry.cms.hhs.gov/api-page

Taxonomy is free-text; the API matches partial strings against
taxonomy.description (e.g. "oncology" matches "Medical Oncology",
"Hematology/Oncology", etc). Limit is capped at 200 per call.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

NPI_API_URL = 'https://npiregistry.cms.hhs.gov/api/'
API_VERSION = '2.1'


class NPITaxonomyNotFound(ValueError):
    """CMS reports the taxonomy_description doesn't match any known taxonomy code.
    Common cause: specialty tag is free-text (e.g. 'clinical research') rather than
    an actual CMS taxonomy description. Service layer catches this and continues."""


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
        # CMS NPI does exact substring match only when wildcards are present — wrap
        # short terms so e.g. "oncology" matches "Medical Oncology", "Hematology/Oncology", etc.
        term = taxonomy.strip()
        if '*' not in term:
            term = f'*{term}*'
        params['taxonomy_description'] = term
    if state:
        params['state'] = state
    if postal_code:
        params['postal_code'] = postal_code
    if first_name:
        params['first_name'] = first_name
    if last_name:
        params['last_name'] = last_name

    log.info('NPI search params: %s', {k: v for k, v in params.items() if k != 'version'})
    r = requests.get(NPI_API_URL, params=params, timeout=20)
    if r.status_code >= 400:
        try:
            errors = r.json().get('Errors') or []
            messages = '; '.join(e.get('description', str(e)) for e in errors) if errors else r.text[:300]
        except Exception:  # noqa: BLE001
            messages = r.text[:300]
        log.warning('NPI %s error: %s', r.status_code, messages)
        raise RuntimeError(f'NPI API {r.status_code}: {messages}')
    data = r.json() or {}
    # CMS sometimes returns 200 with an Errors field for malformed queries
    api_errors = data.get('Errors') or []
    if api_errors:
        messages = '; '.join(e.get('description', str(e)) for e in api_errors)
        log.warning('NPI 200-with-Errors: %s', messages)
        if 'No taxonomy codes found' in messages:
            raise NPITaxonomyNotFound(messages)
        raise RuntimeError(f'NPI API: {messages}')
    results = data.get('results') or []
    log.info('NPI returned %d results (result_count=%s)', len(results), data.get('result_count'))
    return [_normalize(item) for item in results]


def search_multi(
    taxonomies: list[str],
    *,
    state: str | None = None,
    postal_code: str | None = None,
    limit: int = 200,
) -> tuple[list[dict], list[str]]:
    """Iterate through each taxonomy term, aggregate matching candidates (deduped by NPI),
    and report which terms CMS didn't recognize. Lets one bad specialty_tag not kill the whole run.
    """
    seen_npis: set[str] = set()
    aggregated: list[dict] = []
    unrecognized: list[str] = []

    for taxo in taxonomies:
        if not taxo or not taxo.strip():
            continue
        try:
            batch = search(taxonomy=taxo.strip(), state=state, postal_code=postal_code, limit=limit)
        except NPITaxonomyNotFound:
            unrecognized.append(taxo.strip())
            continue
        for row in batch:
            npi_val = row.get('npi') or ''
            if npi_val in seen_npis:
                continue
            seen_npis.add(npi_val)
            aggregated.append(row)
    return aggregated, unrecognized


def _normalize(item: dict) -> dict:
    """Map a raw NPI result to the shape expected by the sourcing pipeline.

    We pull everything useful CMS returns (street, fax, gender, license state,
    enumeration dates, all taxonomies) into the geography JSON so future
    location-specific projects can match without re-querying NPI.
    """
    npi = str(item.get('number', '')) or ''
    basic = item.get('basic') or {}
    addresses = item.get('addresses') or []
    practice = next((a for a in addresses if a.get('address_purpose') == 'LOCATION'), None)
    mailing = next((a for a in addresses if a.get('address_purpose') == 'MAILING'), None)
    practice = practice or mailing or (addresses[0] if addresses else {})

    taxonomies = item.get('taxonomies') or []
    primary_tax = next((t for t in taxonomies if t.get('primary')), taxonomies[0] if taxonomies else {})

    first = basic.get('first_name') or ''
    last = basic.get('last_name') or ''
    org = basic.get('organization_name') or ''
    taxonomy_code = (primary_tax.get('code') or '').strip()

    return {
        'npi': npi,
        'first_name': first.title() if first else '',
        'last_name': last.title() if last else '',
        'organization': org,
        'role': (basic.get('credential') or '').strip(),
        'specialty': (primary_tax.get('desc') or '').strip(),
        'taxonomy_code': taxonomy_code,
        'geography': {
            # Practice address (primary — where the provider sees patients)
            'street': (practice.get('address_1') or '').strip(),
            'address_2': (practice.get('address_2') or '').strip(),
            'city': (practice.get('city') or '').strip().title(),
            'state': (practice.get('state') or '').strip(),
            'postal_code': (practice.get('postal_code') or '').strip()[:5],
            'country': (practice.get('country_code') or '').strip(),
            # Contact info (different from the main Lead.phone so we preserve both)
            'practice_telephone': (practice.get('telephone_number') or '').strip(),
            'fax': (practice.get('fax_number') or '').strip(),
            # Provider metadata
            'gender': (basic.get('gender') or '').strip(),
            'enumeration_date': (basic.get('enumeration_date') or '').strip(),
            'last_updated': (basic.get('last_updated') or '').strip(),
            'status': (basic.get('status') or '').strip(),
            # Taxonomy (primary specialty + license)
            'taxonomy_code': taxonomy_code,
            'taxonomy_license_state': (primary_tax.get('state') or '').strip(),
            'taxonomy_license': (primary_tax.get('license') or '').strip(),
            'all_taxonomies': [
                {
                    'code': t.get('code'),
                    'desc': t.get('desc'),
                    'state': t.get('state'),
                    'primary': t.get('primary'),
                }
                for t in taxonomies
            ],
        },
        'phone': (practice.get('telephone_number') or '').strip(),
        # NPI registry doesn't include email
        'email': None,
    }


# CMS taxonomy codes starting with 207 or 208 are Allopathic & Osteopathic Physicians
# (MDs and DOs across all specialties). Everything else is NPs, PAs, nurses,
# social workers, therapists, etc.
PHYSICIAN_TAXONOMY_PREFIXES = ('207', '208')


def is_physician(candidate: dict) -> bool:
    code = (candidate.get('taxonomy_code') or '').strip()
    return any(code.startswith(p) for p in PHYSICIAN_TAXONOMY_PREFIXES)
