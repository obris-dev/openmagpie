"""Server-side WatchAction-config POLICY (the Django/settings-coupled
guards that can't live in the pure shared `openmagpie-schema` package).

The watches analogue of `feeds.policy`. `enforce_action_policy` runs at
the write validation seam (serializer -> 400 ; service). Idempotent; pure
predicates over the already-shape-valid config.

Guards today:
  - a semantic_filter's `engine.kind`, when pinned, must be a registered
    engine (else the run 500s mid-poll instead of failing at create).
"""

from django.conf import settings

from common.ssrf import destination_block_reason
from engine import registry as engine_registry
from openmagpie_schema.watch_actions import (
    DeliveryConfigBase,
    SemanticFilterConfig,
    WatchActionConfigBase,
    WebhookConfig,
)


class PolicyError(ValueError):
    """A watch-action config violates server policy. Mapped to a 400 at
    the HTTP boundary; the message is operator-facing."""


def enforce_action_policy(config: WatchActionConfigBase) -> WatchActionConfigBase:
    """Apply every server policy guard on the action config; return it or
    raise `PolicyError`. Idempotent. Dispatches per kind ; kinds with no
    settings-coupled policy pass straight through."""
    if isinstance(config, SemanticFilterConfig):
        _enforce_engine_registered(config)
    if isinstance(config, DeliveryConfigBase):
        _enforce_digest_interval(config)
    if isinstance(config, WebhookConfig):
        _enforce_webhook_url_safety(config)
    return config


def _enforce_digest_interval(config: DeliveryConfigBase) -> None:
    """A digest delivery's window length must be a sane bound (the flush
    cron's cadence). Only checked when delivery == DIGEST ; instant ignores
    the field."""
    if not config.is_digest():
        return
    lo, hi = settings.DIGEST_MIN_INTERVAL_SECONDS, settings.DIGEST_MAX_INTERVAL_SECONDS
    if not (lo <= config.digest_interval_seconds <= hi):
        raise PolicyError(
            f"digest_interval_seconds must be between {lo} and {hi} (got {config.digest_interval_seconds})"
        )


def _enforce_webhook_url_safety(config: WebhookConfig) -> None:
    """Write-time SSRF gate: reject a require-https violation or an
    IP-literal host in a blocked range. DNS resolution is deferred to send
    time (the impl re-checks with resolve_dns=True), since a hostname's
    address can change between create and delivery."""
    reason = destination_block_reason(
        config.url,
        require_https=settings.WEBHOOK_REQUIRE_HTTPS,
        block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
        resolve_dns=False,
    )
    if reason:
        raise PolicyError(f"webhook url rejected: {reason}")


def _enforce_engine_registered(config: SemanticFilterConfig) -> None:
    """A pinned `engine.kind` must be registered in this deployment.

    Empty kind means "use the server default" (resolved at judge time
    by the registry), so only a non-empty pin is checked here. Rejecting
    at the write boundary means the operator sees a clean 400 naming the
    bad kind + the available set, instead of every judge cycle 500ing on
    an unknown engine."""
    kind = config.engine.kind
    if not kind:
        # Defaulted at runtime ; the default itself is a deploy invariant
        # (settings + engine.registry), not operator input, so not gated here.
        return
    try:
        engine_registry.get(kind)
    except KeyError:
        raise PolicyError(
            f"unknown engine kind {kind!r}; registered: {engine_registry.kinds() or '(none)'} "
            f"(default is {engine_registry.DEFAULT_KIND!r}; leave engine.kind empty to use it)"
        ) from None
