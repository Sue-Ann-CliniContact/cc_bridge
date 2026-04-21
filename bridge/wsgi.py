"""WSGI config for the bridge project."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bridge.settings')

application = get_wsgi_application()
