import csv
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from integrations import ai_sourcing as ai_sourcing_client
from integrations import apollo as apollo_client
from integrations import instantly as instantly_client
from integrations import monday_client

from .forms import LeadEditForm, PartnerProfileForm, ProjectForm, StudyAssetForm
from .models import Lead, OptOut, PartnerProfile, Project, ProjectLead, StudyAsset
from .services import sourcing


@login_required
def dashboard(request):
    projects = Project.objects.all()[:50]
    return render(request, 'core/dashboard.html', {'projects': projects})


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
    project = get_object_or_404(Project, pk=project_id)
    asset_form = StudyAssetForm()
    return render(request, 'core/project_detail.html', {
        'project': project,
        'assets': project.assets.all(),
        'asset_form': asset_form,
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
    return redirect('project_detail', project_id=project.pk)


@login_required
@require_POST
def asset_approve(request, asset_id):
    asset = get_object_or_404(StudyAsset, pk=asset_id)
    asset.approved_by = request.user
    asset.approved_at = timezone.now()
    asset.save(update_fields=['approved_by', 'approved_at', 'updated_at'])
    return redirect('project_detail', project_id=asset.project_id)


@login_required
def test_instantly(request):
    """Phase 1 sanity check: does the configured Instantly key return campaigns?"""
    result = instantly_client.ping()
    return JsonResponse(result)


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
            return redirect('project_detail', project_id=project.pk)
    else:
        initial = {}
        if suggestion:
            initial = {
                'partner_type': suggestion.get('partner_type') or (profile.partner_type if profile else 'clinician'),
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
            for key in ('email', 'first_name', 'last_name', 'organization', 'role', 'phone', 'specialty')
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
            ('email', 'Email'),
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
    has_email = request.GET.get('has_email', '')
    hide_added = request.GET.get('hide_added', '1')
    query = request.GET.get('q', '').strip()

    if source_filter:
        qs = qs.filter(source=source_filter)
    if has_email == '1':
        qs = qs.filter(email__isnull=False).exclude(email='')
    elif has_email == '0':
        qs = qs.filter(Q(email__isnull=True) | Q(email=''))
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
            'has_email': has_email,
            'hide_added': hide_added,
            'q': query,
        },
        'source_choices': Lead.SOURCE_CHOICES,
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
    limit = int(request.POST.get('limit') or 30)
    result = sourcing.source_from_ai(project, limit=limit, user=request.user)
    _flash_sourcing_result(request, result)
    return redirect('lead_review', project_id=project.pk)


@login_required
@require_POST
def add_leads_to_project(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    lead_ids = [int(x) for x in request.POST.getlist('lead_ids') if x.isdigit()]
    if not lead_ids:
        messages.warning(request, 'Select at least one lead.')
        return redirect('lead_review', project_id=project.pk)

    existing = set(ProjectLead.objects.filter(project=project, lead_id__in=lead_ids).values_list('lead_id', flat=True))
    to_create = [
        ProjectLead(project=project, lead_id=lead_id)
        for lead_id in lead_ids
        if lead_id not in existing
    ]
    ProjectLead.objects.bulk_create(to_create)
    messages.success(request, f'Added {len(to_create)} lead{"s" if len(to_create) != 1 else ""} to {project.study_code}.')
    return redirect('lead_review', project_id=project.pk)


@login_required
@require_POST
def enrich_lead(request, lead_id):
    lead = get_object_or_404(Lead, pk=lead_id)
    result = sourcing.enrich_lead_with_apollo(lead, user=request.user)
    return JsonResponse(result)


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
    if result.errors:
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
