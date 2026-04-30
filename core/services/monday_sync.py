from __future__ import annotations

import logging
import re
from html import unescape

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from accounts.models import ClientProfile
from core.models import Lead, Project, ProjectLead, OutreachEvent
from integrations import monday_client

log = logging.getLogger(__name__)


GROUP_PRE_OUTREACH = 'Pre-Outreach'
GROUP_ACTIVE = 'Active Outreach'
GROUP_HUMAN = 'Needs Human Follow-up'
GROUP_HANDOFF = 'Study Team Handoff'
GROUP_CLOSED = 'Closed'

WORKFLOW_GROUPS = [
    (GROUP_PRE_OUTREACH, '#c4c4c4'),
    (GROUP_ACTIVE, '#fdab3d'),
    (GROUP_HUMAN, '#0086c0'),
    (GROUP_HANDOFF, '#a25ddc'),
    (GROUP_CLOSED, '#00c875'),
]

HUMAN_ACTION_LABELS = [
    {'label': 'No Action', 'index': 1, 'color': 'working_orange'},
    {'label': 'Review Reply', 'index': 2, 'color': 'dark_blue'},
    {'label': 'Send Flyer', 'index': 3, 'color': 'bright_blue'},
    {'label': 'Connect Study Team', 'index': 4, 'color': 'purple'},
    {'label': 'Confirm Alternate Email', 'index': 5, 'color': 'egg_yolk'},
]

REPLY_INTENT_LABELS = [
    {'label': 'Unknown', 'index': 1, 'color': 'working_orange'},
    {'label': 'Interested', 'index': 2, 'color': 'done_green'},
    {'label': 'Not Interested', 'index': 3, 'color': 'stuck_red'},
    {'label': 'Discussing Internally', 'index': 4, 'color': 'dark_blue'},
    {'label': 'Alternate Email Provided', 'index': 5, 'color': 'purple'},
]

CLASSIFICATION_LABELS = [
    {'label': 'Unclassified', 'index': 1, 'color': 'working_orange'},
    {'label': 'Metabolic Clinic', 'index': 2, 'color': 'bright_blue'},
    {'label': 'Genetic Counselor', 'index': 3, 'color': 'dark_blue'},
    {'label': 'Advocacy Organization', 'index': 4, 'color': 'done_green'},
    {'label': 'Community Provider', 'index': 5, 'color': 'egg_yolk'},
]

SOURCE_DIRECTORY_LABELS = [
    {'label': 'NPI Registry', 'index': 1, 'color': 'bright_blue'},
    {'label': 'Apollo', 'index': 2, 'color': 'done_green'},
    {'label': 'AI Suggested (pending review)', 'index': 3, 'color': 'purple'},
    {'label': 'CSV Import', 'index': 4, 'color': 'working_orange'},
    {'label': 'Manual', 'index': 5, 'color': 'dark_blue'},
    {'label': 'ClinicalTrials.gov', 'index': 6, 'color': 'grass_green'},
    {'label': 'Monday Board Import', 'index': 7, 'color': 'egg_yolk'},
]

CAMPAIGN_STATUS_LABELS = [
    {'label': 'Not Started', 'index': 1, 'color': 'working_orange'},
    {'label': 'Email Sent', 'index': 2, 'color': 'bright_blue'},
    {'label': 'Engaged', 'index': 3, 'color': 'dark_blue'},
    {'label': 'Handoff to Study Team', 'index': 4, 'color': 'purple'},
    {'label': 'Opened', 'index': 5, 'color': 'sky'},
    {'label': 'Clicked', 'index': 6, 'color': 'grass_green'},
    {'label': 'Replied', 'index': 7, 'color': 'done_green'},
    {'label': 'Bounced', 'index': 8, 'color': 'stuck_red'},
    {'label': 'Unsubscribed', 'index': 9, 'color': 'blackish'},
    {'label': 'Not Interested', 'index': 10, 'color': 'red_shadow'},
]

INTEREST_LEVEL_LABELS = [
    {'label': 'Interested', 'index': 1, 'color': 'done_green'},
    {'label': 'Not Interested', 'index': 2, 'color': 'stuck_red'},
]


def _sync_user(project: Project, explicit_user=None):
    if explicit_user and _has_monday_token(explicit_user):
        return explicit_user
    if project.created_by and _has_monday_token(project.created_by):
        return project.created_by
    profile = ClientProfile.objects.exclude(access_token__isnull=True).exclude(access_token='').select_related('user').first()
    return profile.user if profile else None


def _has_monday_token(user) -> bool:
    profile = getattr(user, 'clientprofile', None)
    return bool(profile and profile.access_token)


def _item_name_for_lead(lead: Lead) -> str:
    full_name = f'{lead.first_name} {lead.last_name}'.strip()
    return full_name or lead.email or lead.organization or f'Lead {lead.pk}'


