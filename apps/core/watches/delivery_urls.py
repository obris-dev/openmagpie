"""`/v1/action-deliveries/<delivery_id>` — one delivery's detail (with the sent
request_payload), addressed by its own globally-unique ULID.

A delivery is a dependent record of its action (no value apart from it), so its
by-own-id detail route is parent-qualified, kebab-case. The LIST lives nested
under the action (`/v1/actions/<id>/deliveries`, lean rows). Mounted at
`/v1/action-deliveries` in `conf.urls`.
"""

from common.urls import api_path

from . import views_audit

urlpatterns = [
    api_path(
        "<str:delivery_id>",
        views_audit.ActionDeliveryDetailView.as_view(),
        name="delivery_detail",
    ),
]
