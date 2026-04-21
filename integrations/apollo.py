"""Apollo API client — stub for Phase 2.

Apollo will supply email enrichment (people/match) and partner discovery
(people/search). Feature-flagged by APOLLO_API_KEY: when missing, callers
should treat Apollo as unavailable rather than erroring.
"""
from __future__ import annotations

from django.conf import settings


def is_configured() -> bool:
    return bool(settings.APOLLO_API_KEY)


def enrich_person(first_name: str, last_name: str, organization: str) -> dict:
    raise NotImplementedError('Phase 2 — Apollo enrichment not yet implemented')


def discover_people(specialty: str, geography: dict, limit: int = 50) -> list[dict]:
    raise NotImplementedError('Phase 2 — Apollo discovery not yet implemented')
