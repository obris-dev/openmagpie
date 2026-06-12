"""Per-invocation context shared by every subcommand.

The root Typer callback (`cli.main`) builds one `AppContext` and stashes
it into the module-level `ContextVar` below; subcommands grab it via
`app_ctx()` without needing `ctx: typer.Context` in their signatures.

We use `contextvars.ContextVar` (stdlib) rather than reaching into
Click's context stack so the CLI doesn't take a direct dependency on
click beyond what Typer already pulls in.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

import httpx

from .api import Api
from .api.auth import AuthUser, TokenPair
from .config import Config, UserInfo, load, save
from .constants import is_personal_access_token
from .http import ApiError, MagpieClient


class AppContext:
    """Holds the loaded config, the transport client, and the resource API."""

    config: Config
    api: Api
    _http: MagpieClient

    def __init__(self, *, server_url: str | None = None) -> None:
        self.config = load()
        if server_url:
            self.config.server_url = server_url.rstrip("/")
        self._http = MagpieClient(self.config)
        self.api = Api(self._http)

    def close(self) -> None:
        self._http.close()

    def sign_in(self, bundle: TokenPair) -> None:
        """Persist a new authenticated session: apply credentials in-memory
        and write the config file. Accepts any `TokenPair` shape (including
        `DeviceSessionCompleted`, which inherits from it).
        """
        self.config.apply_credentials(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            expires_in=bundle.expires_in,
            user=UserInfo(
                id=bundle.user.id,
                email=bundle.user.email,
                account_id=bundle.user.account_id,
            ),
        )
        save(self.config)

    def sign_in_with_token(self, raw_token: str) -> AuthUser:
        """Validate a personal access token and persist it as the session.

        Stages the PAT as the sole credential FIRST (no refresh token, no
        expiry) so the validating `/me` call sends the PAT itself and the
        http layer never tries to rotate a previous session's token, then
        confirms it by fetching the user and writes the config. Raises
        `AuthError` if the server rejects the token.

        On failure the prior in-memory credentials are restored, so a
        rejected token never leaves the context half-authenticated (the
        staged dead token paired with the old user).
        """
        prior = (
            self.config.access_token,
            self.config.refresh_token,
            self.config.token_expires_at,
            self.config.user,
        )
        self.config.apply_personal_access_token(raw_token)
        try:
            me = self.api.auth.me()  # raises AuthError on a bad/revoked token
        except Exception:
            (
                self.config.access_token,
                self.config.refresh_token,
                self.config.token_expires_at,
                self.config.user,
            ) = prior
            raise
        self.config.user = UserInfo(id=me.id, email=me.email, account_id=me.account_id)
        save(self.config)
        return me

    def sign_out(self) -> bool:
        """End the session. Local cleanup ALWAYS runs; returns True if
        server-side revocation also succeeded, False if it failed
        (server unreachable, token already invalid, etc.).

        Local cleanup is unconditional because a stale local token is
        no worse than what we had before logout, clearing it is the
        load-bearing step. Callers should surface a warning on False
        so the user knows the token may still be live until natural
        expiry.

        A personal access token is NOT revoked here: it's a durable
        credential the operator may reuse on other boxes, so logout only
        forgets it locally. Explicit `auth token revoke` (or the server
        command) ends a PAT's life.
        """
        server_ok = True
        token = self.config.access_token
        if token and not is_personal_access_token(token):
            try:
                self.api.auth.tokens.revoke()
            except (ApiError, httpx.HTTPError):
                # ApiError = server reachable but revocation rejected
                # (already-expired token, etc.) ; httpx.HTTPError =
                # transport failure (server unreachable / timeout).
                # Either way we still want local creds cleared.
                server_ok = False
        self.config.clear_credentials()
        save(self.config)
        return server_ok


_current: ContextVar[AppContext] = ContextVar("magpie_app_context")


def bind_app_ctx(ctx: AppContext) -> Token[AppContext]:
    """Set the current AppContext. Returns a token usable with `unbind`.

    Called once by the root Typer callback. Subcommands should not need
    to call this directly.
    """
    return _current.set(ctx)


def unbind_app_ctx(token: Token[AppContext]) -> None:
    """Restore the previous AppContext (or unset, if there was none).

    Paired with `bind_app_ctx` to keep the ContextVar clean across
    successive invocations within the same process (tests, REPL).
    """
    _current.reset(token)


def app_ctx() -> AppContext:
    """Return the AppContext for the current command invocation.

    Reads from a module-level ContextVar set by the root callback in
    `cli.main`. Callable from any subcommand body without threading
    `ctx: typer.Context` through the signature.
    """
    try:
        return _current.get()
    except LookupError as e:
        raise RuntimeError("AppContext is not bound. Did the root Typer callback run?") from e


def app_api() -> Api:
    """Shortcut for `app_ctx().api`. Use when a command only needs the API."""
    return app_ctx().api


def app_config() -> Config:
    """Shortcut for `app_ctx().config`. Use when a command only needs config."""
    return app_ctx().config
