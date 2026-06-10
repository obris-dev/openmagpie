from common.urls import api_path

from . import views

urlpatterns = [
    api_path(
        "",
        views.FeedListCreateView.as_view(),
        name="feed_list_create",
    ),
    api_path(
        "<str:feed_id>",
        views.FeedDetailView.as_view(),
        name="feed_detail",
    ),
    api_path(
        "<str:feed_id>/sources",
        views.FeedSourcesView.as_view(),
        name="feed_sources",
    ),
    api_path(
        "<str:feed_id>/sources/<str:source_id>",
        views.FeedSourceDetailView.as_view(),
        name="feed_source_detail",
    ),
    api_path(
        "<str:feed_id>/items",
        views.FeedItemsView.as_view(),
        name="feed_items",
    ),
]
