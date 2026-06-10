"""`/v1/feed-sources/<source_id>` — one source's detail / delete, addressed by
its own globally-unique ULID.

A source is a dependent component of its feed (no value apart from it), so its
by-own-id route is parent-qualified, kebab-case ; the feed it belongs to is
resolved server-side, not supplied by the caller. The feed-scoped set lives at
`/v1/feeds/<id>/sources`. Mounted at `/v1/feed-sources` in `conf.urls`.
"""

from common.urls import api_path

from . import views

urlpatterns = [
    api_path(
        "<str:source_id>",
        views.SourceDetailView.as_view(),
        name="feed_source_detail_by_id",
    ),
]
