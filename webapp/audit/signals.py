"""Sign-ins, sign-outs and failed sign-ins go to the audit log."""

from axes.signals import user_locked_out
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .log import record


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    record(request, "login", f"{user.get_username()} signed in", user=user, target_type="user", target_id=user.pk)


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if user is not None:
        record(request, "logout", f"{user.get_username()} signed out", user=user, target_type="user",
               target_id=user.pk)


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    name = (credentials or {}).get("username", "")
    record(request, "login_failed", f"Failed sign-in for '{name}'", user=None, username=name)


@receiver(user_locked_out)
def _locked_out(sender, request=None, username=None, ip_address=None, **kwargs):
    record(request, "locked_out", f"'{username}' locked out after repeated failed sign-ins", user=None,
           username=username or "")
