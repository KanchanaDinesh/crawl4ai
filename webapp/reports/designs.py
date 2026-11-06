"""Report templates: starting points, reading the form, checking a logo, and made-up content to preview a design with."""

import base64
import io
import re

from .models import Report, ReportTemplate

COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGO_PREFIX = "data:image/png;base64,"
MAX_UPLOAD = 2 * 1024 * 1024
MAX_PIXELS = 25_000_000
LOGO_BOX = (900, 400)

PRESETS = {
    "plain": {"label": "Plain"},
    "newsroom": {"label": "Newsroom", "accent": "#a51c30", "heading": "#16181d", "text": "#23262b", "muted": "#5f646c", "tint": "#f4f1ec", "rule": "#d9d4cc", "heading_font": "serif", "masthead": "centered"},
    "boardroom": {"label": "Boardroom", "accent": "#17345c", "heading": "#17345c", "text": "#1d2733", "muted": "#566273", "tint": "#e9eef5", "rule": "#cfd8e3", "masthead": "band", "heading_style": "band", "columns": 1},
    "quiet": {"label": "Quiet", "accent": "#3c4650", "heading": "#20262c", "text": "#2a3036", "muted": "#68727c", "tint": "#f2f3f4", "rule": "#dcdfe2", "body_font": "serif", "heading_font": "serif", "heading_style": "plain", "margins": "wide"},
}


class DesignProblem(ValueError):
    """What to tell the person."""


def starting_values(preset: str) -> ReportTemplate:
    values = {k: v for k, v in PRESETS.get(preset, PRESETS["plain"]).items() if k != "label"}
    return ReportTemplate(**values)


def apply_design(data, template: ReportTemplate) -> None:
    """Fills in everything but the name and the logo. These values go into a stylesheet, so a colour that isn't one and a choice that isn't on the list are simply not taken."""
    for name in ReportTemplate.COLOURS:
        if COLOUR.match(data.get(name, "")):
            setattr(template, name, data[name].lower())
    for name, allowed in ReportTemplate.CHOICES.items():
        if data.get(name) in allowed:
            setattr(template, name, data[name])
    if data.get("columns", "").isdigit() and int(data["columns"]) in ReportTemplate.COLUMNS:
        template.columns = int(data["columns"])
    template.show_contents = "show_contents" in data
    template.show_page_numbers = "show_page_numbers" in data
    template.header_text = " ".join(data.get("header_text", "").split())[:120]
    template.footer_text = " ".join(data.get("footer_text", "").split())[:120]


def read_logo(upload) -> str:
    """A PNG or JPEG, drawn again as a PNG, so what's kept is only pixels: nothing else in the file reaches a PDF or a page. SVG is refused, because an SVG can carry scripts and fetch things."""
    from PIL import Image, UnidentifiedImageError

    if upload.size > MAX_UPLOAD:
        raise DesignProblem("That logo is over 2 MB. A smaller one is plenty for a page heading.")
    try:
        with Image.open(upload) as image:
            if image.format not in ("PNG", "JPEG"):
                raise DesignProblem("A logo must be a PNG or JPEG picture. If you have an SVG, save it as a PNG first.")
            if image.width * image.height > MAX_PIXELS:
                raise DesignProblem("That picture is too large. Please make it smaller first.")
            image.thumbnail(LOGO_BOX)
            clean = image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        if isinstance(e, DesignProblem):
            raise
        raise DesignProblem("That file isn't a picture that can be read. A logo must be a PNG or JPEG.") from e
    out = io.BytesIO()
    clean.save(out, "PNG", optimize=True)
    return LOGO_PREFIX + base64.b64encode(out.getvalue()).decode("ascii")


def sample_context(template: ReportTemplate) -> dict:
    """Made-up content, so a design can be judged before any report uses it. No dataset is read."""
    from django.utils import timezone

    articles = [{"href": "", "title": title, "text": text, "meta": ["Example source", "Today, 09:30"]} for title, text in (
        ("A headline as it will look in your report", "A few lines of the story go here, so you can see how the text sits under its heading."),
        ("A second story, with a longer heading that runs on to another line", "The summary follows. Sizes, colours and fonts come from the template."),
        ("A third story", "Short ones keep together, and move to the next column or page as a whole."),
        ("And a fourth", "The small print below each story shows its source and date."))]
    bars = [{"label": label, "pct": pct, "count": count} for label, pct, count in (("First group", 100, 42), ("Second group", 62, 26), ("Third group", 29, 12))]
    heads = [{"label": "Name", "num": False}, {"label": "Place", "num": False}, {"label": "Amount", "num": True}]
    lines = [[{"text": name}, {"text": place}, {"text": amount, "num": True}] for name, place, amount in (("First row", "Colombo", "1,250"), ("Second row", "Kandy", "980"), ("Third row", "Galle", "415"))]
    sections = [
        {"anchor": "s1", "heading": "A section of articles", "dataset": "Your dataset", "period": "The last 24 hours", "total": 80, "shown": 4, "more": True, "breakdown": "Group", "bars": bars, "articles": articles},
        {"anchor": "s2", "heading": "A section with a table", "dataset": "Your dataset", "period": "", "total": 3, "shown": 3, "more": False, "heads": heads, "lines": lines},
    ]
    report = Report(name=template.name or "Your report's title", kind=Report.NEWSLETTER)
    return {"report": report, "design": template, "made_at": timezone.localtime(), "issue": 12, "sections": sections,
            "paragraphs": ["An opening text, if the report has one, comes here."]}
