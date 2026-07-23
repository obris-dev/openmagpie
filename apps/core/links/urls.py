from django.urls import path

from . import views

# Served ONLY when ShortLinkHostMiddleware swaps request.urlconf to this module
# for the SHORTLINK_HOST, so a bare `<code>` resolves at the short domain's root
# without touching the main API urlconf.
urlpatterns = [
    path("", views.shortlink_root, name="shortlink_root"),
    path("<str:code>", views.shortlink_redirect, name="shortlink"),
]
