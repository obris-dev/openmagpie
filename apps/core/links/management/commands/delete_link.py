"""Revoke a short link: `python manage.py delete_link --code <slug>`.

Deletes the link and its ClickEvents. The way to pull a link whose destination
later turns malicious (destinations aren't re-validated after mint).
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from links.services import ShortLinkService


class Command(BaseCommand):
    help = "Delete a short link (and its click events) by code."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--code", required=True, help="Code of the short link to delete.")

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["code"]
        if not ShortLinkService.delete(code):
            raise CommandError(f"no short link with code {code!r}")
        self.stdout.write(f"deleted short link {code!r} and its click events")
