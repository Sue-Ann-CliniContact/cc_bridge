"""Apollo.io API client — enrichment (people/match) + discovery (mixed_people/search).

Budget-guarded: every credit-spending call is recorded in ApolloCreditLog so we
can tally monthly usage against APOLLO_MONTHLY_BUDGET_CREDITS. A call that
would push the current month over budget is refused *before* it hits Apollo.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import requests
from django.conf import settings
from django.db.models import Sum

APOLLO_BASE = 'https://api.apollo.io/v1'


def is_configured() -> bool:
    return bool(settings.APOLLO_API_KEY)


def _headers() -> dict:
    return {
        'Cache-Control': 'no-cache',
        'Content-Type': 'application/json',
        'X-Api-Key': settings.APOLLO_API_KEY,
    }


def current_month_credits() -> int:
    """Sum of credits spent this calendar month."""
    from core.models import ApolloCreditLog  # local to avoid app-loading order

    today = date.today()
    start = today.replace(day=1)
    agg = ApolloCreditLog.objects.filter(created_at__date__gte=start).aggregate(total=Sum('credits'))
    return int(agg.get('total') or 0)


def budget_remaining() -> int:
    return max(0, settings.APOLLO_MONTHLY_BUDGET_CREDITS - current_month_credits())


def _log_credits(endpoint: str, credits: int, user=None, notes: str = '') -> None:
    from core.models import ApolloCreditLog

    ApolloCreditLog.objects.create(
        user=user,
        endpoint=endpoint,
        credits=credits,
        notes=notes,
    )


def _extract_person_summary(person: dict) -> dict:
    """Pull the fields we want to flow back to the Lead + keep everything else raw."""
    org = person.get('organization') or {}
    phones = person.get('phone_numbers') or []
    phone = ''
    for p in phones:
        phone = p.get('sanitized_number') or p.get('raw_number') or ''
        if phone:
            break
    return {
        'email': person.get('email') or '',
        'email_status': person.get('email_status') or '',
        'phone': phone,
        'all_phones': [p.get('sanitized_number') or p.get('raw_number') or '' for p in phones],
        'linkedin_url': person.get('linkedin_url') or '',
        'twitter_url': person.get('twitter_url') or '',
        'github_url': person.get('github_url') or '',
        'facebook_url': person.get('facebook_url') or '',
        'photo_url': person.get('photo_url') or '',
        'title': person.get('title') or '',
        'headline': person.get('headline') or '',
        'seniority': person.get('seniority') or '',
        'departments': person.get('departments') or [],
        'functions': person.get('functions') or [],
        'city': person.get('city') or '',
        'state': person.get('state') or '',
        'country': person.get('country') or '',
        'organization': {
            'name': org.get('name') or '',
            'website_url': org.get('website_url') or '',
            'primary_domain': org.get('primary_domain') or '',
            'industry': org.get('industry') or '',
            'estimated_num_employees': org.get('estimated_num_employees'),
            'linkedin_url': org.get('linkedin_url') or '',
        },
        'employment_history': [
            {
                'organization_name': h.get('organization_name'),
                'title': h.get('title'),
                'start_date': h.get('start_date'),
                'end_date': h.get('end_date'),
                'current': h.get('current'),
            }
            for h in (person.get('employment_history') or [])[:5]
        ],
        'raw': person,
    }


def enrich_person(
    *,
    first_name: str = '',
    last_name: str = '',
    organization: str = '',
    domain: str = '',
    linkedin_url: str = '',
    title: str = '',
    city: str = '',
    state: str = '',
    user=None,
) -> dict[str, Any]:
    """Resolve a person via Apollo's people/match (1 credit).

    Sends every disambiguating hint we have — Apollo's matcher uses name +
    org + domain + linkedin_url + title as soft matching signals. City/state
    aren't native match params on this endpoint but they narrow the search
    in the fallback path (mixed_people/search).
    """
    if not is_configured():
        raise RuntimeError('Apollo API key not configured')
    if budget_remaining() < 1:
        raise RuntimeError('Apollo monthly budget exhausted')

    if not any([first_name, last_name, organization, domain, linkedin_url]):
        raise RuntimeError(
            'Apollo people/match needs at least a name, organization, domain, or '
            'LinkedIn URL — this lead has none of those.'
        )

    payload = {
        'first_name': first_name or None,
        'last_name': last_name or None,
        'organization_name': organization or None,
        'domain': domain or None,
        'linkedin_url': linkedin_url or None,
        'title': title or None,
        'reveal_personal_emails': True,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    r = requests.post(f'{APOLLO_BASE}/people/match', json=payload, headers=_headers(), timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(
            f'Apollo {r.status_code}: {r.text[:300]}. Sent: {payload}'
        )
    data = r.json() or {}
    _log_credits('people/match', 1, user=user, notes=f'{first_name} {last_name} @ {organization or city}'.strip())
    person = data.get('person') or {}

    if not person:
        # Fallback: Apollo sometimes returns 200 with no person. Try mixed_people/search
        # with location + title to disambiguate clinicians-without-an-org.
        if first_name and last_name and (city or state or title or organization):
            fallback = _search_best_match(
                first_name=first_name,
                last_name=last_name,
                city=city,
                state=state,
                title=title,
                user=user,
            )
            if fallback:
                return _extract_person_summary(fallback)
        raise RuntimeError(
            f'Apollo: no match for {first_name!r} {last_name!r} @ '
            f'{organization or city or "(no org/location hint)"}. Edit the lead '
            f'to add an org, city, or LinkedIn URL and retry.'
        )

    return _extract_person_summary(person)


def _search_best_match(
    *,
    first_name: str,
    last_name: str,
    city: str = '',
    state: str = '',
    title: str = '',
    user=None,
) -> dict | None:
    """mixed_people/search fallback — uses location + title to disambiguate.
    Returns the single best-looking person object or None."""
    if budget_remaining() < 1:
        return None

    location = ', '.join(x for x in [city, state] if x)
    payload: dict[str, Any] = {
        'q_person_name': f'{first_name} {last_name}'.strip(),
        'per_page': 5,
    }
    if title:
        payload['person_titles'] = [title]
    if location:
        payload['person_locations'] = [location]

    r = requests.post(f'{APOLLO_BASE}/mixed_people/search', json=payload, headers=_headers(), timeout=25)
    if r.status_code >= 400:
        log.warning('Apollo search fallback failed %s: %s', r.status_code, r.text[:200])
        return None
    data = r.json() or {}
    people = data.get('people') or []
    if not people:
        return None
    _log_credits('mixed_people/search', min(len(people), 5), user=user, notes=f'{first_name} {last_name} @ {location}')

    # Best match: first result whose last_name matches (case-insensitive) and
    # whose title/location has some overlap. If nothing specific, take the first.
    def score(p: dict) -> int:
        s = 0
        if (p.get('last_name') or '').lower() == last_name.lower():
            s += 3
        if title and title.lower() in (p.get('title') or '').lower():
            s += 2
        if state and state.upper() == (p.get('state') or '').upper():
            s += 1
        if city and city.lower() == (p.get('city') or '').lower():
            s += 1
        return s

    ranked = sorted(people, key=score, reverse=True)
    return ranked[0]


def discover_people(
    *,
    titles: list[str] | None = None,
    person_specialties: list[str] | None = None,
    person_locations: list[str] | None = None,
    limit: int = 25,
    user=None,
) -> list[dict[str, Any]]:
    """Search Apollo for new people matching criteria (1 credit per result)."""
    if not is_configured():
        raise RuntimeError('Apollo API key not configured')
    estimated_cost = min(limit, 100)
    if budget_remaining() < estimated_cost:
        raise RuntimeError(f'Apollo discovery would cost ~{estimated_cost} credits; only {budget_remaining()} remaining')

    payload: dict[str, Any] = {'per_page': min(limit, 100)}
    if titles:
        payload['person_titles'] = titles
    if person_specialties:
        payload['person_seniorities'] = person_specialties
    if person_locations:
        payload['person_locations'] = person_locations

    r = requests.post(f'{APOLLO_BASE}/mixed_people/search', json=payload, headers=_headers(), timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    people = data.get('people') or []
    _log_credits('mixed_people/search', len(people), user=user, notes=f'titles={titles} locations={person_locations}')

    return [
        {
            'first_name': p.get('first_name') or '',
            'last_name': p.get('last_name') or '',
            'email': p.get('email') or '',
            'organization': (p.get('organization') or {}).get('name') or '',
            'role': p.get('title') or '',
            'linkedin_url': p.get('linkedin_url') or '',
            'raw': p,
        }
        for p in people
    ]
