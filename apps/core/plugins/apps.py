from django.apps import AppConfig


class PluginsConfig(AppConfig):
    name = "plugins"

    def ready(self) -> None:
        # Load self-registering plugin hooks once at startup: env-configured
        # `module:function` paths plus `openmagpie.plugins` entry points (subject
        # to the allowlist). See plugins.loader.
        from django.conf import settings

        from plugins.loader import load_hooks

        load_hooks(
            settings.PLUGIN_HOOKS,
            entry_group="openmagpie.plugins",
            allow=settings.PLUGIN_ENTRYPOINT_ALLOW,
        )
