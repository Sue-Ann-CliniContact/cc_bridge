"""Monday.com GraphQL client — thin wrapper around the v2 API.

Each authenticated Django user stores their Monday access_token on ClientProfile
(see accounts.models). This client assumes the caller passes a user whose token
can access the boards/workspace being queried. Workspace-scoped calls default to
settings.MONDAY_WORKSPACE_ID (the dedicated Bridge workspace).
"""
from __future__ import annotations

import json

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


def list_board_items(user, board_id: str, limit: int = 500) -> dict:
    """Fetch board metadata + items with column values for import mapping."""
    query = """
    query ($boardId: [ID!], $limit: Int!) {
      boards (ids: $boardId) {
        id
        name
        columns { id title type }
        items_page (limit: $limit) {
          items {
            id
            name
            group { id title }
            column_values {
              id
              text
              value
              column { id title type }
            }
          }
        }
      }
    }
    """
    data = graphql(user, query, {'boardId': [board_id], 'limit': limit})
    boards = data.get('boards') or []
    if not boards:
        return {'board': None, 'columns': [], 'items': []}
    board = boards[0]
    return {
        'board': {'id': board.get('id'), 'name': board.get('name')},
        'columns': board.get('columns') or [],
        'items': (board.get('items_page') or {}).get('items') or [],
    }


def auto_map_columns(columns: list[dict]) -> dict:
    """Guess which Monday columns correspond to email/name/org/etc. by title."""
    mapping = {
        'email': '',
        'organization_email': '',
        'first_name': '',
        'last_name': '',
        'organization': '',
        'role': '',
        'phone': '',
        'specialty': '',
    }
    for col in columns:
        title = (col.get('title') or '').strip().lower()
        col_id = col.get('id')
        if not col_id:
            continue
        if 'main contact email' in title and not mapping['email']:
            mapping['email'] = col_id
        elif ('email address' in title or ('generic' in title and 'email' in title)) and not mapping['organization_email']:
            mapping['organization_email'] = col_id
        elif 'email' in title and not mapping['email']:
            mapping['email'] = col_id
        elif 'phone' in title and not mapping['phone']:
            mapping['phone'] = col_id
        elif ('org' in title or 'company' in title or 'institution' in title) and not mapping['organization']:
            mapping['organization'] = col_id
        elif ('role' in title or 'title' in title or 'position' in title) and not mapping['role']:
            mapping['role'] = col_id
        elif ('specialty' in title or 'taxonomy' in title or 'therapeutic' in title) and not mapping['specialty']:
            mapping['specialty'] = col_id
        elif ('first' in title and 'name' in title) and not mapping['first_name']:
            mapping['first_name'] = col_id
        elif ('last' in title and 'name' in title) and not mapping['last_name']:
            mapping['last_name'] = col_id
    return mapping


def bridge_column_map(columns: list[dict]) -> dict:
    """Guess the standard Bridge board columns by title."""
    mapping = {
        'contact_name': '',
        'organization': '',
        'role_specialty': '',
        'classification': '',
        'assigned_specialist': '',
        'email': '',
        'organization_email': '',
        'source_directory': '',
        'campaign_status': '',
        'sequence_step': '',
        'human_action_needed': '',
        'reply_intent': '',
        'next_action': '',
        'campaign_name': '',
        'last_event_type': '',
        'last_event': '',
        'interest_level': '',
        'client_visible': '',
        'referral_link': '',
        'referred_count': '',
        'notes': '',
    }
    for col in columns:
        title = (col.get('title') or '').strip().lower()
        col_id = col.get('id')
        if not col_id:
            continue
        if (
            ('contact' in title and 'name' in title)
            or title == 'contact person'
        ) and not mapping['contact_name']:
            mapping['contact_name'] = col_id
        elif ('organization' in title or 'company' in title or 'institution' in title) and not mapping['organization']:
            mapping['organization'] = col_id
        elif ('role' in title or 'specialty' in title or 'position' in title) and not mapping['role_specialty']:
            mapping['role_specialty'] = col_id
        elif ('contact type' in title or 'classification' in title) and not mapping['classification']:
            mapping['classification'] = col_id
        elif ('assigned outreach specialist' in title or 'assigned specialist' in title or 'outreach specialist' in title or 'assigned to' in title) and not mapping['assigned_specialist']:
            mapping['assigned_specialist'] = col_id
        elif ('email address' in title or ('generic' in title and 'email' in title)) and not mapping['organization_email']:
            mapping['organization_email'] = col_id
        elif ('email address' in title or 'main contact email' in title or title == 'email' or 'email' in title) and not mapping['email']:
            mapping['email'] = col_id
        elif 'source' in title and not mapping['source_directory']:
            mapping['source_directory'] = col_id
        elif ('campaign status' in title or 'outreach status' in title) and not mapping['campaign_status']:
            mapping['campaign_status'] = col_id
        elif 'sequence step' in title and not mapping['sequence_step']:
            mapping['sequence_step'] = col_id
        elif 'human action' in title and not mapping['human_action_needed']:
            mapping['human_action_needed'] = col_id
        elif 'reply intent' in title and not mapping['reply_intent']:
            mapping['reply_intent'] = col_id
        elif 'next action' in title and not mapping['next_action']:
            mapping['next_action'] = col_id
        elif 'campaign name' in title and not mapping['campaign_name']:
            mapping['campaign_name'] = col_id
        elif 'last event type' in title and not mapping['last_event_type']:
            mapping['last_event_type'] = col_id
        elif ('last event' in title or 'date of last contact' in title) and not mapping['last_event']:
            mapping['last_event'] = col_id
        elif 'interest' in title and not mapping['interest_level']:
            mapping['interest_level'] = col_id
        elif 'client visible' in title and not mapping['client_visible']:
            mapping['client_visible'] = col_id
        elif 'referral' in title and 'link' in title and not mapping['referral_link']:
            mapping['referral_link'] = col_id
        elif title == 'link' and not mapping['referral_link']:
            mapping['referral_link'] = col_id
        elif 'referred' in title and 'count' in title and not mapping['referred_count']:
            mapping['referred_count'] = col_id
        elif 'notes' in title and not mapping['notes']:
            mapping['notes'] = col_id
    return mapping


