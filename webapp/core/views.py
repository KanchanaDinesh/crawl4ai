"""Home, the pages of later phases (a short note for now), and the error pages."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from webapp.accounts.models import Menu
from webapp.accounts.rights import menu_required


@login_required
def home(request):
    roles = [g.name for g in request.user.groups.order_by("name")]
    return render(request, "core/home.html", {"roles": roles})


def coming(code: str):
    """A menu item whose page comes in a later phase of the plan: rights are already checked."""
    @menu_required(code)
    def view(request):
        menu = Menu.objects.filter(code=code).first()
        return render(request, "core/coming.html", {"item": menu})
    view.__name__ = f"coming_{code}"
    return view


def forbidden(request, exception=None):
    return render(request, "core/403.html", {"reason": str(exception or "")}, status=403)


def not_found(request, exception=None):
    return render(request, "core/404.html", status=404)