def _status_label(project_lead: ProjectLead) -> str:
    status = project_lead.campaign_status
    label_map = {
        ProjectLead.STATUS_QUEUED: 'Not Started',
        ProjectLead.STATUS_SENT: 'Email Sent',
        ProjectLead.STATUS_OPENED: 'Engaged',
        ProjectLead.STATUS_CLICKED: 'Engaged',
        ProjectLead.STATUS_REPLIED: 'Engaged',
        ProjectLead.STATUS_INTERESTED: 'Handoff to Study Team',
        ProjectLead.STATUS_NOT_INTERESTED: 'Not Started',
        ProjectLead.STATUS_BOUNCED: 'Email Sent',
        ProjectLead.STATUS_UNSUBSCRIBED: 'Not Started',
    }
    return label_map.get(status, project_lead.get_campaign_status_display())


def _interest_label(project_lead: ProjectLead) -> str:
    if project_lead.campaign_status == ProjectLead.STATUS_INTERESTED:
        return 'Interested'
    if project_lead.campaign_status in (ProjectLead.STATUS_NOT_INTERESTED, ProjectLead.STATUS_UNSUBSCRIBED):
        return 'Not Interested'
    return ''


def _latest_event(project_lead: ProjectLead):
    return project_lead.events.order_by('-timestamp').first()


def _reply_text(project_lead: ProjectLead) -> str:
    latest = _latest_event(project_lead)
    if not latest or latest.event_type != OutreachEvent.EVENT_EMAIL_REPLIED:
        return ''
    payload = latest.raw_payload or {}
    for key in ('reply_text', 'reply_body', 'body', 'text', 'snippet', 'message'):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _event_payload_text(payload: dict, keys: tuple[str, ...]) -> str:
    if not isinstance(payload, dict):
        return ''
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _strip_html(text: str) -> str:
    cleaned = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'</p\s*>', '\n\n', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r'\r\n?', '\n', cleaned)
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def _reply_intent(project_lead: ProjectLead) -> str:
    if project_lead.campaign_status == ProjectLead.STATUS_INTERESTED:
        return 'Interested'
    if project_lead.campaign_status in (ProjectLead.STATUS_NOT_INTERESTED, ProjectLead.STATUS_UNSUBSCRIBED):
        return 'Not Interested'
    text = _reply_text(project_lead).lower()
    if not text:
        return 'Unknown' if project_lead.campaign_status != ProjectLead.STATUS_REPLIED else 'Unknown'
    if re.search(r'\b(not interested|no thanks|please remove|unsubscribe|do not contact)\b', text):
        return 'Not Interested'
    if re.search(r'\b(alternative email|use .*@|reach me at|contact me at|email me at)\b', text):
        return 'Alternate Email Provided'
    if re.search(r'\b(discuss|come back|circle back|review internally|team will review)\b', text):
        return 'Discussing Internally'
    if re.search(r'\b(interested|happy to help|send the flyer|connect me|would like to learn more)\b', text):
        return 'Interested'
    return 'Unknown'


def _human_action(project_lead: ProjectLead) -> str:
    intent = _reply_intent(project_lead)
    if intent == 'Interested':
        return 'Connect Study Team'
    if intent == 'Alternate Email Provided':
        return 'Confirm Alternate Email'
    if project_lead.campaign_status == ProjectLead.STATUS_REPLIED:
        return 'Review Reply'
    return 'No Action'


def _next_action(project_lead: ProjectLead) -> str:
    action = _human_action(project_lead)
    if action == 'Connect Study Team':
        return 'Introduce provider to study team and send flyer'
    if action == 'Confirm Alternate Email':
        return 'Review reply and update preferred email before continuing outreach'
    if action == 'Review Reply':
        return 'Review reply and decide whether to send flyer or hand off to study team'
    if project_lead.campaign_status == ProjectLead.STATUS_SENT:
        return 'Wait for response or next sequence step'
    if project_lead.campaign_status in (ProjectLead.STATUS_OPENED, ProjectLead.STATUS_CLICKED):
        return 'Monitor engagement and continue sequence unless human follow-up is needed'
    return ''


def _campaign_name(project_lead: ProjectLead) -> str:
    return project_lead.campaign.name if project_lead.campaign_id and project_lead.campaign else ''


def _sequence_step(project_lead: ProjectLead) -> str:
    latest = _latest_event(project_lead)
    payload = latest.raw_payload if latest else {}
    for key in ('sequence_step', 'step', 'step_num', 'step_number'):
        value = payload.get(key) if isinstance(payload, dict) else None
        if value not in (None, ''):
            return f'Step {value}'
    if project_lead.campaign_status == ProjectLead.STATUS_QUEUED:
        return 'Queued for Step 1'
    if project_lead.campaign_status == ProjectLead.STATUS_SENT:
        return 'Step 1 Sent'
    if project_lead.campaign_status in (ProjectLead.STATUS_OPENED, ProjectLead.STATUS_CLICKED):
        return 'Active Sequence'
    if project_lead.campaign_status == ProjectLead.STATUS_REPLIED:
        return 'Replied'
    if project_lead.campaign_status == ProjectLead.STATUS_INTERESTED:
        return 'Stopped - Interested'
    if project_lead.campaign_status in (ProjectLead.STATUS_NOT_INTERESTED, ProjectLead.STATUS_UNSUBSCRIBED):
        return 'Stopped - Closed'
    return ''


