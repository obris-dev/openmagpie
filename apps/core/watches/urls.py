from common.urls import api_path

from . import views, views_bower

urlpatterns = [
    api_path(
        "",
        views.WatchListCreateView.as_view(),
        name="watch_list_create",
    ),
    api_path(
        "<str:watch_id>",
        views.WatchDetailView.as_view(),
        name="watch_detail",
    ),
    api_path(
        "<str:watch_id>/actions",
        views.WatchActionsView.as_view(),
        name="watch_actions",
    ),
    api_path(
        "<str:watch_id>/connect-bower",
        views_bower.WatchBowerConnectView.as_view(),
        name="watch_connect_bower",
    ),
    # Per-action ops (edit / remove / runs) are NOT nested here ; an action
    # ULID is globally unique, so it's addressed directly at /v1/actions/<id>
    # (see watches.action_urls). The chain-level list/add stay watch-scoped.
]
