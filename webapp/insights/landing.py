"""/dashboard/: straight to the first tab this user may open."""

from django.shortcuts import redirect, render
from django.urls import reverse

from webapp.accounts.rights import menu_required

from .common import allowed_tabs


@menu_required("dashboard")
def landing(request):
    tabs = allowed_tabs(request.user)
    if tabs:
        return redirect(reverse(tabs[0].url_name))
    return render(request, "insights/no_tabs.html")
