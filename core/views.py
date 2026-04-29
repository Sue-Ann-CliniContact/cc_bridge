import csv
import io
import json

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from integrations import ai_sourcing as ai_sourcing_client
from integrations import apollo as apollo_client
from integrations import instantly as instantly_client
from integrations import monday_client

from .forms import LeadEditForm, PartnerProfileForm, ProjectForm, StudyAssetForm
from .models import Campaign, Lead, OptOut, OutreachEvent, PartnerProfile, Project, ProjectLead, StudyAsset
from .services import campaigns as campaigns_service
from .services import client_portal
from .services import monday_sync
from .services import sourcing


def _project_tab_redirect(project_id, tab='overview'):
    return redirect(f"{reverse('project_detail', args=[project_id])}?tab={tab}")


def _project_redirect_from_request(request, project_id, default_tab='overview'):
    tab = (request.POST.get('next_tab') or request.GET.get('tab') or default_tab).strip() or default_tab
    return _project_tab_redirect(project_id, tab=tab)


@login_required
def dashboard(request):
    return _render_dashboard(request, portal_mode=False)


@login_required
def client_dashboard(request):
    return _render_dashboard(request, portal_mode=True)


def _render_dashboard(request, *, portal_mode: bool):
    projects = client_portal.visible_projects_for_user(request.user)
    workspace_snapshot = client_portal.workspace_portal_snapshot(projects, user=request.user, assigned_only=portal_mode)
    return render(request, 'core/dashboard.html', {
        'projects': projects,
        'workspace_snapshot': workspace_snapshot,
        'is_operator': any(client_portal.is_operator(request.user, project) for project in projects) or request.user.is_staff,
        'portal_mode': portal_mode,
        'project_url_name': 'client_project_detail' if portal_mode else 'project_detail',
        'dashboard_ai_url': reverse('client_dashboard_ai' if portal_mode else 'dashboard_ai'),
    })


