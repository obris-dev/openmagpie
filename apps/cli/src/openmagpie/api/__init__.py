"""Top-level API coordinator.

`Api` owns the underlying MagpieClient and exposes resource sub-clients
lazily via `@cached_property`. Call sites read like an SDK:

    ac.api.auth.me()
    ac.api.auth.create_device_session()
    ac.api.feed.list()
    ac.api.feed.create({...})

Adding a resource = one new file in `api/`, one cached_property here.
"""

from __future__ import annotations

from functools import cached_property

from ..http import MagpieClient
from .activity import ActivityApi
from .auth import AuthApi
from .delivery import DeliveryApi
from .engine import EngineApi
from .feed import FeedApi
from .watch import WatchApi


class Api:
    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    @cached_property
    def auth(self) -> AuthApi:
        return AuthApi(self._http)

    @cached_property
    def feed(self) -> FeedApi:
        return FeedApi(self._http)

    @cached_property
    def watch(self) -> WatchApi:
        return WatchApi(self._http)

    @cached_property
    def activity(self) -> ActivityApi:
        return ActivityApi(self._http)

    @cached_property
    def delivery(self) -> DeliveryApi:
        return DeliveryApi(self._http)

    @cached_property
    def engine(self) -> EngineApi:
        return EngineApi(self._http)


__all__ = ["ActivityApi", "Api", "AuthApi", "DeliveryApi", "EngineApi", "FeedApi", "WatchApi"]
