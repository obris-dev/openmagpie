"""Mint a personal access token for a user from the server shell.

The headless cold-start path: a self-hoster who has SSH on the box runs
this to get a token, then feeds it to the CLI with
`MAGPIE_TOKEN=... magpie auth login --token` (no browser, no device-flow
URL to reach). Breaks the chicken-and-egg of "you must be logged in to
mint a login credential".

The raw token is printed ONCE, it's only stored hashed, so it can never
be shown again.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from accounts.models.user import User
from accounts.services import UserService
from auth_api.services.cli_tokens import CliTokenService


class Command(BaseCommand):
    help = "Mint a personal access token for a user (printed once)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True, help="Email of the user to issue the token for.")
        parser.add_argument(
            "--name",
            default="cli token",
            help="Label to tell tokens apart (e.g. 'home-office box'). Default: 'cli token'.",
        )
        parser.add_argument(
            "--expires-in-days",
            type=int,
            default=None,
            help="Optional expiry in days. Omit for a non-expiring token (the default).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        email = options["email"]
        try:
            user = UserService.Global.get_by_email(email)
        except User.DoesNotExist:
            raise CommandError(f"No user with email {email!r}.") from None

        try:
            token, raw_token = CliTokenService.Global.mint(
                user,
                name=options["name"],
                expires_in_days=options["expires_in_days"],
            )
        except ValueError as e:
            raise CommandError(str(e)) from None

        expiry = "never" if token.expires_at is None else token.expires_at.isoformat()
        self.stdout.write(self.style.SUCCESS(f"Personal access token for {user.email} (expires: {expiry}):"))
        self.stdout.write("")
        self.stdout.write(f"  {raw_token}")
        self.stdout.write("")
        self.stdout.write(
            "Copy it now, it will NOT be shown again. Use it on the box with:\n"
            "  magpie auth login --token   (paste it at the prompt)\n"
            "Revoke it later with: magpie auth token revoke <id>"
        )
