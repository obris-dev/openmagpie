import os

from django.core.wsgi import get_wsgi_application
from dotenv import load_dotenv

load_dotenv()

# Default to the locked-down "cloud" settings when DJANGO_ENV is unset, so a
# deploy that forgets to set it fails SAFE (never into permissive dev settings).
# Dev opts into "local" via .env (see apps/core/.env.example).
env = os.environ.get("DJANGO_ENV", "cloud")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"conf.settings.{env}")

application = get_wsgi_application()
