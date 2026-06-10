"""`/v1/feed-items/<item_id>` — one feed item's detail, addressed by its own
globally-unique ULID.

A feed item is a dependent record of its feed (no value apart from it), so its
by-own-id detail route is parent-qualified, kebab-case. The LIST lives nested
under the feed (`/v1/feeds/<id>/items`). Mounted at `/v1/feed-items` in
`conf.urls`. Read-only (items are server-produced).
"""

from common.urls import api_path

from . import views

urlpatterns = [
    api_path(
        "<str:item_id>",
        views.FeedItemDetailView.as_view(),
        name="feed_item_detail",
    ),
]