def _sequence_step_number(project_lead: ProjectLead, event: OutreachEvent | None = None) -> int | None:
    latest = event or _latest_event(project_lead)
    payload = latest.raw_payload if latest else {}
    if isinstance(payload, dict):
        for key in ('sequence_step', 'step', 'step_num', 'step_number'):
            value = payload.get(key)
            if value in (None, ''):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _sequence_step_copy(project_lead: ProjectLead, event: OutreachEvent | None = None) -> dict:
    step_number = _sequence_step_number(project_lead, event)
    steps = project_lead.campaign.sequence_config if project_lead.campaign_id and project_lead.campaign else []
    if not isinstance(steps, list):
        return {}
    for index, step in enumerate(steps, start=1):
        try:
            candidate_num = int(step.get('step_num') or index)
        except (TypeError, ValueError):
            candidate_num = index
        if step_number and candidate_num == step_number:
            return step
    if step_number == 1 and steps:
        return steps[0]
    return {}


def _event_subject(project_lead: ProjectLead, event: OutreachEvent) -> str:
    payload = event.raw_payload or {}
    subject = _event_payload_text(payload, ('subject', 'email_subject', 'message_subject'))
    if subject:
        return _strip_html(subject)
    if event.event_type == OutreachEvent.EVENT_EMAIL_SENT:
        subject = (_sequence_step_copy(project_lead, event).get('subject') or '').strip()
        if subject:
            return subject
    return ''


def _event_body(project_lead: ProjectLead, event: OutreachEvent) -> str:
    payload = event.raw_payload or {}
    if event.event_type == OutreachEvent.EVENT_EMAIL_REPLIED:
        text = _event_payload_text(payload, ('reply_text', 'reply_body', 'body', 'text', 'snippet', 'message'))
        return _strip_html(text)[:4000] if text else ''
    if event.event_type == OutreachEvent.EVENT_EMAIL_SENT:
        text = _event_payload_text(payload, ('body', 'html', 'text', 'message'))
        if text:
            return _strip_html(text)[:4000]
        body = (_sequence_step_copy(project_lead, event).get('body') or '').strip()
        if body:
            return _strip_html(body)[:4000]
    return ''


def _event_heading(event: OutreachEvent) -> str:
    if event.event_type == OutreachEvent.EVENT_EMAIL_SENT:
        return 'Email Sent'
    if event.event_type == OutreachEvent.EVENT_EMAIL_REPLIED:
        return 'Email Reply Received'
    return event.get_event_type_display()


def _build_monday_thread_update(project_lead: ProjectLead, event: OutreachEvent) -> str:
    lines = [
        f'Bridge activity: {_event_heading(event)}',
        f'Lead: {_item_name_for_lead(project_lead.lead)}',
        f'When: {timezone.localtime(event.timestamp).strftime("%Y-%m-%d %H:%M")}',
    ]
    campaign_name = _campaign_name(project_lead)
    if campaign_name:
        lines.append(f'Campaign: {campaign_name}')
    sequence_step = _sequence_step(project_lead)
    if sequence_step:
        lines.append(f'Sequence: {sequence_step}')
    subject = _event_subject(project_lead, event)
    if subject:
        lines.append(f'Subject: {subject}')
    body = _event_body(project_lead, event)
    if body:
        lines.extend(['', body[:3000]])
    return '\n'.join(lines).strip()


def _last_event_type(project_lead: ProjectLead) -> str:
    latest = _latest_event(project_lead)
    return latest.get_event_type_display() if latest else ''


def _target_group_name(project_lead: ProjectLead) -> str:
    if project_lead.campaign_status == ProjectLead.STATUS_INTERESTED:
        return GROUP_HANDOFF
    if project_lead.campaign_status in (ProjectLead.STATUS_NOT_INTERESTED, ProjectLead.STATUS_UNSUBSCRIBED, ProjectLead.STATUS_BOUNCED):
        return GROUP_CLOSED
    if _human_action(project_lead) != 'No Action':
        return GROUP_HUMAN
    if project_lead.campaign_status in (ProjectLead.STATUS_SENT, ProjectLead.STATUS_OPENED, ProjectLead.STATUS_CLICKED, ProjectLead.STATUS_REPLIED):
        return GROUP_ACTIVE
    return GROUP_PRE_OUTREACH


def _role_specialty(lead: Lead) -> str:
    bits = [b.strip() for b in [lead.role, lead.specialty] if b and str(b).strip()]
    return ' | '.join(bits)[:255]


