"""The audit log screen: newest first, filtered by person, action and date, 50 per page."""

from datetime import datetime, time

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone

from webapp.accounts.rights import menu_required

from .log import ACTIONS
from .models import AuditEvent


def _day(value: str, end: bool = False):
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return timezone.make_aware(datetime.combine(d, time.max if end else time.min))


@menu_required("admin_audit")
def audit_log(request):
    f = {k: request.GET.get(k, "").strip() for k in ("user", "action", "from", "to", "q")}
    events = AuditEvent.objects.select_related("user")
    if f["user"]:
        events = events.filter(username=f["user"])
    if f["action"]:
        events = events.filter(action=f["action"])
    if _day(f["from"]):
        events = events.filter(at__gte=_day(f["from"]))
    if _day(f["to"], end=True):
        events = events.filter(at__lte=_day(f["to"], end=True))
    if f["q"]:
        events = events.filter(summary__icontains=f["q"])
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    names = get_user_model().objects.order_by("username").values_list("username", flat=True)
    query = "&".join(f"{k}={v}" for k, v in f.items() if v)
    return render(request, "audit/log.html", {"page": page, "f": f, "actions": ACTIONS, "names": names,
                                              "query": query})
