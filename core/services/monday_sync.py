from __future__ import annotations

import logging

from django.conf import settings

from accounts.models import ClientProfile
from core.models import Lead, Project, ProjectLead
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
    return project_lead.get_campaign_status_display()


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
