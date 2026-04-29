"""Campaign orchestration — creation, Claude drafting, launch to Instantly.

A Campaign is a batch of outreach for a Project targeting a specific set of
ProjectLeads. Lifecycle: draft → awaiting_approval → active (pushed to Instantly)
→ completed. Each step's subject/body is drafted by Claude from approved study
materials and edited/approved by a human before launch (IRB compliance).
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import Campaign, Lead, Project, ProjectLead, StudyAsset
from integrations import ai_sourcing as ai_sourcing_client
from integrations import instantly as instantly_client
from . import monday_sync


def _recipient_type(lead: Lead) -> str:
    if lead.first_name or lead.last_name:
        return 'named_person'
    if lead.organization_email:
        return 'generic_org_inbox'
    return 'organization_only'


def _formal_salutation(lead: Lead) -> str:
    role_text = (lead.role or '').lower()
    last_name = (lead.last_name or '').strip()
    first_name = (lead.first_name or '').strip()
    organization = (lead.organization or '').strip()

    if last_name:
        if 'professor' in role_text or role_text.startswith('prof ') or ' prof.' in role_text:
            return f'Prof. {last_name}'
        if (
            'dr' in role_text
            or 'physician' in role_text
            or 'medical' in role_text
            or 'geneticist' in role_text
            or 'neurolog' in role_text
            or 'pediatric' in role_text
        ):
            return f'Dr. {last_name}'
        return first_name or last_name
    if organization:
        return f'{organization} team'
    return 'there'


def _greeting_name(lead: Lead) -> str:
    if lead.first_name:
        return lead.first_name.strip()
    if lead.organization:
        return f'{lead.organization} team'
    return 'there'


def _lead_personalization(lead: Lead, project_lead: ProjectLead, campaign: Campaign) -> dict:
    return {
        'project_id': campaign.project_id,
        'campaign_id': campaign.pk,
        'lead_id': lead.pk,
        'tracking_token': project_lead.tracking_token,
        'first_name': (lead.first_name or '').strip(),
        'last_name': (lead.last_name or '').strip(),
        'organization_name': (lead.organization or '').strip(),
        'role': (lead.role or '').strip(),
        'specialty': (lead.specialty or '').strip(),
        'recipient_type': _recipient_type(lead),
        'formal_salutation': _formal_salutation(lead),
        'greeting_name': _greeting_name(lead),
    }


def get_landing_page_url(project: Project) -> str:
    """Pull the approved landing-page URL from the project's StudyAssets."""
    asset = project.assets.filter(type=StudyAsset.TYPE_LANDING_PAGE).order_by('-approved_at', '-created_at').first()
    if asset and asset.content_url:
        return asset.content_url
    return ''


def _project_asset_texts(project: Project) -> list[str]:
    texts = []
    for a in project.assets.all():
        if a.content_text:
            tag = f'{a.get_type_display()}'
            if a.subject:
                tag += f': {a.subject}'
            texts.append(f'[{tag}]\n{a.content_text}')
    return texts


@transaction.atomic
def create_campaign_from_leads(
    project: Project,
    *,
    name: str,
    project_lead_ids: list[int],
    user=None,
) -> Campaign:
    """Create a Campaign and attach the selected ProjectLeads to it.

    Leads already on another active/completed campaign in this project are skipped
    — we don't double-enroll. ProjectLeads not belonging to this project are ignored.
    """
    campaign = Campaign.objects.create(
        project=project,
        name=name,
        status=Campaign.STATUS_DRAFT,
    )

    attachable = ProjectLead.objects.filter(
        project=project,
        pk__in=project_lead_ids,
        campaign__isnull=True,
    )
    attachable.update(campaign=campaign)
    return campaign


def draft_sequence(campaign: Campaign, *, user=None) -> Campaign:
    """Ask Claude to draft the 3-step sequence using the project's assets + profile."""
    project = campaign.project
    profile = getattr(project, 'partner_profile', None)
    if not profile:
        raise RuntimeError('Project needs a Partner Profile before drafting a sequence.')

    landing_page = get_landing_page_url(project)
    asset_texts = _project_asset_texts(project)
    if not asset_texts:
        raise RuntimeError('Project has no uploaded study assets with text content to draft from.')

    steps = ai_sourcing_client.draft_email_sequence(
        project_name=project.name,
        study_code=project.study_code,
        asset_texts=asset_texts,
        profile=profile,
        landing_page_url=landing_page,
        user=user,
    )
    if not steps:
        raise RuntimeError('Claude returned no steps — try again or adjust the partner profile.')
    if not all((step.get('subject') or '').strip() and (step.get('body') or '').strip() for step in steps[:3]):
        raise RuntimeError('Bridge could not generate a complete sequence draft — please try redraft again.')

    campaign.sequence_config = steps
    campaign.status = Campaign.STATUS_AWAITING_APPROVAL
    campaign.save(update_fields=['sequence_config', 'status', 'updated_at'])
    return campaign


def update_sequence(campaign: Campaign, *, steps: list[dict]) -> Campaign:
    """Persist edits the operator made to the draft."""
    campaign.sequence_config = steps
    campaign.save(update_fields=['sequence_config', 'updated_at'])
    return campaign


