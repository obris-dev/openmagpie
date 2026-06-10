"""`/v1/action-activity/<activity_id>` — one run's detail, addressed by its own
globally-unique ULID.

A run ("activity entry") is a dependent record of its action (no value apart from
it), so its by-own-id detail route is parent-qualified, kebab-case. The LIST lives
nested under the action (`/v1/actions/<id>/activity`, lean rows). Mounted at
`/v1/action-activity` in `conf.urls`.
"""

from common.urls import api_path

from . import views_audit

urlpatterns = [
    api_path(
        "<str:activity_id>",
        views_audit.ActionActivityDetailView.as_view(),
        name="action_activity_detail",
    ),
]
