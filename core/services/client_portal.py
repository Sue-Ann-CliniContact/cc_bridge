from __future__ import annotations

import json
from collections import Counter

from accounts.models import ClientProfile
from ai_manager.services import AIService
from core.models import OutreachEvent, Project, ProjectLead
from integrations import monday_client


CHART_COLORS = [
    '#2550e6',
    '#5ebeff',
    '#12285f',
    '#43c59e',
    '#fdab3d',
    '#a25ddc',
    '#ff7575',
]


def is_operator(user, project: Project | None = None) -> bool:
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    if project and project.created_by_id == user.id:
        return True
    return False


def user_can_access_project(user, project: Project) -> bool:
    if is_operator(user, project):
        return True
    profile = getattr(user, 'clientprofile', None)
    if not profile or not profile.access_token or not project.monday_board_id:
        return False
    try:
        board = monday_client.get_board(user, str(project.monday_board_id))
    except Exception:
        return False
    return bool(board.get('id'))


def visible_projects_for_user(user):
    projects = list(Project.objects.all()[:50])
    return [project for project in projects if user_can_access_project(user, project)]


def _sync_user(project: Project, explicit_user=None):
    if explicit_user and getattr(getattr(explicit_user, 'clientprofile', None), 'access_token', None):
        return explicit_user
    if project.created_by and getattr(getattr(project.created_by, 'clientprofile', None), 'access_token', None):
        return project.created_by
    profile = ClientProfile.objects.exclude(access_token__isnull=True).exclude(access_token='').select_related('user').first()
    return profile.user if profile else None


def _series_from_counter(counter: Counter | dict, *, empty_label: str = 'No data') -> list[dict]:
    items = list(counter.items()) if hasattr(counter, 'items') else list(counter)
    items = [(str(label or empty_label), int(value)) for label, value in items if value]
    total = sum(value for _label, value in items)
    if not items:
        return [{'label': empty_label, 'value': 0, 'percent': 0, 'color': CHART_COLORS[0]}]
    ordered = sorted(items, key=lambda pair: (-pair[1], pair[0]))
    return [
        {
            'label': label,
            'value': value,
            'percent': round((value / total) * 100, 1) if total else 0,
            'color': CHART_COLORS[idx % len(CHART_COLORS)],
        }
        for idx, (label, value) in enumerate(ordered)
    ]


def _trend_series(project: Project) -> list[dict]:
    buckets: dict[str, Counter] = {}
    for event in OutreachEvent.objects.filter(project_lead__project=project).only('event_type', 'timestamp'):
        day = event.timestamp.date().isoformat()
        bucket = buckets.setdefault(day, Counter())
        if event.event_type == OutreachEvent.EVENT_EMAIL_SENT:
            bucket['sent'] += 1
        elif event.event_type == OutreachEvent.EVENT_EMAIL_OPENED:
            bucket['opened'] += 1
        elif event.event_type == OutreachEvent.EVENT_EMAIL_REPLIED:
            bucket['replied'] += 1
        elif event.event_type == OutreachEvent.EVENT_EMAIL_CLICKED:
            bucket['clicked'] += 1
    return [
        {
            'label': day,
            'sent': data.get('sent', 0),
            'opened': data.get('opened', 0),
            'replied': data.get('replied', 0),
            'clicked': data.get('clicked', 0),
        }
        for day, data in sorted(buckets.items())[-14:]
    ]


def _column_value(item: dict, column_id: str) -> dict:
    for value in item.get('column_values') or []:
        if (value.get('column') or {}).get('id') == column_id:
            return value
    return {}


def _column_text(item: dict, column_id: str) -> str:
    return (_column_value(item, column_id).get('text') or '').strip()


def _checkbox_value(item: dict, column_id: str) -> bool:
    value = _column_value(item, column_id)
    text = (value.get('text') or '').strip().lower()
    if text in ('true', 'yes', 'checked', 'v'):
        return True
    raw = value.get('value')
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return bool(parsed.get('checked') or parsed.get('value'))


