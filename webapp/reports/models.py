"""Reports, on demand or on a schedule: a dashboard as a PDF, or a newsletter made of sections that each take the latest rows of a dataset.
Each one made is kept as a file, with a row here to find it by."""

from django.conf import settings
from django.db import models

from webapp.catalog.models import Dataset
from webapp.charts.models import Dashboard


class ReportTemplate(models.Model):
    """How a report looks, apart from what's in it: people choose colours, fonts, a logo and a page layout from a form. Every value ends up in
    the PDF's stylesheet, so each one is a choice from these lists or a checked colour (`designs.apply_design`), never free text. An unsaved
    one, with these defaults, is the plain design every report had before templates."""

    FONTS = {"sans": 'Helvetica, Arial, "Noto Sans", sans-serif', "serif": 'Georgia, "Times New Roman", "Noto Serif", serif'}
    FONT_NAMES = {"sans": "Clean (Helvetica, Arial)", "serif": "Bookish (Georgia, Times)"}
    TEXT_SIZES = {"small": "Smaller", "normal": "Normal", "large": "Larger"}
    SCALES = {"small": 0.92, "normal": 1.0, "large": 1.1}
    MASTHEADS = {"line": "Title under a coloured line", "band": "Title on a coloured band", "centered": "Title in the centre"}
    HEADINGS = {"underlined": "Coloured, with a line under", "plain": "Coloured, no line", "band": "On a coloured band"}
    LOGO_PLACES = {"right": "On the right", "left": "On the left", "center": "In the centre, above the title"}
    LOGO_SIZES = {"small": "Small", "medium": "Medium", "large": "Large"}
    LOGO_HEIGHTS = {"small": 28, "medium": 44, "large": 64}
    PAGES = {"A4": "A4", "Letter": "US Letter"}
    ORIENTATIONS = {"portrait": "Upright", "landscape": "Sideways"}
    MARGINS = {"normal": "Normal", "narrow": "Narrow", "wide": "Wide"}
    MARGIN_SIZES = {"normal": "18mm 16mm", "narrow": "12mm 12mm", "wide": "24mm 22mm"}
    COLUMNS = {2: "Two columns", 1: "One column"}
    COLOURS = {"accent": "Main colour", "heading": "Headings", "text": "Text", "muted": "Small print", "tint": "Shaded box", "rule": "Thin lines"}
    CHOICES = {"body_font": FONT_NAMES, "heading_font": FONT_NAMES, "text_size": TEXT_SIZES, "masthead": MASTHEADS, "heading_style": HEADINGS,
               "logo_place": LOGO_PLACES, "logo_size": LOGO_SIZES, "page_size": PAGES, "orientation": ORIENTATIONS, "margins": MARGINS}

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="report_templates")
    name = models.CharField(max_length=80)
    shared = models.BooleanField(default=False, help_text="Everyone with Report templates can use it and copy it")
    accent = models.CharField(max_length=7, default="#0b6e8a")
    heading = models.CharField(max_length=7, default="#18232b")
    text = models.CharField(max_length=7, default="#18232b")
    muted = models.CharField(max_length=7, default="#5b6a75")
    tint = models.CharField(max_length=7, default="#eef2f4")
    rule = models.CharField(max_length=7, default="#d7dfe4")
    body_font = models.CharField(max_length=10, default="sans")
    heading_font = models.CharField(max_length=10, default="sans")
    text_size = models.CharField(max_length=10, default="normal")
    masthead = models.CharField(max_length=10, default="line")
    heading_style = models.CharField(max_length=10, default="underlined")
    columns = models.PositiveSmallIntegerField(default=2)
    show_contents = models.BooleanField(default=True, help_text="The list of sections at the start of a newsletter")
    logo = models.TextField(blank=True, help_text="A checked PNG, as a data address, so a PDF never has to fetch it")
    logo_place = models.CharField(max_length=10, default="right")
    logo_size = models.CharField(max_length=10, default="medium")
    page_size = models.CharField(max_length=10, default="A4")
    orientation = models.CharField(max_length=10, default="portrait")
    margins = models.CharField(max_length=10, default="normal")
    header_text = models.CharField(max_length=120, blank=True)
    footer_text = models.CharField(max_length=120, blank=True, help_text="Empty: the report's name")
    show_page_numbers = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name

    @property
    def body_stack(self) -> str:
        return self.FONTS.get(self.body_font, self.FONTS["sans"])

    @property
    def heading_stack(self) -> str:
        return self.FONTS.get(self.heading_font, self.FONTS["sans"])

    @property
    def scale(self) -> float:
        return self.SCALES.get(self.text_size, 1.0)

    @property
    def logo_height(self) -> int:
        return self.LOGO_HEIGHTS.get(self.logo_size, 44)

    @property
    def page_margin(self) -> str:
        return self.MARGIN_SIZES.get(self.margins, self.MARGIN_SIZES["normal"])

    @property
    def classes(self) -> str:
        return f"m-{self.masthead} h-{self.heading_style}"

    @property
    def on_accent(self) -> str:
        """Text on the main colour: whichever of black and white can be read on it, so nobody has to think about it when choosing."""
        red, green, blue = (int(self.accent[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return "#18232b" if 0.2126 * red ** 2.2 + 0.7152 * green ** 2.2 + 0.0722 * blue ** 2.2 > 0.3 else "#ffffff"


class Report(models.Model):
    DASHBOARD, NEWSLETTER = "dashboard", "newsletter"
    NEVER, DAILY, WEEKLY, MONTHLY = "", "daily", "weekly", "monthly"
    SCHEDULES = {NEVER: "Only when asked", DAILY: "Every day", WEEKLY: "Every week", MONTHLY: "Every month"}
    WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports")
    name = models.CharField(max_length=120)
    intro = models.TextField(blank=True, max_length=3000)
    kind = models.CharField(max_length=10, default=DASHBOARD)
    dashboard = models.ForeignKey(Dashboard, null=True, blank=True, on_delete=models.CASCADE, related_name="reports")
    template = models.ForeignKey(ReportTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports", help_text="How it looks. Empty: the plain design")
    include_tables = models.BooleanField(default=True, help_text="Print the numbers under each chart")
    schedule = models.CharField(max_length=7, blank=True, default=NEVER)
    weekday = models.PositiveSmallIntegerField(default=0)
    shared = models.BooleanField(default=False)
    last_scheduled = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name

    def get_weekday_name(self) -> str:
        return self.WEEKDAYS[self.weekday]

    @property
    def is_newsletter(self) -> bool:
        return self.kind == self.NEWSLETTER

    @property
    def design(self) -> ReportTemplate:
        return self.template or ReportTemplate()


class ReportSection(models.Model):
    """One part of a newsletter. Stores what was chosen (field paths from the catalog), never rows: it's worked out when the report is made, with the rights of whoever it's for."""

    ARTICLES, TABLE = "articles", "table"
    LAYOUTS = {ARTICLES: "Articles: a heading and a few lines each", TABLE: "Table: one line each"}
    PERIODS = {0: "Everything", 1: "The last 24 hours", 2: "The last 2 days", 7: "The last 7 days", 30: "The last 30 days"}
    MAX_ITEMS = 100

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="sections")
    order = models.PositiveSmallIntegerField(default=0)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="report_sections")
    heading = models.CharField(max_length=120)
    layout = models.CharField(max_length=10, default=ARTICLES)
    title_field = models.CharField(max_length=200, blank=True)
    text_field = models.CharField(max_length=200, blank=True)
    link_field = models.CharField(max_length=200, blank=True)
    detail_fields = models.JSONField(default=list, blank=True)
    date_field = models.CharField(max_length=200, blank=True)
    days = models.PositiveSmallIntegerField(default=0, help_text="Only rows this recent, by date_field. 0: all of them")
    sort_field = models.CharField(max_length=200, blank=True)
    sort_desc = models.BooleanField(default=True)
    limit = models.PositiveSmallIntegerField(default=10, help_text="0: only the count and the breakdown")
    breakdown_field = models.CharField(max_length=200, blank=True)
    filters = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.heading

    def period_label(self) -> str:
        return self.PERIODS.get(self.days, f"The last {self.days} days")


class ReportRun(models.Model):
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="runs")
    made_at = models.DateTimeField(auto_now_add=True)
    made_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    scheduled = models.BooleanField(default=False)
    filename = models.CharField(max_length=200, blank=True)
    size = models.PositiveIntegerField(default=0)
    seconds = models.FloatField(default=0)
    problem = models.TextField(blank=True)

    class Meta:
        ordering = ["-made_at", "-id"]
