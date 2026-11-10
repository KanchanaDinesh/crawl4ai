"""python -m webapp starter_charts: the ready-made dashboards, one per section (webapp/charts/starters.py). Safe to repeat."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from webapp.charts import starters


class Command(BaseCommand):
    help = "Make the ready-made dashboards (Markets, World news, Tech intel, Companies, Vehicles, Articles). One already there is left alone."

    def add_arguments(self, parser):
        parser.add_argument("--owner", help="whose they are (default: the only administrator account)")
        parser.add_argument("--private", action="store_true", help="don't share them (by default everyone with the Charts menu sees them, with their own rights)")
        parser.add_argument("--check", action="store_true", help="work out every chart and say what would be made, but save nothing")

    def _owner(self, name):
        users = get_user_model().objects.filter(is_active=True)
        if name:
            found = users.filter(username=name).first()
            if found is None:
                raise CommandError(f"No active account called {name}. Check the Users page.")
            return found
        admins = list(users.filter(is_superuser=True))
        if len(admins) != 1:
            raise CommandError(f"There are {len(admins)} administrator accounts, so say whose they'll be: --owner NAME")
        return admins[0]

    def handle(self, *args, **options):
        owner = self._owner(options["owner"])
        out = starters.build(owner, shared=not options["private"], save=not options["check"])
        verb = "Would make" if options["check"] else "Made"
        for name, n in out.made.items():
            self.stdout.write(f"{verb} “{name}” with {n} charts")
        for name in out.kept:
            self.stdout.write(f"“{name}” is already there, so it was left as it is")
        for board, chart, why in out.left_out:
            self.stdout.write(f"Left out of “{board}”: {chart}: {why}")
        for board, chart in out.empty:
            self.stdout.write(f"In “{board}”, {chart} has no rows yet; it fills in once the tool has collected them")
        if not out.made and not out.kept:
            raise CommandError("Nothing could be made. Run `uv run python -m webapp catalog_scan` first, then try again.")
        if out.made and not options["check"]:
            self.stdout.write(f"Open them under Charts (owner {owner.get_username()}).")
