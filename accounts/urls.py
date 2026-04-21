from django.urls import path

from . import views

urlpatterns = [
    path('', views.login_page, name='home'),
    path('healthz/', views.healthz, name='healthz'),
    path('oauth/login/', views.monday_login, name='monday_login'),
    path('oauth/callback/', views.monday_callback, name='monday_callback'),
    path('logout/', views.logout_view, name='logout'),
]
