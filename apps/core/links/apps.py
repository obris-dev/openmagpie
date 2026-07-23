from django.apps import AppConfig


class LinksConfig(AppConfig):
    name = "links"

    def ready(self) -> None:
        from . import checks  # noqa: F401  registers the SHORTLINK_HOST system check
