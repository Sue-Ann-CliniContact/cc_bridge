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


def enrich_person(
    *,
    first_name: str = '',
    last_name: str = '',
    organization: str = '',
    domain: str = '',
    user=None,
) -> dict[str, Any]:
    """Resolve name + org to a verified email via people/match (1 credit)."""
    if not is_configured():
        raise RuntimeError('Apollo API key not configured')
    if budget_remaining() < 1:
        raise RuntimeError('Apollo monthly budget exhausted')

    payload = {
        'first_name': first_name,
        'last_name': last_name,
        'organization_name': organization,
        'domain': domain,
        # Unlocks the email on match (costs credits but is the whole point).
        'reveal_personal_emails': True,
    }
    if not any([first_name, last_name, organization, domain]):
        raise RuntimeError(
            'Apollo people/match needs at least a name or organization — '
            'this lead has neither.'
        )

    r = requests.post(f'{APOLLO_BASE}/people/match', json=payload, headers=_headers(), timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(
            f'Apollo {r.status_code}: {r.text[:300]}. Sent: first={first_name!r} '
            f'last={last_name!r} org={organization!r} domain={domain!r}.'
        )
    data = r.json() or {}
    _log_credits('people/match', 1, user=user, notes=f'{first_name} {last_name} @ {organization}'.strip())
    person = data.get('person') or {}
    if not person:
        # Apollo sometimes returns 200 with no person — surface that to the operator.
        raise RuntimeError(
            f'Apollo: no match for {first_name!r} {last_name!r} @ {organization!r}. '
            f'Try adding the organization name, or edit the lead manually.'
        )
    return {
        'email': person.get('email') or '',
        'phone': (person.get('phone_numbers') or [{}])[0].get('sanitized_number', '') if person.get('phone_numbers') else '',
        'linkedin_url': person.get('linkedin_url') or '',
        'title': person.get('title') or '',
        'raw': person,
    }


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
