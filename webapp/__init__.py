"""The user platform: an internal web app on top of the collected data (docs/user-platform-plan.md).

Django + HTMX, Python only. Its own tables live in PostgreSQL schema `app` (login crawl4ai_app); it reads the
collected data in the other schemas. Run it:

    uv run python -m webapp migrate          create or update the app's tables
    uv run python -m webapp createadmin      the first administrator (asks for a password)
    uv run python -m webapp runserver 8060   http://localhost:8060

Any other Django management command works the same way (python -m webapp <command>).
"""

import project_paths

project_paths.apply_local_env()
