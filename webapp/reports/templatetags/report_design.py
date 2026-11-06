"""What the report's stylesheet needs from a template."""

from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def pt(context, size: float) -> str:
    """A font size, scaled by the template's text size. Worked out here because the same stylesheet must behave the same in a PDF and in the app's page, whose base sizes differ."""
    design = context.get("design")
    return f"{round(size * (design.scale if design else 1), 2):g}pt"


@register.filter
def css_string(value) -> str:
    """Text for `content:` in a stylesheet. People type it, so everything but letters, digits and spaces is written as a CSS escape: it can't end the string or the style block."""
    text = " ".join(str(value).split())
    return mark_safe('"' + "".join(c if c.isalnum() or c == " " else f"\\{ord(c):x} " for c in text) + '"')


@register.simple_tag(takes_context=True)
def isolated_sheet(context) -> str:
    """The sheet as a page of its own, for a sandboxed frame. Someone's own CSS can't be tied down completely (position: fixed could cover the screen),
    so on screen it never shares a page with the app. A rendered template counts as safe, so `_sheet.html` must force the escape for the frame's srcdoc."""
    return render_to_string("reports/_sheet_page.html", context.flatten())
