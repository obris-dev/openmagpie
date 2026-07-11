"""The stable auth/tenant view base a fork's endpoints subclass (`plugins.api`).

Pins the plugin-facing re-export so a rename/move inside `accounts.api` surfaces as a
failing CORE test (a conscious break of the fork surface) rather than a silent break
downstream on the next upstream merge.
"""

from __future__ import annotations

from django.test import SimpleTestCase
from rest_framework import permissions

import accounts.api as core_api
from plugins.api import AccountScopedAPIView, AccountScopedRequest


class PluginApiSurfaceTests(SimpleTestCase):
    def test_reexports_the_core_scoped_view(self) -> None:
        # The facade IS the core base, not a divergent copy.
        self.assertIs(AccountScopedAPIView, core_api.AccountScopedAPIView)
        self.assertIs(AccountScopedRequest, core_api.AccountScopedRequest)

    def test_scoped_view_requires_authentication(self) -> None:
        # The whole reason a fork subclasses this: DRF's default permission is open,
        # so the base has to bring IsAuthenticated.
        self.assertIn(permissions.IsAuthenticated, AccountScopedAPIView.permission_classes)