def _classification_label(lead: Lead) -> str:
    return lead.get_classification_display()


def _referral_link(project_lead: ProjectLead) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/r/{project_lead.tracking_token}"


def _referral_link_value(project_lead: ProjectLead) -> dict:
    link = _referral_link(project_lead)
    return {'url': link, 'text': 'Referral link'}


def _lead_origin_monday_ref(lead: Lead) -> tuple[str, str]:
    geo = lead.geography or {}
    return (str(geo.get('monday_board_id') or ''), str(geo.get('monday_item_id') or ''))


def _attach_origin_item_if_same_board(project_lead: ProjectLead) -> bool:
    if project_lead.monday_item_id:
        return True
    board_id, item_id = _lead_origin_monday_ref(project_lead.lead)
    if board_id and item_id and str(project_lead.project.monday_board_id or '') == board_id:
        project_lead.monday_item_id = item_id
        project_lead.save(update_fields=['monday_item_id', 'updated_at'])
        return True
    return False


def _board_item_matches_lead(item: dict, lead: Lead) -> bool:
    target_emails = {
        email.lower()
        for email in (lead.email, lead.organization_email)
        if email and '@' in email
    }
    searchable_parts = [str(item.get('name') or '')]
    for column_value in item.get('column_values') or []:
        searchable_parts.append(str(column_value.get('text') or ''))
        searchable_parts.append(str(column_value.get('value') or ''))
    searchable = ' '.join(searchable_parts).lower()
    if target_emails and any(email in searchable for email in target_emails):
        return True

    item_name = (item.get('name') or '').strip().lower()
    lead_name = _item_name_for_lead(lead).strip().lower()
    organization = (lead.organization or '').strip().lower()
    return bool(item_name and (item_name == lead_name or (organization and item_name == organization)))


def _attach_existing_board_item_by_match(project_lead: ProjectLead, user, board_id: str) -> bool:
    if project_lead.monday_item_id:
        return True
    try:
        payload = monday_client.list_board_items(user, board_id, limit=500)
    except Exception as exc:  # noqa: BLE001
        log.warning('Monday existing-item lookup failed for ProjectLead %s: %s', project_lead.pk, exc)
        return False

    for item in payload.get('items') or []:
        item_id = str(item.get('id') or '')
        if item_id and _board_item_matches_lead(item, project_lead.lead):
            project_lead.monday_item_id = item_id
            project_lead.save(update_fields=['monday_item_id', 'updated_at'])
            log.info('Attached ProjectLead %s to existing Monday item %s', project_lead.pk, item_id)
            return True
    return False


def _column_type(columns: dict, key: str) -> str:
    column_id = columns.get(key)
    return (columns.get('__types') or {}).get(column_id, '') if column_id else ''


def _status_value(columns: dict, key: str, label: str):
    return {'label': label} if _column_type(columns, key) == 'status' else label


def _email_value(columns: dict, key: str, email: str):
    return {'email': email, 'text': email} if _column_type(columns, key) == 'email' else email


def _date_value(columns: dict, key: str, value: str):
    return {'date': value} if _column_type(columns, key) == 'date' else value


def _link_value(columns: dict, key: str, value: dict):
    if _column_type(columns, key) == 'link':
        return value
    return value.get('url') or value.get('text') or ''


def _column_values(project_lead: ProjectLead, columns: dict) -> dict:
    lead = project_lead.lead
    values: dict = {}
    if columns.get('contact_name'):
        values[columns['contact_name']] = _item_name_for_lead(lead)
    if columns.get('organization'):
        values[columns['organization']] = lead.organization or ''
    if columns.get('role_specialty'):
        values[columns['role_specialty']] = _role_specialty(lead)
    if columns.get('classification'):
        values[columns['classification']] = _status_value(columns, 'classification', _classification_label(lead))
    if columns.get('email') and lead.email:
        values[columns['email']] = _email_value(columns, 'email', lead.email)
    if columns.get('organization_email') and lead.organization_email:
        values[columns['organization_email']] = _email_value(columns, 'organization_email', lead.organization_email)
    if columns.get('source_directory'):
        values[columns['source_directory']] = _status_value(columns, 'source_directory', lead.get_source_display())
    if columns.get('campaign_status'):
        values[columns['campaign_status']] = _status_value(columns, 'campaign_status', _status_label(project_lead))
    latest_event = _latest_event(project_lead)
    if columns.get('last_event') and latest_event:
        values[columns['last_event']] = _date_value(columns, 'last_event', latest_event.timestamp.date().isoformat())
    if columns.get('interest_level'):
        label = _interest_label(project_lead)
        if label:
            values[columns['interest_level']] = _status_value(columns, 'interest_level', label)
    if columns.get('sequence_step'):
        values[columns['sequence_step']] = _sequence_step(project_lead)
    if columns.get('human_action_needed'):
        values[columns['human_action_needed']] = _status_value(columns, 'human_action_needed', _human_action(project_lead))
    if columns.get('reply_intent'):
        values[columns['reply_intent']] = _status_value(columns, 'reply_intent', _reply_intent(project_lead))
    if columns.get('next_action'):
        next_action = _next_action(project_lead)
        if next_action:
            values[columns['next_action']] = next_action
    if columns.get('campaign_name'):
        campaign_name = _campaign_name(project_lead)
        if campaign_name:
            values[columns['campaign_name']] = campaign_name
    if columns.get('last_event_type') and latest_event:
        values[columns['last_event_type']] = _last_event_type(project_lead)
    if columns.get('referral_link'):
        values[columns['referral_link']] = _link_value(columns, 'referral_link', _referral_link_value(project_lead))
    if columns.get('referred_count'):
        values[columns['referred_count']] = project_lead.referred_count
    if columns.get('notes') and lead.do_not_contact_reason:
        values[columns['notes']] = lead.do_not_contact_reason
    elif columns.get('notes'):
        latest_event = project_lead.events.order_by('-timestamp').first()
        if latest_event:
            values[columns['notes']] = f'Latest Bridge event: {latest_event.get_event_type_display()}'
    return values


