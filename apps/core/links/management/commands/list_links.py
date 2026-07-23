"""List short links with click stats: `python manage.py list_links [--code <slug>]`.

`events` is recorded (deduped) click events, not raw hits: repeat visits from one
IP within the dedup window collapse to a single row. `unique` is distinct visitors.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from links.services import ShortLinkService


class Command(BaseCommand):
    help = "List short links with recorded (deduped) events / unique visitors / by-country."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--code", default=None, help="Show one link by its code (else all).")

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["code"]
        if code:
            link = ShortLinkService.find_by_code(code)
            if link is None:
                self.stdout.write(f"no short link with code {code!r}")
                return
            links = [link]
        else:
            links = list(ShortLinkService.iter_all())
        if not links:
            self.stdout.write("no short links yet")
            return
        stats_map = ShortLinkService.stats_for([link.id for link in links])
        for link in links:
            stats = stats_map[link.id]
            geo = ", ".join(f"{country}:{n}" for country, n in stats.by_country.items()) or "-"
            self.stdout.write(f"{link.code:12}  events={stats.total:<5} unique={stats.unique:<5} [{geo}]  {link.url}")
