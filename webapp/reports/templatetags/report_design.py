"""What the report's stylesheet needs from a template."""

from django import template
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
