"""`/v1/action-backfills` — this account's backfill jobs (list) + one job's status
by its own ULID.

Mounted at `/v1/action-backfills` in `conf.urls`. The SUBMIT endpoint is
action-scoped (`/v1/actions/<id>/backfill`, in `action_urls`); the job is a
dependent record of that action, so it's parent-qualified with the `action-`
prefix, mirroring `action-activity` / `action-deliveries`.
"""

from common.urls import api_path

from . import views_backfill

urlpatterns = [
    api_path(
        "",
        views_backfill.BackfillListView.as_view(),
        name="backfill_list",
    ),
    api_path(
        "<str:backfill_id>",
        views_backfill.BackfillDetailView.as_view(),
        name="backfill_detail",
    ),
]
