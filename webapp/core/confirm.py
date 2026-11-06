"""Asking "are you sure?" before something is deleted.

It's asked by the server, not by a script in the page, so no way of reaching a delete can skip it: an old page still open in a
browser, a script that didn't load, a double click. Nothing is deleted until the answer comes back with `confirmed` in it."""

from django.shortcuts import render


def ask_first(request, question: str, *, back: str, detail: str = "", yes: str = "Yes, delete it"):
    """The page that asks, or None once they've said yes. Whatever else the form sent goes along with the answer, so the same view
    picks up where it was."""
    if "confirmed" in request.POST:
        return None
    sent = [(k, v) for k, v in request.POST.items() if k != "csrfmiddlewaretoken"]
    return render(request, "core/confirm.html", {"question": question, "detail": detail, "yes": yes, "back": back, "action": request.path, "sent": sent})