def create_item(user, board_id: str, *, item_name: str, column_values: dict | None = None, group_id: str | None = None) -> dict:
    query = """
    mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON, $groupId: String) {
      create_item(board_id: $boardId, item_name: $itemName, column_values: $columnValues, group_id: $groupId) {
        id
      }
    }
    """
    variables = {
        'boardId': board_id,
        'itemName': item_name,
        'columnValues': json.dumps(column_values or {}),
        'groupId': group_id,
    }
    data = graphql(user, query, variables)
    return (data.get('create_item') or {})


def change_multiple_column_values(user, board_id: str, item_id: str, column_values: dict) -> dict:
    query = """
    mutation ($boardId: ID!, $itemId: ID!, $columnValues: JSON!) {
      change_multiple_column_values(board_id: $boardId, item_id: $itemId, column_values: $columnValues) {
        id
      }
    }
    """
    variables = {
        'boardId': board_id,
        'itemId': item_id,
        'columnValues': json.dumps(column_values),
    }
    data = graphql(user, query, variables)
    return (data.get('change_multiple_column_values') or {})


def change_column_value(user, board_id: str, item_id: str, column_id: str, value) -> dict:
    query = """
    mutation ($boardId: ID!, $itemId: ID!, $columnId: String!, $value: JSON!) {
      change_column_value(board_id: $boardId, item_id: $itemId, column_id: $columnId, value: $value) {
        id
      }
    }
    """
    data = graphql(
        user,
        query,
        {
            'boardId': board_id,
            'itemId': item_id,
            'columnId': column_id,
            'value': json.dumps(value),
        },
    )
    return (data.get('change_column_value') or {})


def create_board(user, *, name: str, workspace_id: str | None = None, board_kind: str = 'public') -> dict:
    wid = workspace_id or settings.MONDAY_WORKSPACE_ID
    if not wid:
        raise RuntimeError('MONDAY_WORKSPACE_ID not configured')
    query = """
    mutation ($boardName: String!, $workspaceId: ID!, $boardKind: BoardKind!) {
      create_board(board_name: $boardName, workspace_id: $workspaceId, board_kind: $boardKind) {
        id
        name
      }
    }
    """
    data = graphql(user, query, {'boardName': name, 'workspaceId': wid, 'boardKind': board_kind})
    return data.get('create_board') or {}


def create_column(user, board_id: str, *, title: str, column_type: str, defaults: dict | None = None) -> dict:
    query = """
    mutation ($boardId: ID!, $title: String!, $columnType: ColumnType!, $defaults: JSON) {
      create_column(board_id: $boardId, title: $title, column_type: $columnType, defaults: $defaults) {
        id
        title
      }
    }
    """
    data = graphql(
        user,
        query,
        {'boardId': board_id, 'title': title, 'columnType': column_type, 'defaults': json.dumps(defaults) if defaults else None},
    )
    return data.get('create_column') or {}


def create_group(user, board_id: str, *, group_name: str, group_color: str | None = None) -> dict:
    query = """
    mutation ($boardId: ID!, $groupName: String!, $groupColor: String) {
      create_group(board_id: $boardId, group_name: $groupName, group_color: $groupColor) {
        id
        title
      }
    }
    """
    data = graphql(user, query, {'boardId': board_id, 'groupName': group_name, 'groupColor': group_color})
    return data.get('create_group') or {}


def move_item_to_group(user, item_id: str, group_id: str) -> dict:
    query = """
    mutation ($itemId: ID!, $groupId: String!) {
      move_item_to_group(item_id: $itemId, group_id: $groupId) {
        id
      }
    }
    """
    data = graphql(user, query, {'itemId': item_id, 'groupId': group_id})
    return data.get('move_item_to_group') or {}


def create_update(user, item_id: str, body: str) -> dict:
    query = """
    mutation ($itemId: ID!, $body: String!) {
      create_update(item_id: $itemId, body: $body) {
        id
      }
    }
    """
    data = graphql(user, query, {'itemId': item_id, 'body': body})
    return data.get('create_update') or {}