def _user_assignment_identity(user) -> dict:
    profile = getattr(user, 'clientprofile', None)
    full_name = (user.get_full_name() or '').strip()
    return {
        'monday_id': str(getattr(profile, 'monday_id', '') or '').strip(),
        'email': (getattr(user, 'email', '') or '').strip().lower(),
        'full_name': full_name.lower(),
        'username': (getattr(user, 'username', '') or '').strip().lower(),
    }


def _is_item_assigned_to_user(item: dict, column_id: str, user) -> bool:
    if not column_id or not user:
        return False
    identity = _user_assignment_identity(user)
    value = _column_value(item, column_id)
    raw = value.get('value')
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = {}
        assignees = parsed.get('personsAndTeams') or parsed.get('persons_and_teams') or []
        if identity['monday_id'] and any(str(entry.get('id') or '') == identity['monday_id'] for entry in assignees):
            return True
    text = (value.get('text') or '').strip().lower()
    if not text:
        return False
    return any(token and token in text for token in (identity['full_name'], identity['email'], identity['username']))


def _board_snapshot(project: Project, *, user=None, assigned_only: bool = False) -> dict:
    board_id = str(project.monday_board_id or '')
    if not board_id:
        return {
            'board_id': '',
            'items': [],
            'columns': {},
            'groups_count': 0,
            'group_series': _series_from_counter({}),
            'status_series': _series_from_counter({}),
            'contact_type_series': _series_from_counter({}),
            'sequence_series': _series_from_counter({}),
            'assigned_items': [],
            'assigned_count': 0,
            'campaign_linked_count': 0,
            'with_email_count': 0,
        }

    sync_user = _sync_user(project, explicit_user=user)
    if not sync_user:
        return {
            'board_id': board_id,
            'items': [],
            'columns': {},
            'groups_count': 0,
            'group_series': _series_from_counter({}),
            'status_series': _series_from_counter({}),
            'contact_type_series': _series_from_counter({}),
            'sequence_series': _series_from_counter({}),
            'assigned_items': [],
            'assigned_count': 0,
            'campaign_linked_count': 0,
            'with_email_count': 0,
        }

    try:
        payload = monday_client.list_board_items(sync_user, board_id, limit=500)
    except Exception:
        return {
            'board_id': board_id,
            'items': [],
            'columns': {},
            'groups_count': 0,
            'group_series': _series_from_counter({}),
            'status_series': _series_from_counter({}),
            'contact_type_series': _series_from_counter({}),
            'sequence_series': _series_from_counter({}),
            'assigned_items': [],
            'assigned_count': 0,
            'campaign_linked_count': 0,
            'with_email_count': 0,
        }

    items = payload.get('items') or []
    columns = monday_client.bridge_column_map(payload.get('columns') or [])
    if assigned_only and columns.get('assigned_specialist'):
        items = [item for item in items if _is_item_assigned_to_user(item, columns['assigned_specialist'], user)]
    elif assigned_only:
        items = []

    group_counter = Counter((item.get('group') or {}).get('title') or 'Ungrouped' for item in items)
    status_counter = Counter()
    contact_type_counter = Counter()
    sequence_counter = Counter()
    assigned_items = []
    campaign_linked_count = 0
    with_email_count = 0

    for item in items:
        status = _column_text(item, columns.get('campaign_status', ''))
        contact_type = _column_text(item, columns.get('classification', ''))
        sequence_step = _column_text(item, columns.get('sequence_step', ''))
        campaign_name = _column_text(item, columns.get('campaign_name', ''))
        email = _column_text(item, columns.get('email', ''))

        if status:
            status_counter[status] += 1
        if contact_type:
            contact_type_counter[contact_type] += 1
        if sequence_step:
            sequence_counter[sequence_step] += 1
        if campaign_name:
            campaign_linked_count += 1
        if email:
            with_email_count += 1

        if columns.get('assigned_specialist') and _is_item_assigned_to_user(item, columns['assigned_specialist'], user):
            assigned_items.append({
                'name': item.get('name') or 'Lead',
                'organization': _column_text(item, columns.get('organization', '')),
                'role': _column_text(item, columns.get('role_specialty', '')),
                'status': status or 'Assigned',
                'interest': _column_text(item, columns.get('interest_level', '')),
                'next_action': _column_text(item, columns.get('next_action', '')),
                'group': (item.get('group') or {}).get('title') or '',
            })

    return {
        'board_id': board_id,
        'items': items,
        'columns': columns,
        'groups_count': len(group_counter),
        'group_series': _series_from_counter(group_counter),
        'status_series': _series_from_counter(status_counter),
        'contact_type_series': _series_from_counter(contact_type_counter),
        'sequence_series': _series_from_counter(sequence_counter),
        'assigned_items': assigned_items[:50],
        'assigned_count': len(assigned_items),
        'campaign_linked_count': campaign_linked_count,
        'with_email_count': with_email_count,
    }


