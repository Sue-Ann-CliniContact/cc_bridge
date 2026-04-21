from django.urls import path

from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('projects/new/', views.project_create, name='project_create'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('projects/<int:project_id>/assets/', views.asset_upload, name='asset_upload'),
    path('assets/<int:asset_id>/approve/', views.asset_approve, name='asset_approve'),
    path('api/instantly/ping/', views.test_instantly, name='test_instantly'),
]
