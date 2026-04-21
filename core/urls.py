from django.urls import path

from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('projects/new/', views.project_create, name='project_create'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('projects/<int:project_id>/assets/', views.asset_upload, name='asset_upload'),
    path('assets/<int:asset_id>/approve/', views.asset_approve, name='asset_approve'),
    path('projects/<int:project_id>/partner-profile/', views.partner_profile_edit, name='partner_profile_edit'),
    path('projects/<int:project_id>/leads/', views.lead_review, name='lead_review'),
    path('projects/<int:project_id>/leads/source/npi/', views.source_leads_npi, name='source_leads_npi'),
    path('projects/<int:project_id>/leads/source/ai/', views.source_leads_ai, name='source_leads_ai'),
    path('projects/<int:project_id>/leads/add/', views.add_leads_to_project, name='add_leads_to_project'),
    path('projects/<int:project_id>/leads/import/', views.lead_import_csv, name='lead_import_csv'),
    path('api/leads/<int:lead_id>/enrich/', views.enrich_lead, name='enrich_lead'),
    path('api/instantly/ping/', views.test_instantly, name='test_instantly'),
]
