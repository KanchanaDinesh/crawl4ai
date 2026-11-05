"""The side menu: only the items the signed-in user may open (webapp/accounts/rights.py)."""

from django.urls import NoReverseMatch, reverse

from webapp.accounts.rights import allowed_menus


def menu(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"menu_sections": []}
    from webapp.accounts.models import Menu
    codes = allowed_menus(user)
    sections, current = [], request.path
    for code, title in Menu.SECTIONS:
        items = []
        for m in Menu.objects.filter(section=code, code__in=codes):
            try:
                href = reverse(m.url_name)
            except NoReverseMatch:
                continue
            active = current == href or (href != "/" and current.startswith(href))
            items.append({"code": m.code, "label": m.label, "href": href, "active": active, "phase": m.phase})
        if items:
            sections.append({"title": title, "items": items})
    return {"menu_sections": sections}