@login_required
def project_create(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.save()
            return redirect('project_detail', project_id=project.pk)
    else:
        form = ProjectForm()
    return render(request, 'core/project_form.html', {'form': form})


@login_required
def project_detail(request, project_id):
    return _render_project_detail(request, project_id, portal_mode=False)


@login_required
def client_project_detail(request, project_id):
    return _render_project_detail(request, project_id, portal_mode=True)


def _render_project_detail(request, project_id, *, portal_mode: bool):
    project = get_object_or_404(Project, pk=project_id)
    if not client_portal.user_can_access_project(request.user, project):
        return HttpResponseForbidden('You do not have access to this project.')
    asset_form = StudyAssetForm()
    monday_dashboard = monday_sync.project_dashboard_snapshot(project)
    client_snapshot = client_portal.project_client_snapshot(project, user=request.user, assigned_only=portal_mode)
    return render(request, 'core/project_detail.html', {
        'project': project,
        'assets': project.assets.all(),
        'asset_form': asset_form,
        'campaigns': project.campaigns.order_by('-created_at'),
        'monday_dashboard': monday_dashboard,
        'client_snapshot': client_snapshot,
        'is_operator': client_portal.is_operator(request.user, project),
        'active_tab': request.GET.get('tab', 'overview'),
        'portal_mode': portal_mode,
        'dashboard_url': reverse('client_dashboard' if portal_mode else 'dashboard'),
        'project_ai_url': reverse('client_project_ai' if portal_mode else 'project_ai', args=[project.pk]),
    })


@login_required
@require_POST
def asset_upload(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    form = StudyAssetForm(request.POST, request.FILES)
    if form.is_valid():
        asset = form.save(commit=False)
        asset.project = project
        asset.save()
    return _project_redirect_from_request(request, project.pk)


@login_required
@require_POST
def asset_approve(request, asset_id):
    asset = get_object_or_404(StudyAsset, pk=asset_id)
    asset.approved_by = request.user
    asset.approved_at = timezone.now()
    asset.save(update_fields=['approved_by', 'approved_at', 'updated_at'])
    return _project_redirect_from_request(request, asset.project_id)


@login_required
@require_POST
def monday_provision_board(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    result = monday_sync.provision_project_board(project, user=request.user)
    if result.get('ok'):
        note = f"Provisioned Monday board {result.get('board_id')}." if result.get('created') else f"Using existing Monday board {result.get('board_id')}."
        messages.success(request, note)
        if result.get('column_errors'):
            messages.warning(request, 'Some columns could not be created: ' + '; '.join(result['column_errors'][:3]))
    else:
        messages.error(request, f"Monday board provisioning failed: {result.get('error', 'unknown error')}")
    return _project_redirect_from_request(request, project.pk, default_tab='operator')


@login_required
@require_POST
def monday_sync_project_view(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    result = monday_sync.sync_project(project, user=request.user)
    if result.get('ok'):
        messages.success(
            request,
            f"Monday sync complete: {result['created']} created, {result['updated']} updated, {result['failed']} failed."
        )
    else:
        messages.error(request, f"Monday sync failed: {result.get('error', 'unknown error')}")
    return _project_redirect_from_request(request, project.pk, default_tab='operator')


@login_required
@require_POST
def monday_pull_statuses_view(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    result = monday_sync.pull_project_board_statuses(project, user=request.user)
    if result.get('ok'):
        messages.success(
            request,
            f"Pulled Monday updates: checked {result['items_checked']} items, applied {result['status_updates']} status changes."
        )
    else:
        messages.error(request, f"Monday pull failed: {result.get('error', 'unknown error')}")
    return _project_redirect_from_request(request, project.pk, default_tab='operator')


@login_required
def test_instantly(request):
    """Phase 1 sanity check: does the configured Instantly key return campaigns?"""
    result = instantly_client.ping()
    return JsonResponse(result)


@login_required
@require_POST
def dashboard_ai(request):
    return _dashboard_ai_response(request, portal_mode=False)


@login_required
@require_POST
def client_dashboard_ai(request):
    return _dashboard_ai_response(request, portal_mode=True)


def _dashboard_ai_response(request, *, portal_mode: bool):
    question = (request.POST.get('question') or '').strip()
    if not question:
        return JsonResponse({'ok': False, 'error': 'Question is required.'}, status=400)
    projects = client_portal.visible_projects_for_user(request.user)
    try:
        answer = client_portal.answer_workspace_question(projects, question, user=request.user, assigned_only=portal_mode)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
    return JsonResponse({'ok': True, 'answer': answer})


@login_required
@require_POST
def project_ai(request, project_id):
    return _project_ai_response(request, project_id, portal_mode=False)


@login_required
@require_POST
def client_project_ai(request, project_id):
    return _project_ai_response(request, project_id, portal_mode=True)


def _project_ai_response(request, project_id, *, portal_mode: bool):
    project = get_object_or_404(Project, pk=project_id)
    if not client_portal.user_can_access_project(request.user, project):
        return JsonResponse({'ok': False, 'error': 'Forbidden'}, status=403)
    question = (request.POST.get('question') or '').strip()
    if not question:
        return JsonResponse({'ok': False, 'error': 'Question is required.'}, status=400)
    try:
        answer = client_portal.answer_project_question(project, question, user=request.user, assigned_only=portal_mode)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
    return JsonResponse({'ok': True, 'answer': answer})


@login_required
def partner_profile_edit(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    profile = getattr(project, 'partner_profile', None)
    suggestion = request.session.pop('suggested_profile', None) if request.method == 'GET' else None

    if request.method == 'POST':
        form = PartnerProfileForm(request.POST, instance=profile)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.project = project
            instance.save()
            return _project_redirect_from_request(request, project.pk, default_tab='operator')
    else:
        initial = {}
        if suggestion:
            initial = {
                'partner_type': suggestion.get('partner_type') or (profile.partner_type if profile else 'clinician'),
                'study_indication': suggestion.get('study_indication') or (profile.study_indication if profile else ''),
                'patient_population_description': suggestion.get('patient_population_description') or (profile.patient_population_description if profile else ''),
                'target_org_types_csv': ', '.join(suggestion.get('target_org_types') or []),
                'target_contact_roles_csv': ', '.join(suggestion.get('target_contact_roles') or []),
                'specialty_tags_csv': ', '.join(suggestion.get('specialty_tags') or []),
                'icd10_codes_csv': ', '.join(suggestion.get('icd10_codes') or []),
                'target_size': suggestion.get('target_size') or (profile.target_size if profile else 100),
            }
            geo = suggestion.get('geography') or {}
            initial['geography_mode'] = geo.get('type') or 'national'
            initial['geography_states_csv'] = ', '.join(geo.get('states') or [])
            initial['geography_zip'] = geo.get('zip') or ''
            initial['geography_radius_miles'] = geo.get('radius_miles')
        form = PartnerProfileForm(instance=profile, initial=initial)
    return render(request, 'core/partner_profile_form.html', {
        'project': project,
        'profile': profile,
        'form': form,
        'suggestion': suggestion,
    })


@login_required
@require_POST
def partner_profile_suggest(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    asset_texts = []
    for asset in project.assets.all():
        if asset.content_text:
            asset_texts.append(f'[{asset.get_type_display()}{": " + asset.subject if asset.subject else ""}]\n{asset.content_text}')
        elif asset.content_url:
            asset_texts.append(f'[{asset.get_type_display()}] URL: {asset.content_url}')
    try:
        suggestion = ai_sourcing_client.suggest_partner_profile(
            project_name=project.name,
            study_code=project.study_code,
            asset_texts=asset_texts,
            user=request.user,
        )
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f'AI suggestion failed: {exc}')
        return redirect('partner_profile_edit', project_id=project.pk)

    if not suggestion:
        messages.warning(request, 'AI did not return a usable suggestion. Try again or fill in manually.')
    else:
        request.session['suggested_profile'] = suggestion
        if suggestion.get('rationale'):
            messages.info(request, f"AI rationale: {suggestion['rationale']}")
    return redirect('partner_profile_edit', project_id=project.pk)


@login_required
def monday_import_picker(request, project_id):
    """Step 1: pick a Monday board to import from."""
    project = get_object_or_404(Project, pk=project_id)
    try:
        boards = monday_client.list_workspace_boards(request.user)
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f'Could not load Monday boards: {exc}')
        boards = []
    return render(request, 'core/monday_import_picker.html', {
        'project': project,
        'boards': boards,
    })


@login_required
def monday_import_preview(request, project_id, board_id):
    """Step 2: preview the board + confirm column mapping, then run import."""
    project = get_object_or_404(Project, pk=project_id)

    if request.method == 'POST':
        column_map = {
            key: request.POST.get(f'col_{key}', '').strip()
            for key in ('email', 'organization_email', 'first_name', 'last_name', 'organization', 'role', 'phone', 'specialty')
        }
        result = sourcing.import_from_monday_board(
            project,
            board_id=board_id,
            column_map=column_map,
            user=request.user,
        )
        _flash_sourcing_result(request, result)
        return redirect('lead_review', project_id=project.pk)

    try:
        payload = monday_client.list_board_items(request.user, board_id, limit=200)
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f'Could not load board: {exc}')
        return redirect('monday_import_picker', project_id=project.pk)

    column_map = monday_client.auto_map_columns(payload.get('columns') or [])
    return render(request, 'core/monday_import_preview.html', {
        'project': project,
        'board': payload.get('board') or {},
        'columns': payload.get('columns') or [],
        'items': payload.get('items') or [],
        'column_map': column_map,
        'mapped_keys': [
            ('email', 'Main contact email'),
            ('organization_email', 'Generic / organization email'),
            ('first_name', 'First name'),
            ('last_name', 'Last name'),
            ('organization', 'Organization'),
            ('role', 'Role / title'),
            ('phone', 'Phone'),
            ('specialty', 'Specialty'),
        ],
    })


@login_required
def lead_edit(request, lead_id):
    lead = get_object_or_404(Lead, pk=lead_id)
    if request.method == 'POST':
        form = LeadEditForm(request.POST, instance=lead)
        if form.is_valid():
            form.save()
            if lead.classification == Lead.CLASS_UNCLASSIFIED:
                sourcing.refresh_lead_classification(lead)
            monday_sync.sync_lead_everywhere(lead, user=request.user)
            messages.success(request, f'Updated {lead}.')
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect('lead_edit', lead_id=lead.pk)
    else:
        form = LeadEditForm(instance=lead)
    return render(request, 'core/lead_edit.html', {'lead': lead, 'form': form})


@login_required
def lead_conflicts(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    leads = Lead.objects.filter(pending_conflict__isnull=False).order_by('-updated_at')
    return render(request, 'core/lead_conflicts.html', {'project': project, 'leads': leads})


@login_required
@require_POST
def lead_resolve_conflict(request, lead_id):
    lead = get_object_or_404(Lead, pk=lead_id)
    action = request.POST.get('action', '')
    if action not in ('merge', 'skip'):
        messages.error(request, 'Unknown action')
    else:
        sourcing.resolve_conflict(lead, action)
        monday_sync.sync_lead_everywhere(lead, user=request.user)
        messages.success(request, f'{lead}: conflict {action}d.')
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'dashboard'
    return redirect(next_url)


@login_required
def lead_review(request, project_id):
    """Main Phase-2 workspace: review candidate Leads and bulk-add to the project."""
    project = get_object_or_404(Project, pk=project_id)
    already_on_project = ProjectLead.objects.filter(project=project, lead=OuterRef('pk'))

    qs = Lead.objects.annotate(on_project=Exists(already_on_project))
    source_filter = request.GET.get('source', '')
    classification_filter = request.GET.get('classification', '')
    has_email = request.GET.get('has_email', '')
    project_scope = request.GET.get('project_scope', '')
    hide_added = request.GET.get('hide_added', '1')
    query = request.GET.get('q', '').strip()

    if source_filter:
        qs = qs.filter(source=source_filter)
    if classification_filter:
        qs = qs.filter(classification=classification_filter)
    if has_email == '1':
        qs = qs.filter(email__isnull=False).exclude(email='')
    elif has_email == '0':
        qs = qs.filter(Q(email__isnull=True) | Q(email=''))
    if project_scope == 'on_project':
        qs = qs.filter(on_project=True)
    elif project_scope == 'not_on_project':
        qs = qs.filter(on_project=False)
    if hide_added == '1':
        qs = qs.filter(on_project=False)
    if query:
        qs = qs.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(organization__icontains=query)
            | Q(specialty__icontains=query)
            | Q(email__icontains=query)
        )
    qs = qs.exclude(global_opt_out=True).order_by('-created_at')

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))

    conflict_count = Lead.objects.filter(pending_conflict__isnull=False).count()

    return render(request, 'core/lead_review.html', {
        'project': project,
        'profile': getattr(project, 'partner_profile', None),
        'page': page,
        'apollo_configured': apollo_client.is_configured(),
        'apollo_remaining': apollo_client.budget_remaining() if apollo_client.is_configured() else None,
        'conflict_count': conflict_count,
        'filters': {
            'source': source_filter,
            'classification': classification_filter,
            'has_email': has_email,
            'project_scope': project_scope,
            'hide_added': hide_added,
            'q': query,
        },
        'source_choices': Lead.SOURCE_CHOICES,
        'classification_choices': Lead.CLASSIFICATION_CHOICES,
    })


@login_required
@require_POST
def source_leads_npi(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    limit = int(request.POST.get('limit') or 100)
    result = sourcing.source_from_npi(project, limit=limit)
    _flash_sourcing_result(request, result)
    return redirect('lead_review', project_id=project.pk)


@login_required
@require_POST
def source_leads_ai(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    limit = int(request.POST.get('limit') or 50)
    result = sourcing.source_from_ai(project, limit=limit, user=request.user)
    _flash_sourcing_result(request, result)
    return redirect('lead_review', project_id=project.pk)


@login_required
@require_POST
def add_leads_to_project(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    lead_ids = list(dict.fromkeys(int(x) for x in request.POST.getlist('lead_ids') if x.isdigit()))
    if not lead_ids:
        messages.warning(request, 'Select at least one lead.')
        return redirect('lead_review', project_id=project.pk)

    existing = set(ProjectLead.objects.filter(project=project, lead_id__in=lead_ids).values_list('lead_id', flat=True))
    to_create = [
        ProjectLead(project=project, lead_id=lead_id)
        for lead_id in lead_ids
        if lead_id not in existing
    ]
    ProjectLead.objects.bulk_create(to_create, ignore_conflicts=True)
    created_rows = list(
        ProjectLead.objects.filter(
            project=project,
            lead_id__in=[pl.lead_id for pl in to_create if pl.lead_id not in existing],
        )
        .select_related('project', 'lead')
    )
    monday_sync.sync_project_leads(created_rows, user=request.user)
    messages.success(request, f'Added {len(to_create)} lead{"s" if len(to_create) != 1 else ""} to {project.study_code}.')
    return redirect('lead_review', project_id=project.pk)


@login_required
@require_POST
def enrich_lead(request, lead_id):
    lead = get_object_or_404(Lead, pk=lead_id)
    result = sourcing.enrich_lead_with_apollo(lead, user=request.user)
    if result.get('ok'):
        monday_sync.sync_lead_everywhere(lead, user=request.user)
    return JsonResponse(result)


@login_required
@require_POST
def reclassify_lead(request, lead_id):
    lead = get_object_or_404(Lead, pk=lead_id)
    previous = lead.classification
    updated = sourcing.refresh_lead_classification(lead, overwrite=True)
    monday_sync.sync_lead_everywhere(lead, user=request.user)
    return JsonResponse({
        'ok': True,
        'classification': updated,
        'changed': updated != previous,
    })


@login_required
@require_POST
def find_org_contact(request, lead_id):
    """Fetch the org's contact URL and extract named contacts via Claude."""
    lead = get_object_or_404(Lead, pk=lead_id)
    result = sourcing.find_contact_from_org_page(lead, user=request.user)
    return JsonResponse(result)


@login_required
@require_POST
def web_find_org_contacts(request, lead_id):
    """Use Claude + Anthropic web_search to find org contacts when page fetch fails."""
    lead = get_object_or_404(Lead, pk=lead_id)
    result = sourcing.find_org_contacts_via_web(lead, user=request.user)
    return JsonResponse(result)


@login_required
@require_POST
def web_enrich_clinician(request, lead_id):
    """Use Claude + web_search to find a clinician's work email."""
    lead = get_object_or_404(Lead, pk=lead_id)
    result = sourcing.enrich_clinician_via_web(lead, user=request.user)
    if result.get('ok'):
        monday_sync.sync_lead_everywhere(lead, user=request.user)
    return JsonResponse(result)


# ──────────────────────────── Campaigns (Phase 3) ───────────────────────────

@login_required
def campaign_create(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    classification_filter = request.GET.get('classification', '')

    # Only offer ProjectLeads that (a) have an email and (b) aren't already on a campaign.
    available_qs = (
        ProjectLead.objects
        .filter(project=project, campaign__isnull=True)
        .exclude(lead__email__isnull=True)
        .exclude(lead__email='')
        .select_related('lead')
        .order_by('-created_at')
    )
    if classification_filter:
        available_qs = available_qs.filter(lead__classification=classification_filter)

    classification_counts = list(
        ProjectLead.objects
        .filter(project=project, campaign__isnull=True)
        .exclude(lead__email__isnull=True)
        .exclude(lead__email='')
        .values('lead__classification')
        .annotate(total=Count('id'))
        .order_by('lead__classification')
    )
    classification_totals = {
        row['lead__classification']: row['total']
        for row in classification_counts
    }
    classification_summaries = [
        {
            'value': value,
            'label': label,
            'total': classification_totals.get(value, 0),
        }
        for value, label in Lead.CLASSIFICATION_CHOICES
    ]

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip() or f'{project.study_code} outreach — {timezone.now():%Y-%m-%d}'
        lead_ids = [int(x) for x in request.POST.getlist('project_lead_ids') if x.isdigit()]
        if not lead_ids:
            messages.warning(request, 'Select at least one lead with an email.')
            return redirect('campaign_create', project_id=project.pk)

        campaign = campaigns_service.create_campaign_from_leads(
            project, name=name, project_lead_ids=lead_ids, user=request.user,
        )
        try:
            campaigns_service.draft_sequence(campaign, user=request.user)
            messages.success(request, f'Campaign created and AI sequence drafted ({campaign.project_leads.count()} leads).')
        except Exception as exc:  # noqa: BLE001
            messages.warning(request, f'Campaign created but sequence draft failed: {exc}. Try "Regenerate" on the detail page.')
        return redirect('campaign_detail', campaign_id=campaign.pk)

    return render(request, 'core/campaign_form.html', {
        'project': project,
        'available_leads': available_qs,
        'suggested_name': f'{project.study_code} outreach — {timezone.now():%Y-%m-%d}',
        'classification_filter': classification_filter,
        'classification_choices': Lead.CLASSIFICATION_CHOICES,
        'classification_summaries': classification_summaries,
    })


@login_required
def campaign_detail(request, campaign_id):
    campaign = get_object_or_404(Campaign.objects.select_related('project'), pk=campaign_id)
    project_leads = campaign.project_leads.select_related('lead').order_by('-created_at')
    editable_statuses = {Campaign.STATUS_DRAFT, Campaign.STATUS_AWAITING_APPROVAL, Campaign.STATUS_PAUSED}
    can_manage_leads = campaign.status in editable_statuses
    available_project_leads = (
        ProjectLead.objects
        .filter(project=campaign.project, campaign__isnull=True)
        .exclude(lead__email__isnull=True)
        .exclude(lead__email='')
        .select_related('lead')
        .order_by('-created_at')
    ) if can_manage_leads else ProjectLead.objects.none()

    sending_accounts = []
    if django_settings.INSTANTLY_API_KEY:
        try:
            sending_accounts = instantly_client.get_sending_accounts()
        except Exception as exc:  # noqa: BLE001
            messages.warning(request, f'Could not load Instantly sending accounts: {exc}')

    return render(request, 'core/campaign_detail.html', {
        'campaign': campaign,
        'project': campaign.project,
        'project_leads': project_leads,
        'available_project_leads': available_project_leads,
        'can_manage_leads': can_manage_leads,
        'sending_accounts': sending_accounts,
        'sequence_steps': campaign.sequence_config or [],
    })


@login_required
@require_POST
def campaign_redraft(request, campaign_id):
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    try:
        campaigns_service.draft_sequence(campaign, user=request.user)
        messages.success(request, 'Sequence redrafted by Claude.')
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f'Redraft failed: {exc}')
    return redirect('campaign_detail', campaign_id=campaign.pk)


@login_required
@require_POST
def campaign_update_sequence(request, campaign_id):
    """Save edits from the sequence editor."""
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    existing = campaign.sequence_config or []
    steps = []
    for i, existing_step in enumerate(existing):
        subject = (request.POST.get(f'step_{i}_subject') or '').strip()
        body = (request.POST.get(f'step_{i}_body') or '').strip()
        delay_raw = request.POST.get(f'step_{i}_delay_days') or '0'
        try:
            delay = int(delay_raw)
        except ValueError:
            delay = 0
        approved = request.POST.get(f'step_{i}_approved') == 'on'
        steps.append({
            **existing_step,
            'step_num': existing_step.get('step_num') or (i + 1),
            'subject': subject,
            'body': body,
            'delay_days': delay,
            'approved': approved,
        })
    campaigns_service.update_sequence(campaign, steps=steps)
    messages.success(request, 'Sequence saved.')
    return redirect('campaign_detail', campaign_id=campaign.pk)


@login_required
@require_POST
def campaign_add_leads(request, campaign_id):
    campaign = get_object_or_404(Campaign.objects.select_related('project'), pk=campaign_id)
    if campaign.status not in (Campaign.STATUS_DRAFT, Campaign.STATUS_AWAITING_APPROVAL, Campaign.STATUS_PAUSED):
        messages.error(request, 'Leads can only be changed while the campaign is still editable.')
        return redirect('campaign_detail', campaign_id=campaign.pk)

    project_lead_ids = sorted({int(x) for x in request.POST.getlist('project_lead_ids') if x.isdigit()})
    if not project_lead_ids:
        messages.warning(request, 'Select at least one project lead to add.')
        return redirect('campaign_detail', campaign_id=campaign.pk)

    try:
        attachable = list(
            ProjectLead.objects
            .filter(project=campaign.project, pk__in=project_lead_ids, campaign__isnull=True)
            .select_related('project', 'lead')
        )
        if not attachable:
            messages.warning(request, 'Those leads are already attached or no longer available for this campaign.')
            return redirect('campaign_detail', campaign_id=campaign.pk)

        attachable_ids = [pl.pk for pl in attachable]
        with transaction.atomic():
            ProjectLead.objects.filter(pk__in=attachable_ids).update(campaign=campaign)

        updated_rows = list(
            ProjectLead.objects
            .filter(pk__in=attachable_ids)
            .select_related('project', 'lead', 'campaign')
        )
        if updated_rows:
            monday_sync.sync_project_leads(updated_rows, user=request.user)
        messages.success(request, f'Added {len(updated_rows)} lead{"s" if len(updated_rows) != 1 else ""} to {campaign.name}.')
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f'Could not add leads to the campaign: {exc}')
    return redirect('campaign_detail', campaign_id=campaign.pk)


@login_required
@require_POST
def campaign_remove_lead(request, campaign_id, project_lead_id):
    campaign = get_object_or_404(Campaign.objects.select_related('project'), pk=campaign_id)
    if campaign.status not in (Campaign.STATUS_DRAFT, Campaign.STATUS_AWAITING_APPROVAL, Campaign.STATUS_PAUSED):
        messages.error(request, 'Leads can only be removed while the campaign is still editable.')
        return redirect('campaign_detail', campaign_id=campaign.pk)

    project_lead = get_object_or_404(
        ProjectLead.objects.select_related('project', 'lead'),
        pk=project_lead_id,
        campaign=campaign,
    )
    project_lead.campaign = None
    project_lead.campaign_status = ProjectLead.STATUS_QUEUED
    project_lead.save(update_fields=['campaign', 'campaign_status', 'updated_at'])
    monday_sync.sync_project_lead(project_lead, user=request.user)
    messages.success(request, f'Removed {project_lead.lead} from {campaign.name}.')
    return redirect('campaign_detail', campaign_id=campaign.pk)


@login_required
@require_POST
def campaign_launch(request, campaign_id):
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    sending_emails = request.POST.getlist('sending_account_email')
    sending_emails = [e.strip() for e in sending_emails if e and e.strip()]
    result = campaigns_service.launch_campaign(
        campaign,
        sending_account_emails=sending_emails,
        user=request.user,
    )
    if not result.get('ok'):
        messages.error(request, f'Launch failed: {result.get("error", "unknown error")}')
        return redirect('campaign_detail', campaign_id=campaign.pk)
    push_errors = result.get('push_errors') or []
    err_note = f' · push errors: {"; ".join(push_errors)[:200]}' if push_errors else ''
    messages.success(
        request,
        f'Launched on Instantly (id {result["campaign_id"]}): pushed {result["pushed"]}, '
        f'skipped {result["skipped_no_email"]} without email, {result["skipped_opt_out"]} opted out.{err_note}',
    )
    return redirect('campaign_detail', campaign_id=campaign.pk)


@csrf_exempt
def webhook_instantly(request):
    """Receive Instantly event webhooks. No auth/CSRF (public endpoint).
    Instantly doesn't sign payloads by default, so at minimum we require
    INSTANTLY_WEBHOOK_SECRET as a header match if configured."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    secret = getattr(django_settings, 'INSTANTLY_WEBHOOK_SECRET', '') or ''
    if secret:
        supplied = request.headers.get('X-Instantly-Secret') or request.GET.get('secret', '')
        if supplied != secret:
            return JsonResponse({'ok': False, 'error': 'unauthorized'}, status=401)

    try:
        payload = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'invalid json'}, status=400)

    event_type = (payload.get('event_type') or payload.get('event') or '').strip()
    lead_email = (payload.get('lead_email') or payload.get('email') or '').strip().lower()
    campaign_id = str(payload.get('campaign_id') or payload.get('campaign') or '')
    ts_raw = payload.get('timestamp') or payload.get('time') or ''
    ts = timezone.now()  # fallback — Instantly timestamps vary

    # Map Instantly event names to our OutreachEvent.EVENT_TYPE values
    status_map = {
        'email_sent': (OutreachEvent.EVENT_EMAIL_SENT, ProjectLead.STATUS_SENT),
        'email_opened': (OutreachEvent.EVENT_EMAIL_OPENED, ProjectLead.STATUS_OPENED),
        'email_clicked': (OutreachEvent.EVENT_EMAIL_CLICKED, ProjectLead.STATUS_CLICKED),
        'email_replied': (OutreachEvent.EVENT_EMAIL_REPLIED, ProjectLead.STATUS_REPLIED),
        'email_bounced': (OutreachEvent.EVENT_EMAIL_BOUNCED, ProjectLead.STATUS_BOUNCED),
        'lead_unsubscribed': (OutreachEvent.EVENT_UNSUBSCRIBED, ProjectLead.STATUS_UNSUBSCRIBED),
    }

    if event_type not in status_map or not lead_email:
        # Still log it — makes debugging easier.
        return JsonResponse({'ok': True, 'note': 'event not mapped; ignored', 'event_type': event_type}, status=200)

    mapped_event, mapped_status = status_map[event_type]

    # Find the ProjectLead: prefer match on campaign_id + email, else fall back to email
    pl_qs = ProjectLead.objects.filter(lead__email__iexact=lead_email)
    if campaign_id:
        pl_qs = pl_qs.filter(campaign__instantly_campaign_id=campaign_id)
    project_lead = pl_qs.first()

    if not project_lead:
        return JsonResponse({'ok': True, 'note': 'no matching ProjectLead', 'email': lead_email}, status=200)

    event = OutreachEvent.objects.create(
        project_lead=project_lead,
        event_type=mapped_event,
        timestamp=ts,
        raw_payload=payload,
    )
    # Only advance status; don't downgrade (e.g. don't set 'sent' if we already have 'replied')
    project_lead.campaign_status = mapped_status
    project_lead.save(update_fields=['campaign_status', 'updated_at'])
    monday_sync.sync_project_lead(project_lead)
    if mapped_event in (OutreachEvent.EVENT_EMAIL_SENT, OutreachEvent.EVENT_EMAIL_REPLIED):
        monday_sync.sync_event_update(project_lead, event)

    # Auto-opt-out on unsubscribe
    if event_type == 'lead_unsubscribed' and lead_email:
        OptOut.objects.get_or_create(
            email=lead_email,
            defaults={'source': OptOut.SOURCE_INSTANTLY_WEBHOOK, 'reason': 'Unsubscribed via email link'},
        )

    return JsonResponse({'ok': True, 'project_lead_id': project_lead.pk, 'event_type': event_type})


@login_required
def validate_contact_urls_view(request):
    """HEAD-check every Lead.contact_url and clear broken ones."""
    qs = Lead.objects.exclude(contact_url='').only('id', 'contact_url')
    total = qs.count()

    if request.method == 'POST' and request.POST.get('confirm') == 'VALIDATE-CONTACT-URLS':
        pairs = list(qs.values_list('id', 'contact_url'))
        urls = [u for (_id, u) in pairs]
        result_map = sourcing.validate_urls(urls, timeout=5.0, max_workers=20)
        broken_ids = [lid for (lid, url) in pairs if not result_map.get(url, False)]
        updated = Lead.objects.filter(pk__in=broken_ids).update(contact_url='')
        messages.success(request, f'Checked {total} URLs — cleared {updated} broken ones.')
        return redirect('dashboard')

    return render(request, 'core/validate_urls.html', {'total': total})


@login_required
def cleanup_non_monday_leads_view(request):
    """Browser-accessible version of the cleanup management command — used when
    the Render Shell is unresponsive. GET shows the preview; POST with a
    confirmation token actually deletes.
    """
    from django.db.models import Count

    target = Lead.objects.exclude(source=Lead.SOURCE_MONDAY)
    total_to_delete = target.count()
    total_kept = Lead.objects.filter(source=Lead.SOURCE_MONDAY).count()
    breakdown = list(
        target.values('source').annotate(n=Count('id')).order_by('-n')
    )

    if request.method == 'POST' and request.POST.get('confirm') == 'DELETE-NON-MONDAY-LEADS':
        if total_to_delete == 0:
            messages.info(request, 'Nothing to delete.')
            return redirect('dashboard')
        deleted_count, per_model = target.delete()
        messages.success(
            request,
            f'Deleted {deleted_count} rows. Breakdown: '
            + ', '.join(f'{k.split(".")[-1]}={v}' for k, v in per_model.items()),
        )
        return redirect('dashboard')

    return render(request, 'core/cleanup_leads.html', {
        'total_to_delete': total_to_delete,
        'total_kept': total_kept,
        'breakdown': breakdown,
    })


@login_required
def lead_import_csv(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == 'POST' and request.FILES.get('file'):
        upload = request.FILES['file']
        try:
            text = upload.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            messages.error(request, 'CSV must be UTF-8 encoded.')
            return redirect('lead_import_csv', project_id=project.pk)

        reader = csv.DictReader(io.StringIO(text))
        created = reused = skipped_opt_out = 0
        errors: list[str] = []
        opted_out = set(OptOut.objects.values_list('email', flat=True))

        for i, row in enumerate(reader, start=2):
            email = (row.get('email') or '').strip().lower()
            if email and email in opted_out:
                skipped_opt_out += 1
                continue
            candidate = {
                'first_name': (row.get('first_name') or '').strip(),
                'last_name': (row.get('last_name') or '').strip(),
                'email': email or None,
                'phone': (row.get('phone') or '').strip(),
                'npi': (row.get('npi') or '').strip() or None,
                'organization': (row.get('organization') or '').strip(),
                'role': (row.get('role') or '').strip(),
                'specialty': (row.get('specialty') or '').strip(),
            }
            if not any([candidate['first_name'], candidate['last_name'], candidate['email'], candidate['organization']]):
                errors.append(f'Row {i}: no usable fields')
                continue
            _, was_created, had_conflict = sourcing._persist_candidate(  # noqa: SLF001 — internal reuse is deliberate
                candidate,
                default_source=Lead.SOURCE_CSV,
                default_enrichment=Lead.ENRICHMENT_COMPLETE if candidate['email'] else Lead.ENRICHMENT_NEEDED,
            )
            if was_created:
                created += 1
            else:
                reused += 1
            if had_conflict:
                errors.append(f'Row {i}: email matches an existing lead with different name/org — see Conflicts')

        messages.success(
            request,
            f'Imported — {created} new · {reused} reused · {skipped_opt_out} opt-out · {len(errors)} errors/conflicts',
        )
        return redirect('lead_review', project_id=project.pk)

    return render(request, 'core/lead_import.html', {'project': project})


def _flash_sourcing_result(request, result):
    if result.errors and result.candidates_found == 0:
        messages.warning(request, f"{result.source}: {' · '.join(result.errors)}")
        return
    if result.candidates_found == 0:
        messages.info(
            request,
            f"{result.source}: returned 0 candidates. Try broadening or narrowing the partner profile "
            f"(different specialty, add a state filter, or switch partner type).",
        )
        return
    conflict_note = f" · {len(result.conflicts)} conflicts" if result.conflicts else ''
    messages.success(
        request,
        f"{result.source}: {len(result.created)} new · {len(result.reused)} reused "
        f"· {result.skipped_opted_out} opt-out skipped{conflict_note} · {result.candidates_found} considered",
    )
    # Show non-fatal warnings alongside success (e.g. "2 specialty tags unrecognized but 98 leads found")
    if result.errors:
        messages.warning(request, f"{result.source}: {' · '.join(result.errors)}")
