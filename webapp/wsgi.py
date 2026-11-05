"""WSGI entry point for a production server (gunicorn webapp.wsgi), used from phase 8 on."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webapp.settings")
application = get_wsgi_application()
