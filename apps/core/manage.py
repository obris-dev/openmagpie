#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

from dotenv import load_dotenv


def main():
    load_dotenv()
    # Unset DJANGO_ENV defaults to "cloud" (locked-down) so a deploy fails safe;
    # dev opts into "local" via .env. See apps/core/.env.example.
    env = os.environ.get("DJANGO_ENV", "cloud")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"conf.settings.{env}")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
