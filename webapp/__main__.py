"""python -m webapp <command>: Django's manage.py for this project."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webapp.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line([sys.argv[0].replace("__main__.py", "webapp"), *sys.argv[1:]])


if __name__ == "__main__":
    main()