def _campaign_column_values(project_lead: ProjectLead, columns: dict) -> dict:
    values: dict = {}
    if columns.get('campaign_status'):
        values[columns['campaign_status']] = _status_value(columns, 'campaign_status', _status_label(project_lead))
    if columns.get('sequence_step'):
        values[columns['sequence_step']] = _sequence_step(project_lead)
    if columns.get('campaign_name'):
        campaign_name = _campaign_name(project_lead)
        if campaign_name:
            values[columns['campaign_name']] = campaign_name
    if columns.get('next_action'):
        next_action = _next_action(project_lead)
        if next_action:
            values[columns['next_action']] = next_action
    return values


def _safe_change_column_values(user, board_id: str, item_id: str, values: dict) -> list[str]:
    if not values:
        return []
    try:
        monday_client.change_multiple_column_values(user, board_id, item_id, values)
        return []
    except Exception as bulk_exc:  # noqa: BLE001
        errors = [f'bulk update failed: {bulk_exc}']

    for column_id, value in values.items():
        try:
            monday_client.change_column_value(user, board_id, item_id, column_id, value)
        except Exception as exc:  # noqa: BLE001
            errors.append(f'{column_id}: {exc}')
    return errors


def _mapping_with_types(columns: list[dict]) -> dict:
    mapping = monday_client.bridge_column_map(columns)
    mapping['__types'] = {
        col.get('id'): col.get('type')
        for col in columns
        if col.get('id') and col.get('type')
    }
    return mapping


def _board_columns(user, board_id: str) -> dict:
    board = monday_client.get_board(user, board_id)
    return _mapping_with_types(board.get('columns') or [])


def _board_meta(user, board_id: str) -> dict:
    return monday_client.get_board(user, board_id)


def _ensure_groups(user, board_id: str) -> dict[str, str]:
    board = _board_meta(user, board_id)
    groups = {g.get('title'): g.get('id') for g in (board.get('groups') or []) if g.get('id') and g.get('title')}
    for group_name, color in WORKFLOW_GROUPS:
        if group_name in groups:
            continue
        try:
            created = monday_client.create_group(user, board_id, group_name=group_name, group_color=color)
            if created.get('id'):
                groups[group_name] = created['id']
        except Exception as exc:  # noqa: BLE001
            log.warning('Monday group creation failed for board %s group %s: %s', board_id, group_name, exc)
    return groups


