from django.urls import path

from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/ai/', views.dashboard_ai, name='dashboard_ai'),
    path('portal/', views.client_dashboard, name='client_dashboard'),
    path('portal/ai/', views.client_dashboard_ai, name='client_dashboard_ai'),
    path('projects/new/', views.project_create, name='project_create'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('projects/<int:project_id>/ai/', views.project_ai, name='project_ai'),
    path('portal/projects/<int:project_id>/', views.client_project_detail, name='client_project_detail'),
    path('portal/projects/<int:project_id>/ai/', views.client_project_ai, name='client_project_ai'),
    path('projects/<int:project_id>/monday/provision/', views.monday_provision_board, name='monday_provision_board'),
    path('projects/<int:project_id>/monday/sync/', views.monday_sync_project_view, name='monday_sync_project'),
    path('projects/<int:project_id>/monday/pull/', views.monday_pull_statuses_view, name='monday_pull_statuses'),
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
    path('api/leads/<int:lead_id>/web-contacts/', views.web_find_org_contacts, name='web_find_org_contacts'),
    path('api/leads/<int:lead_id>/web-enrich/', views.web_enrich_clinician, name='web_enrich_clinician'),
    path('tools/cleanup-non-monday-leads/', views.cleanup_non_monday_leads_view, name='cleanup_non_monday_leads'),
    path('tools/validate-contact-urls/', views.validate_contact_urls_view, name='validate_contact_urls'),

    # Campaigns (Phase 3)
    path('projects/<int:project_id>/campaigns/new/', views.campaign_create, name='campaign_create'),
    path('campaigns/<int:campaign_id>/', views.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:campaign_id>/redraft/', views.campaign_redraft, name='campaign_redraft'),
    path('campaigns/<int:campaign_id>/update-sequence/', views.campaign_update_sequence, name='campaign_update_sequence'),
    path('campaigns/<int:campaign_id>/add-leads/', views.campaign_add_leads, name='campaign_add_leads'),
    path('campaigns/<int:campaign_id>/remove-lead/<int:project_lead_id>/', views.campaign_remove_lead, name='campaign_remove_lead'),
    path('campaigns/<int:campaign_id>/launch/', views.campaign_launch, name='campaign_launch'),

    # Instantly webhook
    path('api/webhooks/instantly/', views.webhook_instantly, name='webhook_instantly'),
    path('api/instantly/ping/', views.test_instantly, name='test_instantly'),
]
