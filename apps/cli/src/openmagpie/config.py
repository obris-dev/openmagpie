"""Local CLI config: per-environment server URL + tokens + user identity.

Stored at `~/.magpie/config.json`, mode 0600. File shape:

    {
      "active_env": "local",
      "envs": {
        "local":  {"server_url": "http://localhost:8000", ...},
        "cloud":  {"server_url": "https://api.openmagpie.ai", ...}
      }
    }

The wrapper exists so a future hosted environment ("cloud") can sit
alongside a local-dev environment without callers having to manage
multiple config files. The CLI operates on a single env at a time,
chosen by `active_env`. `load()` returns that env's `Config` so call
sites stay on `config.access_token` and don't see the wrapper.

Pydantic validates on read so a corrupted file fails loudly instead of
crashing later.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

CONFIG_DIR_NAME = ".magpie"
CONFIG_FILE_NAME = "config.json"

ENV_LOCAL = "local"
DEFAULT_ACTIVE_ENV = ENV_LOCAL
DEFAULT_SERVER_URL = "http://localhost:8000"


def config_dir() -> Path:
    return Path.home() / CONFIG_DIR_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


class UserInfo(BaseModel):
    id: str
    email: str
    account_id: str | None = None


class Config(BaseModel):
    """Per-environment settings: where to point the CLI and what
    credentials it currently holds for that environment.
    """

    server_url: str = DEFAULT_SERVER_URL
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    user: UserInfo | None = None
    # Whether this machine's CLI has already offered the (server-side) telemetry
    # opt-in after a login, so we don't re-ask. A UX flag only; the real decision
    # lives server-side (apps/core/telemetry), not here.
    telemetry_prompted: bool = False

    @property
    def is_authenticated(self) -> bool:
        return bool(self.access_token and self.user)

    def access_token_expired(self, *, leeway_seconds: int = 0) -> bool:
        if not self.token_expires_at:
            return True
        now = datetime.now(UTC)
        return (self.token_expires_at - now).total_seconds() <= leeway_seconds

    def apply_credentials(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        user: UserInfo | None = None,
    ) -> None:
        """Set the bearer-auth fields in place. Caller is responsible for
        persisting via `save()`. `user` is optional so the refresh path
        (which doesn't re-send the user record) can call this too.
        """
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        if user is not None:
            self.user = user

    def apply_personal_access_token(self, token: str) -> None:
        """Store a long-lived personal access token as the credential.

        Unlike `apply_credentials`, there's no refresh token (PATs don't
        rotate) and no tracked expiry: the server is the source of truth,
        and an expired/revoked PAT surfaces as a 401 on the next call. The
        empty refresh token is also what makes the http layer send the PAT
        directly instead of trying to refresh it. Caller persists via
        `save()` and sets `user` from the validating `/me` call.
        """
        self.access_token = token
        self.refresh_token = None
        self.token_expires_at = None

    def clear_credentials(self) -> None:
        """Zero out the bearer-auth fields in place. Doesn't touch the file."""
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.user = None


class ConfigFile(BaseModel):
    """Top-level wrapper around ~/.magpie/config.json. Holds multiple
    named environments plus the currently active one.

    Most code touches `Config` (the active env), not this wrapper.
    """

    active_env: str = DEFAULT_ACTIVE_ENV
    envs: dict[str, Config] = Field(default_factory=lambda: {ENV_LOCAL: Config()})

    @property
    def active(self) -> Config:
        """The Config for whichever env `active_env` points at.

        Auto-seeds the `local` env if it's somehow missing (fresh
        config files default to having it). Any other unknown env name
        is a user / file error, raise loudly rather than silently
        making one up.
        """
        if self.active_env not in self.envs:
            if self.active_env == ENV_LOCAL:
                self.envs[ENV_LOCAL] = Config()
            else:
                raise RuntimeError(
                    f"active_env={self.active_env!r} has no record under envs in ~/{CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}"
                )
        return self.envs[self.active_env]


def load_file() -> ConfigFile:
    """Read the full multi-env wrapper. Most callers want `load()`."""
    path = config_path()
    if not path.exists():
        return ConfigFile()
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"~/{CONFIG_DIR_NAME}/{CONFIG_FILE_NAME} is corrupt: {e}") from e
    return ConfigFile.model_validate(raw)


def load() -> Config:
    """Load the active environment's Config. The default for most callers."""
    return load_file().active


def save(config: Config) -> None:
    """Persist `config` as the active environment's record.

    Reads the existing wrapper (preserving any other envs) and writes
    the whole file back atomically with 0600 perms.

    `tempfile.NamedTemporaryFile` gives us:
      - a randomized name in the same directory as the target, so two
        concurrent CLI invocations don't share a tmp path and clobber
        each other
      - `O_CREAT | O_EXCL` and mode 0600 internally, so the file is
        created with the right permissions atomically, no
        world-readable window between create and chmod
    Followed by `os.replace` for the atomic rename onto `target`.
    """
    wrapper = load_file()
    wrapper.envs[wrapper.active_env] = config

    directory = config_dir()
    directory.mkdir(mode=0o700, exist_ok=True)
    target = config_path()

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=directory,
            prefix=".config-",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = f.name
            f.write(wrapper.model_dump_json(indent=2))
        os.replace(tmp_path, target)
    except OSError:
        # Tempfile / write / replace are the only OSError paths here ;
        # cleanup runs and the original error re-propagates so the
        # caller sees the real failure (disk full, permission denied,
        # parent dir gone, ...).
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)
        raise
