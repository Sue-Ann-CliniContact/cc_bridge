from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from integrations import instantly as instantly_client

from .forms import ProjectForm, StudyAssetForm
from .models import Project, StudyAsset


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
