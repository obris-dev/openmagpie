"""Stable API-view base for fork / sibling-app endpoints.

An external app mounted via `OPENMAGPIE_PLUGIN_API_URLS` almost always needs the
same authentication + tenant scoping core's own endpoints have. That base lives in
`accounts.api` (a core app's internal module); a fork importing it directly would
couple to a core internal that can move, breaking the fork on the next upstream
merge and defeating the zero-core-edit goal.

So this module is the STABLE seam, the same role `plugins.registry` / `plugins.db` /
`plugins.guards` play for the rest of the framework: a fork imports the base view
FROM HERE, and core promises to keep this surface working while it is free to refactor
`accounts` however it likes (only this one re-export would need updating, a core edit,
never a fork edit).

    from plugins.api import AccountScopedAPIView

    class RecordListView(AccountScopedAPIView):
        def get(self, request):
            # authenticated; request.account_id is the caller's tenant.
            return Response({"account_id": request.account_id})

`AccountScopedAPIView` requires authentication (a 401 for an anonymous request; the
default DRF permission here is open, so subclassing this is how a fork's endpoint gets
auth) and resolves the caller's primary account into `request.account_id` (a 403 for a
user with no account). `AccountScopedRequest` is the typing-only request subclass that
lets a checker see `request.account_id`. Compose further scoping on top (e.g. a
resource-scoped mixin) the way `feeds.api` builds `FeedScopedAPIView`.
"""

from __future__ import annotations

from accounts.api import AccountScopedAPIView, AccountScopedRequest

__all__ = ["AccountScopedAPIView", "AccountScopedRequest"]
