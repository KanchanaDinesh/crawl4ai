"""{% asset 'js/datagrid.js' %}: the static URL plus the file's change time (?v=...), so a browser never keeps an
old copy of our own scripts and styles after an update. Library files have their version in their folder name."""

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def asset(path: str) -> str:
    import os
    found = finders.find(path)
    version = int(os.path.getmtime(found)) if found else 0
    return f"{static(path)}?v={version}"