def _ensure_board_schema(user, board_id: str) -> dict:
    board = _board_meta(user, board_id)
    columns = board.get('columns') or []
    mapping = monday_client.bridge_column_map(columns)

    for key, title, column_type in BRIDGE_BOARD_COLUMNS:
        if mapping.get(key):
            continue
        try:
            monday_client.create_column(
                user,
                board_id,
                title=title,
                column_type=column_type,
                defaults=_column_defaults(title),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning('Monday column creation failed for board %s column %s: %s', board_id, title, exc)

    return _mapping_with_types(_board_meta(user, board_id).get('columns') or [])


def _sync_group(project_lead: ProjectLead, user) -> None:
    if not project_lead.monday_item_id:
        return
    board_id = str(project_lead.project.monday_board_id or '')
    if not board_id:
        return
    groups = _ensure_groups(user, board_id)
    target_group = groups.get(_target_group_name(project_lead))
    if target_group:
        try:
            monday_client.move_item_to_group(user, project_lead.monday_item_id, target_group)
        except Exception as exc:  # noqa: BLE001
            log.warning('Monday item move failed for ProjectLead %s: %s', project_lead.pk, exc)


def _create_monday_item(user, board_id: str, *, item_name: str, group_id: str | None = None) -> dict:
    try:
        return monday_client.create_item(user, board_id, item_name=item_name, column_values={}, group_id=group_id)
    except Exception as grouped_exc:  # noqa: BLE001
        if not group_id:
            raise
        log.warning('Monday item creation with group failed for board %s item %s: %s', board_id, item_name, grouped_exc)
        return monday_client.create_item(user, board_id, item_name=item_name, column_values={}, group_id=None)


def sync_project_lead(project_lead: ProjectLead, *, user=None) -> dict:
    project = project_lead.project
    board_id = str(project.monday_board_id or '')
    if not board_id:
        return {'ok': False, 'skipped': 'project has no monday_board_id'}

    sync_user = _sync_user(project, explicit_user=user)
    if not sync_user:
        return {'ok': False, 'skipped': 'no Monday user token available'}

    try:
        _attach_origin_item_if_same_board(project_lead)
        _attach_existing_board_item_by_match(project_lead, sync_user, board_id)
        columns = _ensure_board_schema(sync_user, board_id)
        item_name = _item_name_for_lead(project_lead.lead)
        values = _column_values(project_lead, columns)
        update_errors = []
        if project_lead.monday_item_id:
            update_errors = _safe_change_column_values(sync_user, board_id, project_lead.monday_item_id, values)
            action = 'updated'
        else:
            groups = _ensure_groups(sync_user, board_id)
            target_group = groups.get(_target_group_name(project_lead))
            created = _create_monday_item(sync_user, board_id, item_name=item_name, group_id=target_group)
            item_id = str(created.get('id') or '')
            if item_id:
                project_lead.monday_item_id = item_id
                project_lead.save(update_fields=['monday_item_id', 'updated_at'])
                update_errors = _safe_change_column_values(sync_user, board_id, item_id, values)
            action = 'created'
        _sync_group(project_lead, sync_user)
        result = {'ok': True, 'action': action, 'item_id': project_lead.monday_item_id}
        if update_errors:
            result['warnings'] = update_errors[:5]
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning('Monday sync failed for ProjectLead %s: %s', project_lead.pk, exc)
        return {'ok': False, 'error': str(exc)}


def sync_campaign_state(project_lead: ProjectLead, *, user=None) -> dict:
    project = project_lead.project
    board_id = str(project.monday_board_id or '')
    if not board_id:
        return {'ok': False, 'skipped': 'project has no monday_board_id'}
    if not project_lead.monday_item_id:
        lead_sync = sync_project_lead(project_lead, user=user)
        if not lead_sync.get('ok'):
            return {'ok': False, 'skipped': lead_sync.get('skipped') or lead_sync.get('error') or 'project lead has no monday item id'}
        project_lead.refresh_from_db(fields=['monday_item_id', 'updated_at'])
        if not project_lead.monday_item_id:
            return {'ok': False, 'skipped': 'project lead has no monday item id'}

    sync_user = _sync_user(project, explicit_user=user)
    if not sync_user:
        return {'ok': False, 'skipped': 'no Monday user token available'}

    try:
        columns = _board_columns(sync_user, board_id)
        values = _campaign_column_values(project_lead, columns)
        if values:
            update_errors = _safe_change_column_values(sync_user, board_id, project_lead.monday_item_id, values)
        else:
            update_errors = []
        result = {'ok': True, 'action': 'campaign_state_updated', 'item_id': project_lead.monday_item_id}
        if update_errors:
            result['warnings'] = update_errors[:5]
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning('Monday campaign state sync failed for ProjectLead %s: %s', project_lead.pk, exc)
        return {'ok': False, 'error': str(exc)}


def sync_campaign_states(project_leads, *, user=None) -> list[dict]:
    return [sync_campaign_state(pl, user=user) for pl in project_leads]


def sync_event_update(project_lead: ProjectLead, event: OutreachEvent, *, user=None) -> dict:
    if event.synced_to_monday:
        return {'ok': True, 'action': 'already_synced', 'item_id': project_lead.monday_item_id}

    project_sync = sync_project_lead(project_lead, user=user)
    if not project_sync.get('ok'):
        return {'ok': False, 'error': project_sync.get('error') or project_sync.get('skipped') or 'lead sync failed'}
    if not project_lead.monday_item_id:
        return {'ok': False, 'error': 'project lead has no monday item id'}

    sync_user = _sync_user(project_lead.project, explicit_user=user)
    if not sync_user:
        return {'ok': False, 'error': 'no Monday user token available'}

    body = _build_monday_thread_update(project_lead, event)
    if not body:
        return {'ok': False, 'error': 'no update body available'}

    try:
        monday_client.create_update(sync_user, project_lead.monday_item_id, body)
        event.synced_to_monday = True
        event.synced_at = timezone.now()
        event.save(update_fields=['synced_to_monday', 'synced_at'])
        return {'ok': True, 'action': 'update_created', 'item_id': project_lead.monday_item_id}
    except Exception as exc:  # noqa: BLE001
        log.warning('Monday item update failed for OutreachEvent %s: %s', event.pk, exc)
        return {'ok': False, 'error': str(exc)}


def sync_project_leads(project_leads, *, user=None) -> list[dict]:
    return [sync_project_lead(pl, user=user) for pl in project_leads]


def sync_project(project: Project, *, user=None) -> dict:
    rows = list(project.project_leads.select_related('project', 'lead').all())
    results = sync_project_leads(rows, user=user)
    ok = sum(1 for r in results if r.get('ok'))
    failed = len(results) - ok
    created = sum(1 for r in results if r.get('action') == 'created')
    updated = sum(1 for r in results if r.get('action') == 'updated')
    warning_count = sum(1 for r in results if r.get('warnings'))
    return {
        'ok': True,
        'total': len(results),
        'created': created,
        'updated': updated,
        'failed': failed,
        'warnings': warning_count,
        'results': results,
    }


def sync_lead_everywhere(lead: Lead, *, user=None) -> list[dict]:
    results: list[dict] = []

    board_id, item_id = _lead_origin_monday_ref(lead)
    if board_id and item_id and user and _has_monday_token(user):
        try:
            columns = _board_columns(user, board_id)
            origin_values = {}
            if columns.get('contact_name'):
                origin_values[columns['contact_name']] = _item_name_for_lead(lead)
            if columns.get('organization'):
                origin_values[columns['organization']] = lead.organization or ''
            if columns.get('role_specialty'):
                origin_values[columns['role_specialty']] = _role_specialty(lead)
            if columns.get('classification'):
                origin_values[columns['classification']] = {'label': _classification_label(lead)}
            if columns.get('email') and lead.email:
                origin_values[columns['email']] = {'email': lead.email, 'text': lead.email}
            if columns.get('organization_email') and lead.organization_email:
                origin_values[columns['organization_email']] = {'email': lead.organization_email, 'text': lead.organization_email}
            if origin_values:
                monday_client.change_multiple_column_values(user, board_id, item_id, origin_values)
                results.append({'ok': True, 'action': 'updated_origin_item', 'item_id': item_id})
        except Exception as exc:  # noqa: BLE001
            log.warning('Monday origin update failed for Lead %s: %s', lead.pk, exc)
            results.append({'ok': False, 'error': str(exc)})

    related = (
        lead.project_leads.select_related('project', 'lead')
        .all()
    )
    for project_lead in related:
        results.append(sync_project_lead(project_lead, user=user))
    return results


BRIDGE_BOARD_COLUMNS = [
    ('contact_name', 'Contact Name', 'text'),
    ('organization', 'Organization', 'text'),
    ('role_specialty', 'Role / Specialty', 'text'),
    ('classification', 'Contact Type', 'status'),
    ('email', 'Email', 'email'),
    ('organization_email', 'Organization Email', 'email'),
    ('source_directory', 'Source Directory', 'status'),
    ('campaign_status', 'Campaign Status', 'status'),
    ('sequence_step', 'Sequence Step', 'text'),
    ('human_action_needed', 'Human Action Needed', 'status'),
    ('reply_intent', 'Reply Intent', 'status'),
    ('next_action', 'Next Action', 'text'),
    ('campaign_name', 'Campaign Name', 'text'),
    ('last_event_type', 'Last Event Type', 'text'),
    ('last_event', 'Last Event', 'date'),
    ('interest_level', 'Interest Level', 'status'),
    ('client_visible', 'Client Visible', 'checkbox'),
    ('referral_link', 'Referral Link', 'link'),
    ('referred_count', 'Referred Count', 'numbers'),
    ('notes', 'Notes', 'long_text'),
]


def _column_defaults(title: str) -> dict | None:
    if title == 'Contact Type':
        return {'labels': CLASSIFICATION_LABELS}
    if title == 'Source Directory':
        return {'labels': SOURCE_DIRECTORY_LABELS}
    if title == 'Campaign Status':
        return {'labels': CAMPAIGN_STATUS_LABELS}
    if title == 'Human Action Needed':
        return {'labels': HUMAN_ACTION_LABELS}
    if title == 'Reply Intent':
        return {'labels': REPLY_INTENT_LABELS}
    if title == 'Interest Level':
        return {'labels': INTEREST_LEVEL_LABELS}
    return None


def provision_project_board(project: Project, *, user=None) -> dict:
    sync_user = _sync_user(project, explicit_user=user)
    if not sync_user:
        return {'ok': False, 'error': 'No Monday user token available'}
    if project.monday_board_id:
        return {'ok': True, 'board_id': project.monday_board_id, 'created': False}

    board_name = f'{project.study_code} Bridge Outreach'
    created = monday_client.create_board(sync_user, name=board_name)
    board_id = str(created.get('id') or '')
    if not board_id:
        return {'ok': False, 'error': 'Monday did not return a board id'}

    column_errors = []
    for _, title, column_type in BRIDGE_BOARD_COLUMNS:
        try:
            monday_client.create_column(sync_user, board_id, title=title, column_type=column_type, defaults=_column_defaults(title))
        except Exception as exc:  # noqa: BLE001
            column_errors.append(f'{title}: {exc}')
    _ensure_groups(sync_user, board_id)

    project.monday_board_id = board_id
    project.save(update_fields=['monday_board_id', 'updated_at'])
    return {'ok': True, 'board_id': board_id, 'created': True, 'column_errors': column_errors}


def pull_project_board_statuses(project: Project, *, user=None) -> dict:
    board_id = str(project.monday_board_id or '')
    if not board_id:
        return {'ok': False, 'error': 'Project has no Monday board'}

    sync_user = _sync_user(project, explicit_user=user)
    if not sync_user:
        return {'ok': False, 'error': 'No Monday user token available'}

    payload = monday_client.list_board_items(sync_user, board_id, limit=500)
    columns = monday_client.bridge_column_map(payload.get('columns') or [])
    items = payload.get('items') or []

    status_updates = 0
    changed_ids: list[int] = []
    interest_col = columns.get('interest_level')
    campaign_col = columns.get('campaign_status')
    reply_intent_col = columns.get('reply_intent')
    human_action_col = columns.get('human_action_needed')

    by_item_id = {
        str(pl.monday_item_id): pl
        for pl in project.project_leads.select_related('lead').all()
        if pl.monday_item_id
    }

    for item in items:
        project_lead = by_item_id.get(str(item.get('id') or ''))
        if not project_lead:
            continue

        cvs = {
            cv.get('column', {}).get('id'): (cv.get('text') or '').strip()
            for cv in item.get('column_values') or []
        }
        interest = (cvs.get(interest_col) or '').strip().lower()
        campaign_status = (cvs.get(campaign_col) or '').strip().lower()
        reply_intent = (cvs.get(reply_intent_col) or '').strip().lower()
        human_action = (cvs.get(human_action_col) or '').strip().lower()
        next_status = ''

        if interest in ('not interested', 'not_interested') or reply_intent == 'not interested':
            next_status = ProjectLead.STATUS_NOT_INTERESTED
        elif interest == 'interested' or reply_intent == 'interested' or human_action == 'connect study team':
            next_status = ProjectLead.STATUS_INTERESTED
        elif campaign_status in ('unsubscribed', 'opted out'):
            next_status = ProjectLead.STATUS_UNSUBSCRIBED

        if next_status and project_lead.campaign_status != next_status:
            project_lead.campaign_status = next_status
            project_lead.save(update_fields=['campaign_status', 'updated_at'])
            if next_status in (ProjectLead.STATUS_NOT_INTERESTED, ProjectLead.STATUS_UNSUBSCRIBED):
                lead = project_lead.lead
                if not lead.global_opt_out:
                    lead.global_opt_out = True
                    lead.do_not_contact_reason = 'Marked as not interested from Monday board'
                    lead.save(update_fields=['global_opt_out', 'do_not_contact_reason', 'updated_at'])
            status_updates += 1
            changed_ids.append(project_lead.pk)

    return {'ok': True, 'items_checked': len(items), 'status_updates': status_updates, 'project_lead_ids': changed_ids}


def project_dashboard_snapshot(project: Project) -> dict:
    project_leads = project.project_leads.select_related('lead').all()
    total = project_leads.count()
    with_email = project_leads.exclude(lead__email__isnull=True).exclude(lead__email='').count()
    on_monday = project_leads.exclude(monday_item_id='').count()
    status_counts = {
        row['campaign_status']: row['n']
        for row in project_leads.values('campaign_status').annotate(n=Count('id'))
    }
    events = OutreachEvent.objects.filter(project_lead__project=project)
    sent = events.filter(event_type=OutreachEvent.EVENT_EMAIL_SENT).count()
    opened = events.filter(event_type=OutreachEvent.EVENT_EMAIL_OPENED).count()
    replied = events.filter(event_type=OutreachEvent.EVENT_EMAIL_REPLIED).count()
    bounced = events.filter(event_type=OutreachEvent.EVENT_EMAIL_BOUNCED).count()
    opted_out = events.filter(event_type=OutreachEvent.EVENT_UNSUBSCRIBED).count()
    denominator = sent or 1
    return {
        'total_leads': total,
        'with_email': with_email,
        'on_monday': on_monday,
        'campaign_count': project.campaigns.count(),
        'active_campaign_count': project.campaigns.filter(status='active').count(),
        'sent': sent,
        'opened': opened,
        'replied': replied,
        'bounced': bounced,
        'opted_out': opted_out,
        'open_rate': round((opened / denominator) * 100, 1) if sent else 0,
        'reply_rate': round((replied / denominator) * 100, 1) if sent else 0,
        'bounce_rate': round((bounced / denominator) * 100, 1) if sent else 0,
        'status_counts': status_counts,
        'recent_project_leads': list(project_leads.order_by('-updated_at')[:10]),
    }
