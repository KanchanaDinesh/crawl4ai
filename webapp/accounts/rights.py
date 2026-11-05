"""Who may open what. Every page checks this on the server (the menu only hides what a user can't open; hiding is
never the protection)."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

ADMINISTRATOR = "Administrator"


def allowed_menus(user) -> set[str]:
    """Codes of the menus this user may open (cached on the user object for the request)."""
    if not user.is_authenticated or not user.is_active:
        return set()
    cached = getattr(user, "_allowed_menus", None)
    if cached is not None:
        return cached
    from .models import Menu
    if user.is_superuser:
        codes = set(Menu.objects.values_list("code", flat=True))
    else:
        codes = set(Menu.objects.filter(roles__user=user).values_list("code", flat=True))
    user._allowed_menus = codes
    return codes


def can_open(user, code: str) -> bool:
    return code in allowed_menus(user)


def menu_required(code: str):
    """View decorator: signed in, and one of the user's roles may open menu `code` (else 403)."""
    def decorate(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not can_open(request.user, code):
                raise PermissionDenied(f"Your roles don't include the menu '{code}'.")
            return view(request, *args, **kwargs)
        return wrapper
    return decorate