def project_client_snapshot(project: Project, *, user=None, assigned_only: bool = False) -> dict:
    board_snapshot = _board_snapshot(project, user=user, assigned_only=assigned_only)
    project_leads = project.project_leads.select_related('lead').all()
    total_leads = board_snapshot['assigned_count'] if assigned_only else project_leads.count()
    with_email = board_snapshot['with_email_count'] if assigned_only else project_leads.exclude(lead__email__isnull=True).exclude(lead__email='').count()
    campaigns = list(project.campaigns.all())
    active_campaigns = [campaign for campaign in campaigns if campaign.status == 'active']
    status_counter = Counter(pl.get_campaign_status_display() for pl in project_leads if pl.campaign_status)
    contact_counter = Counter(pl.lead.get_classification_display() for pl in project_leads)
    trend = _trend_series(project)
    sent = sum(day['sent'] for day in trend)
    opened = sum(day['opened'] for day in trend)
    replied = sum(day['replied'] for day in trend)
    clicked = sum(day['clicked'] for day in trend)

    denominator = sent or 1
    snapshot = {
        'project_id': project.pk,
        'name': project.name,
        'study_code': project.study_code,
        'status': project.get_status_display(),
        'board_id': board_snapshot['board_id'],
        'headline_stats': [
            {'label': 'Project Leads', 'value': total_leads, 'subtext': f'{with_email} with direct email'},
            {'label': 'Campaigns', 'value': len(campaigns), 'subtext': f'{len(active_campaigns)} active'},
            {'label': 'In Campaign', 'value': board_snapshot['campaign_linked_count'] or project_leads.exclude(campaign__isnull=True).count(), 'subtext': 'leads currently on a sequence'},
            {'label': 'Assigned To You', 'value': board_snapshot['assigned_count'], 'subtext': 'leads visible to this login'},
        ],
        'performance_stats': [
            {'label': 'Open Rate', 'value': round((opened / denominator) * 100, 1) if sent else 0, 'suffix': '%', 'subtext': f'{opened} opens'},
            {'label': 'Reply Rate', 'value': round((replied / denominator) * 100, 1) if sent else 0, 'suffix': '%', 'subtext': f'{replied} replies'},
            {'label': 'Click Rate', 'value': round((clicked / denominator) * 100, 1) if sent else 0, 'suffix': '%', 'subtext': f'{clicked} clicks'},
            {'label': 'Board Groups', 'value': board_snapshot['groups_count'], 'subtext': project.monday_board_id or 'No Monday board'},
        ],
        'group_series': board_snapshot['group_series'],
        'status_series': board_snapshot['status_series'] if assigned_only or board_snapshot['status_series'][0]['value'] else _series_from_counter(status_counter),
        'contact_type_series': board_snapshot['contact_type_series'] if assigned_only or board_snapshot['contact_type_series'][0]['value'] else _series_from_counter(contact_counter),
        'sequence_series': board_snapshot['sequence_series'],
        'trend_series': trend,
        'assigned_items': board_snapshot['assigned_items'],
        'assigned_count': board_snapshot['assigned_count'],
        'campaign_cards': [
            {
                'id': campaign.pk,
                'name': campaign.name,
                'status': campaign.get_status_display(),
                'lead_count': campaign.project_leads.count(),
                'started_at': campaign.started_at,
            }
            for campaign in campaigns[:6]
        ],
        'ai_context': {
            'project': project.name,
            'study_code': project.study_code,
            'project_status': project.get_status_display(),
            'headline_stats': [
                {'label': stat['label'], 'value': stat['value']}
                for stat in [
                    {'label': 'Project Leads', 'value': total_leads},
                    {'label': 'Campaigns', 'value': len(campaigns)},
                    {'label': 'In Campaign', 'value': board_snapshot['campaign_linked_count'] or project_leads.exclude(campaign__isnull=True).count()},
                    {'label': 'Assigned To You', 'value': board_snapshot['assigned_count']},
                ]
            ],
            'group_breakdown': board_snapshot['group_series'],
            'status_breakdown': board_snapshot['status_series'] if assigned_only or board_snapshot['status_series'][0]['value'] else _series_from_counter(status_counter),
            'contact_type_breakdown': board_snapshot['contact_type_series'] if assigned_only or board_snapshot['contact_type_series'][0]['value'] else _series_from_counter(contact_counter),
            'sequence_breakdown': board_snapshot['sequence_series'],
            'trend': trend,
        },
    }
    return snapshot


