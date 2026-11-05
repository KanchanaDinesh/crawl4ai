"""The old aiohttp dashboard was retired on 2026-11-06: its five tabs are now pages of the web app (webapp/insights/).
This command stays only to say where they went."""

print("The dashboard is now part of the web app. Start it with:\n\n"
      "    uv run python -m webapp runserver 8060\n\n"
      "then open http://localhost:8060/dashboard/ and sign in.")
