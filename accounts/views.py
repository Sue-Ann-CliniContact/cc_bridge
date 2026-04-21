import secrets

import requests
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render

from .models import ClientProfile

MONDAY_AUTH_URL = 'https://auth.monday.com/oauth2/authorize'
MONDAY_TOKEN_URL = 'https://auth.monday.com/oauth2/token'
MONDAY_API_URL = 'https://api.monday.com/v2'


def login_page(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'accounts/login.html')


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return JsonResponse({
            'ok': True,
            'env': {
                'monday_oauth': bool(settings.MONDAY_CLIENT_ID and settings.MONDAY_CLIENT_SECRET),
                'instantly': bool(settings.INSTANTLY_API_KEY),
                'anthropic': bool(settings.ANTHROPIC_API_KEY),
                'apollo': bool(settings.APOLLO_API_KEY),
                'monday_workspace': bool(settings.MONDAY_WORKSPACE_ID),
            },
        })
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=503)


def monday_login(request):
    """Redirects the user to Monday's OAuth authorize endpoint."""
    if not settings.MONDAY_CLIENT_ID:
        return render(request, 'accounts/error.html', {
            'message': 'MONDAY_CLIENT_ID is not configured. Set it in your .env before attempting to log in.'
        })

    state = secrets.token_urlsafe(32)
    request.session['monday_oauth_state'] = state

    url = (
        f"{MONDAY_AUTH_URL}"
        f"?client_id={settings.MONDAY_CLIENT_ID}"
        f"&redirect_uri={settings.MONDAY_REDIRECT_URI}"
        f"&state={state}"
    )
    return redirect(url)


def monday_callback(request):
    """Handles the OAuth callback, creates the Django user, stores the Monday token."""
    code = request.GET.get('code')
    returned_state = request.GET.get('state')
    expected_state = request.session.pop('monday_oauth_state', None)

    if not code:
        return redirect('home')
    if not returned_state or not expected_state or returned_state != expected_state:
        return render(request, 'accounts/error.html', {'message': 'Invalid OAuth state. Please try logging in again.'})

    payload = {
        'client_id': settings.MONDAY_CLIENT_ID,
        'client_secret': settings.MONDAY_CLIENT_SECRET,
        'code': code,
        'redirect_uri': settings.MONDAY_REDIRECT_URI,
    }
    token_response = requests.post(MONDAY_TOKEN_URL, data=payload, timeout=15)
    token_data = token_response.json()
    access_token = token_data.get('access_token')

    if not access_token:
        return render(request, 'accounts/error.html', {
            'message': 'Failed to obtain a Monday access token.',
            'debug': token_data,
        })

    headers = {'Authorization': access_token, 'API-Version': '2023-10'}
    user_query = '{ me { id email name } }'
    user_res = requests.post(MONDAY_API_URL, json={'query': user_query}, headers=headers, timeout=15)
    user_data = user_res.json()

    try:
        monday_user = user_data['data']['me']
        monday_id = monday_user['id']
        email = monday_user['email']
        name = monday_user['name'] or ''
    except (KeyError, TypeError):
        return render(request, 'accounts/error.html', {'message': 'Could not retrieve Monday user profile.'})

    user, created = User.objects.get_or_create(username=str(monday_id))
    if created or not user.email:
        user.email = email
        user.first_name = name.split(' ')[0] if name else ''
        last = name.split(' ', 1)[1] if ' ' in name else ''
        user.last_name = last
        user.save()

    ClientProfile.objects.update_or_create(
        user=user,
        defaults={'monday_id': str(monday_id), 'access_token': access_token},
    )

    login(request, user)
    return redirect('dashboard')


def logout_view(request):
    logout(request)
    return redirect('home')
