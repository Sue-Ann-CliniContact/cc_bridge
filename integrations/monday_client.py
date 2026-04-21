"""Monday.com GraphQL client — thin wrapper around the v2 API.

Each authenticated Django user stores their Monday access_token on ClientProfile
(see accounts.models). This client assumes the caller passes a user whose token
can access the boards/workspace being queried. Workspace-scoped calls default to
settings.MONDAY_WORKSPACE_ID (the dedicated Bridge workspace).
"""
from __future__ import annotations

import requests
from django.conf import settings

MONDAY_API_URL = 'https://api.monday.com/v2'
MONDAY_API_VERSION = '2023-10'


def _headers_for(user) -> dict:
    token = user.clientprofile.access_token
    return {'Authorization': token, 'API-Version': MONDAY_API_VERSION}


def graphql(user, query: str, variables: dict | None = None) -> dict:
    r = requests.post(
        MONDAY_API_URL,
        json={'query': query, 'variables': variables or {}},
        headers=_headers_for(user),
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json() or {}
    if 'errors' in payload:
        raise RuntimeError(f"Monday GraphQL errors: {payload['errors']}")
    return payload.get('data', {})


def list_workspace_boards(user, workspace_id: str | None = None) -> list[dict]:
    wid = workspace_id or settings.MONDAY_WORKSPACE_ID
    if not wid:
        raise RuntimeError('MONDAY_WORKSPACE_ID not configured')
    query = '{ boards (workspace_ids: %s) { id name } }' % wid
    data = graphql(user, query)
    return data.get('boards') or []


def get_board(user, board_id: str) -> dict:
    query = """
    query ($boardId: [ID!]) {
      boards (ids: $boardId) {
        id
        name
        groups { id title }
        columns { id title type }
      }
    }
    """
    data = graphql(user, query, {'boardId': [board_id]})
    boards = data.get('boards') or []
    return boards[0] if boards else {}