def workspace_portal_snapshot(projects, *, user=None, assigned_only: bool = True) -> dict:
    snapshots = [project_client_snapshot(project, user=user, assigned_only=assigned_only) for project in projects]
    status_groups = {'Active': [], 'Pending': [], 'Completed': []}
    for snap in snapshots:
        status_label = (snap['status'] or '').lower()
        if 'active' in status_label:
            status_groups['Active'].append(snap)
        elif 'completed' in status_label:
            status_groups['Completed'].append(snap)
        else:
            status_groups['Pending'].append(snap)

    summary = {
        'active_projects': len(status_groups['Active']),
        'pending_projects': len(status_groups['Pending']),
        'completed_projects': len(status_groups['Completed']),
        'total_leads': sum(item['headline_stats'][0]['value'] for item in snapshots),
        'total_assigned': sum(item['assigned_count'] for item in snapshots),
    }
    return {
        'summary': summary,
        'projects_by_status': status_groups,
        'ai_context': {
            'workspace_summary': summary,
            'projects': [
                {
                    'study_code': snap['study_code'],
                    'name': snap['name'],
                    'status': snap['status'],
                    'project_leads': snap['headline_stats'][0]['value'],
                    'campaigns': snap['headline_stats'][1]['value'],
                    'assigned': snap['assigned_count'],
                }
                for snap in snapshots
            ],
        },
    }


CLIENT_PROJECT_AI_PROMPT = (
    "You are Lini, the client-facing Bridge analytics assistant. You answer only from the sanitized project analytics "
    "data provided. Never invent counts or expose hidden/raw lead data. Keep answers concise, helpful, and client-safe. "
    "If asked about hidden lead-level details, explain that Bridge only exposes leads assigned to the logged-in user here."
)


CLIENT_WORKSPACE_AI_PROMPT = (
    "You are Lini, the client-facing Bridge workspace assistant. You answer only from sanitized multi-project summaries. "
    "Do not expose raw lead data or operational internals. Give concise portfolio-level guidance."
)


def answer_project_question(project: Project, question: str, *, user=None, assigned_only: bool = True) -> str:
    snapshot = project_client_snapshot(project, user=user, assigned_only=assigned_only)
    prompt = (
        f"Project analytics snapshot:\n{json.dumps(snapshot['ai_context'], indent=2)}\n\n"
        f"Client question: {question.strip()}\n\n"
        "Answer in 3-6 sentences. Reference the available metrics, trends, and assignment visibility where relevant."
    )
    return AIService.complete(
        prompt=prompt,
        system_prompt=CLIENT_PROJECT_AI_PROMPT,
        function_name='client_project_ai',
        user=user,
        max_tokens=500,
    )


def answer_workspace_question(projects, question: str, *, user=None, assigned_only: bool = True) -> str:
    snapshot = workspace_portal_snapshot(projects, user=user, assigned_only=assigned_only)
    prompt = (
        f"Workspace analytics snapshot:\n{json.dumps(snapshot['ai_context'], indent=2)}\n\n"
        f"Client question: {question.strip()}\n\n"
        "Answer in 3-6 sentences. Stay at portfolio level unless the summary includes a direct project comparison."
    )
    return AIService.complete(
        prompt=prompt,
        system_prompt=CLIENT_WORKSPACE_AI_PROMPT,
        function_name='client_workspace_ai',
        user=user,
        max_tokens=500,
    )
