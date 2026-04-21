from django.urls import path

from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('projects/new/', views.project_create, name='project_create'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('projects/<int:project_id>/assets/', views.asset_upload, name='asset_upload'),
    path('assets/<int:asset_id>/approve/', views.asset_approve, name='asset_approve'),
    path('projects/<int:project_id>/partner-profile/', views.partner_profile_edit, name='partner_profile_edit'),
    path('projects/<int:project_id>/partner-profile/suggest/', views.partner_profile_suggest, name='partner_profile_suggest'),
    path('projects/<int:project_id>/leads/', views.lead_review, name='lead_review'),
    path('projects/<int:project_id>/leads/conflicts/', views.lead_conflicts, name='lead_conflicts'),
    path('projects/<int:project_id>/leads/source/npi/', views.source_leads_npi, name='source_leads_npi'),
    path('projects/<int:project_id>/leads/source/ai/', views.source_leads_ai, name='source_leads_ai'),
    path('projects/<int:project_id>/leads/add/', views.add_leads_to_project, name='add_leads_to_project'),
    path('projects/<int:project_id>/leads/import/', views.lead_import_csv, name='lead_import_csv'),
    path('projects/<int:project_id>/leads/import/monday/', views.monday_import_picker, name='monday_import_picker'),
    path('projects/<int:project_id>/leads/import/monday/<str:board_id>/', views.monday_import_preview, name='monday_import_preview'),
    path('leads/<int:lead_id>/edit/', views.lead_edit, name='lead_edit'),
    path('leads/<int:lead_id>/resolve-conflict/', views.lead_resolve_conflict, name='lead_resolve_conflict'),
    path('api/leads/<int:lead_id>/enrich/', views.enrich_lead, name='enrich_lead'),
    path('api/leads/<int:lead_id>/find-contact/', views.find_org_contact, name='find_org_contact'),
    path('api/instantly/ping/', views.test_instantly, name='test_instantly'),
]
