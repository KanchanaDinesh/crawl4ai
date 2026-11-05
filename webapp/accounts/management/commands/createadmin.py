"""python -m webapp createadmin: the first administrator (or another one). Asks for the password; never stored
anywhere but as a hash in the database."""

import getpass

from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from webapp.accounts.rights import ADMINISTRATOR
from webapp.audit.log import record


class Command(BaseCommand):
    help = "Create an administrator who can open every menu and manage users and roles."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--email", default="")

    def handle(self, *args, username=None, email="", **opts):
        User = get_user_model()
        username = (username or input("User name: ")).strip()
        if not username:
            raise CommandError("A user name is needed.")
        if User.objects.filter(username=username).exists():
            raise CommandError(f"{username} already exists. Give them the Administrator role on the Users page.")
        password = getpass.getpass("Password (at least 10 characters): ")
        if password != getpass.getpass("Same password again: "):
            raise CommandError("The passwords don't match.")
        user = User(username=username, email=email)
        try:
            password_validation.validate_password(password, user)
        except ValidationError as e:
            raise CommandError(" ".join(e.messages)) from e
        with transaction.atomic():
            user.set_password(password)
            user.is_staff = user.is_superuser = True
            user.save()
            admin_role = Group.objects.filter(name=ADMINISTRATOR).first()
            if admin_role:
                user.groups.add(admin_role)
            record(None, "user_created", f"Administrator {username} created from the command line",
                   target_type="user", target_id=user.pk, user=user)
        self.stdout.write(self.style.SUCCESS(f"Administrator {username} created. Sign in at http://localhost:8060/"))
