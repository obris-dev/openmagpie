from django.conf import settings
from django.urls import path

from common.urls import api_include
from common.views import healthz

# NOTE: we intentionally do NOT mount `oauth2_provider.urls`. The Toolkit
# models (AccessToken / RefreshToken / Application) are useful storage
# primitives that our own /v1/auth/* views compose with directly; we have
# no need for Toolkit's HTTP surface (which would expose /oauth/token's
# password / client_credentials grants and bypass our login/audit
# pipeline). Reinstate only if you build a real OAuth-provider flow.
#
# `api_include` makes the trailing slash on the prefix optional;
# `api_path` (inside each app's urlconf) makes the trailing slash on
# each leaf route optional. Together they let every /v1/<app>[/<leaf>]
# route work with or without a final slash.
_V1 = settings.API_VERSION_PREFIX

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    api_include(f"{_V1}/auth", "auth_api.urls"),
    api_include(f"{_V1}/feeds", "feeds.urls"),
    # One item's / source's detail by its own ULID (dependent records of a feed,
    # so parent-qualified) ; the lists are nested under the feed.
    api_include(f"{_V1}/feed-items", "feeds.item_urls"),
    api_include(f"{_V1}/feed-sources", "feeds.source_urls"),
    api_include(f"{_V1}/watches", "watches.urls"),
    # Per-action ops live at the top level (addressed by the action's own
    # ULID), not under /watches/<id>/actions ; see watches.action_urls.
    api_include(f"{_V1}/actions", "watches.action_urls"),
    # One run's / delivery's detail by its own ULID (dependent records of an
    # action, so parent-qualified) ; the lists are nested under the action.
    api_include(f"{_V1}/action-activity", "watches.activity_urls"),
    api_include(f"{_V1}/action-deliveries", "watches.delivery_urls"),
    api_include(f"{_V1}/engines", "engine.urls"),
    # Public, unauthenticated waitlist signup (marketing site).
    api_include(f"{_V1}/waitlist", "waitlist.urls"),
]
