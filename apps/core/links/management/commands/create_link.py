"""Mint a short link: `python manage.py create_link --url <url> [--code <slug>]`.

Prints the full short URL. A random base62 code is generated unless `--code`
gives a custom vanity slug.
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from links.constants import CODE_LENGTH
from links.services import ShortLinkService


class Command(BaseCommand):
    help = "Create a short link (random code by default; --code for a custom slug)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--url", required=True, help="Destination URL to redirect to.")
        parser.add_argument(
            "--code", default=None, help=f"Optional custom slug (else a random {CODE_LENGTH}-char code)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            link = ShortLinkService.create(url=options["url"], code=options["code"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        host = settings.SHORTLINK_HOST or "<SHORTLINK_HOST unset>"
        self.stdout.write(f"https://{host}/{link.code}  ->  {link.url}")
