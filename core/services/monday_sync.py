from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import Count, Q

from accounts.models import ClientProfile
from core.models import Lead, Project, ProjectLead, OutreachEvent
from integrations import monday_client

log = logging.getLogger(__name__)


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


def _role_specialty(lead: Lead) -> str:
    bits = [b.strip() for b in [lead.role, lead.specialty] if b and str(b).strip()]
    return ' | '.join(bits)[:255]


def _referral_link(project_lead: ProjectLead) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/r/{project_lead.tracking_token}"


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


def _column_values(project_lead: ProjectLead, columns: dict) -> dict:
    lead = project_lead.lead
    values: dict = {}
    if columns.get('contact_name'):
        values[columns['contact_name']] = _item_name_for_lead(lead)
    if columns.get('organization'):
        values[columns['organization']] = lead.organization or ''
    if columns.get('role_specialty'):
        values[columns['role_specialty']] = _role_specialty(lead)
    if columns.get('email') and lead.email:
        values[columns['email']] = {'email': lead.email, 'text': lead.email}
    if columns.get('source_directory'):
        values[columns['source_directory']] = {'label': lead.get_source_display()}
    if columns.get('campaign_status'):
        values[columns['campaign_status']] = {'label': _status_label(project_lead)}
    latest_event = project_lead.events.order_by('-timestamp').first()
    if columns.get('last_event') and latest_event:
        values[columns['last_event']] = {'date': latest_event.timestamp.date().isoformat()}
    if columns.get('interest_level'):
        label = _interest_label(project_lead)
        if label:
            values[columns['interest_level']] = {'label': label}
    if columns.get('referral_link'):
        values[columns['referral_link']] = _referral_link(project_lead)
    if columns.get('referred_count'):
        values[columns['referred_count']] = project_lead.referred_count
    if columns.get('notes') and lead.do_not_contact_reason:
        values[columns['notes']] = lead.do_not_contact_reason
    elif columns.get('notes'):
        latest_event = project_lead.events.order_by('-timestamp').first()
        if latest_event:
            values[columns['notes']] = f'Latest Bridge event: {latest_event.get_event_type_display()}'
    return values


def _board_columns(user, board_id: str) -> dict:
    board = monday_client.get_board(user, board_id)
    return monday_client.bridge_column_map(board.get('columns') or [])


def sync_project_lead(project_lead: ProjectLead, *, user=None) -> dict:
    project = project_lead.project
    board_id = str(project.monday_board_id or '')
    if not board_id:
        return {'ok': False, 'skipped': 'project has no monday_board_id'}

    sync_user = _sync_user(project, explicit_user=user)
    if not sync_user:
        return {'ok': False, 'skipped': 'no Monday user token available'}

    _attach_origin_item_if_same_board(project_lead)
    columns = _board_columns(sync_user, board_id)
    item_name = _item_name_for_lead(project_lead.lead)
    values = _column_values(project_lead, columns)

    try:
        if project_lead.monday_item_id:
            monday_client.change_multiple_column_values(sync_user, board_id, project_lead.monday_item_id, values)
            action = 'updated'
        else:
            created = monday_client.create_item(sync_user, board_id, item_name=item_name, column_values=values)
            item_id = str(created.get('id') or '')
            if item_id:
                project_lead.monday_item_id = item_id
                project_lead.save(update_fields=['monday_item_id', 'updated_at'])
            action = 'created'
        return {'ok': True, 'action': action, 'item_id': project_lead.monday_item_id}
    except Exception as exc:  # noqa: BLE001
        log.warning('Monday sync failed for ProjectLead %s: %s', project_lead.pk, exc)
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
    return {'ok': True, 'total': len(results), 'created': created, 'updated': updated, 'failed': failed, 'results': results}


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
            if columns.get('email') and lead.email:
                origin_values[columns['email']] = {'email': lead.email, 'text': lead.email}
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
    ('Contact Name', 'text'),
    ('Organization', 'text'),
    ('Role / Specialty', 'text'),
    ('Email', 'email'),
    ('Source Directory', 'status'),
    ('Campaign Status', 'status'),
    ('Last Event', 'date'),
    ('Interest Level', 'status'),
    ('Referral Link', 'link'),
    ('Referred Count', 'numbers'),
    ('Notes', 'long_text'),
]


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
    for title, column_type in BRIDGE_BOARD_COLUMNS:
        try:
            monday_client.create_column(sync_user, board_id, title=title, column_type=column_type)
        except Exception as exc:  # noqa: BLE001
            column_errors.append(f'{title}: {exc}')

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
        next_status = ''

        if interest in ('not interested', 'not_interested'):
            next_status = ProjectLead.STATUS_NOT_INTERESTED
        elif interest == 'interested':
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