def launch_campaign(
    campaign: Campaign,
    *,
    sending_account_emails: list[str],
    user=None,
) -> dict:
    """Create the campaign on Instantly, push the attached leads, mark active.

    Returns a summary dict: {ok, campaign_id, pushed, skipped, errors}.
    """
    is_retrying_created_campaign = bool(campaign.instantly_campaign_id) and campaign.status == Campaign.STATUS_ACTIVE
    if campaign.status not in (Campaign.STATUS_DRAFT, Campaign.STATUS_AWAITING_APPROVAL, Campaign.STATUS_PAUSED) and not is_retrying_created_campaign:
        return {'ok': False, 'error': f'Campaign is {campaign.status} — cannot launch.'}
    if not campaign.sequence_config:
        return {'ok': False, 'error': 'Sequence is empty. Draft it first.'}
    if not sending_account_emails:
        return {'ok': False, 'error': 'Pick at least one sending account.'}

    # Only launch steps that have non-empty subject+body.
    clean_steps = [
        {
            'subject': s.get('subject', '').strip(),
            'body': s.get('body', '').strip(),
            'delay_days': int(s.get('delay_days') or 0),
        }
        for s in campaign.sequence_config
        if (s.get('subject') or '').strip() and (s.get('body') or '').strip()
    ]
    if not clean_steps:
        return {'ok': False, 'error': 'No step has both a subject and body.'}

    # Resolve leads to push (exclude opt-outs, require email).
    project_leads = campaign.project_leads.select_related('lead').all()
    payload_leads = []
    skipped_no_email = 0
    skipped_opt_out = 0
    for pl in project_leads:
        lead: Lead = pl.lead
        if not lead.email:
            skipped_no_email += 1
            continue
        if lead.global_opt_out:
            skipped_opt_out += 1
            continue
        payload_leads.append({
            'email': lead.email,
            'first_name': lead.first_name,
            'last_name': lead.last_name,
            'organization': lead.organization,
            'custom_variables': _lead_personalization(lead, pl, campaign),
        })
    if not payload_leads:
        return {
            'ok': False,
            'error': f'No sendable leads (skipped {skipped_no_email} without email, {skipped_opt_out} opted out).',
        }

    # 1) Create the Instantly campaign, or reuse the created campaign during a retry.
    instantly_campaign_id = str(campaign.instantly_campaign_id or '')
    if not instantly_campaign_id:
        try:
            created = instantly_client.create_campaign(
                name=campaign.name,
                sequence_steps=clean_steps,
                sending_account_emails=sending_account_emails,
            )
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'error': f'Instantly create_campaign failed: {exc}'}

        instantly_campaign_id = created['id']

    # 2) Push leads
    try:
        push_result = instantly_client.push_leads(
            leads=payload_leads,
            campaign_id=instantly_campaign_id,
        )
    except Exception as exc:  # noqa: BLE001
        campaign.instantly_campaign_id = str(instantly_campaign_id)
        campaign.save(update_fields=['instantly_campaign_id', 'updated_at'])
        return {
            'ok': False,
            'error': f'Instantly campaign was created ({instantly_campaign_id}) but lead push failed: {exc}',
        }
    if push_result.get('errors') and not push_result.get('pushed'):
        campaign.instantly_campaign_id = str(instantly_campaign_id)
        campaign.status = Campaign.STATUS_AWAITING_APPROVAL
        campaign.save(update_fields=['instantly_campaign_id', 'status', 'updated_at'])
        return {
            'ok': False,
            'error': f'Instantly campaign was created ({instantly_campaign_id}) but no leads were pushed: {"; ".join(push_result["errors"])[:500]}',
        }

    # 3) Mark campaign active + leads queued
    try:
        with transaction.atomic():
            campaign.instantly_campaign_id = str(instantly_campaign_id)
            campaign.status = Campaign.STATUS_ACTIVE
            campaign.started_at = timezone.now()
            campaign.save(update_fields=['instantly_campaign_id', 'status', 'started_at', 'updated_at'])

            ProjectLead.objects.filter(campaign=campaign, lead__email__in=[pl['email'] for pl in payload_leads]).update(
                campaign_status=ProjectLead.STATUS_QUEUED,
            )
    except Exception as exc:  # noqa: BLE001
        return {
            'ok': False,
            'error': f'Instantly campaign was created ({instantly_campaign_id}) but Bridge status update failed: {exc}',
        }

    try:
        monday_results = monday_sync.sync_project_leads(
            campaign.project_leads.select_related('project', 'lead').all(),
            user=user,
        )
        monday_errors = [r.get('error') or r.get('skipped') for r in monday_results if not r.get('ok')]
        if monday_errors:
            push_errors = push_result.get('errors', [])
            push_errors.append(f'Monday sync warnings after launch: {"; ".join(str(e) for e in monday_errors[:3])[:300]}')
            push_result['errors'] = push_errors
    except Exception as exc:  # noqa: BLE001
        push_errors = push_result.get('errors', [])
        push_errors.append(f'Monday sync failed after launch: {exc}')
        push_result['errors'] = push_errors

    return {
        'ok': True,
        'campaign_id': instantly_campaign_id,
        'pushed': push_result.get('pushed', 0),
        'push_errors': push_result.get('errors', []),
        'skipped_no_email': skipped_no_email,
        'skipped_opt_out': skipped_opt_out,
    }
