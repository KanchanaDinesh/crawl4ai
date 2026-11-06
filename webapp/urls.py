"""All pages of the web app. Each page checks its own menu right (webapp/accounts/rights.py)."""

from django.contrib import admin
from django.urls import include, path

from webapp.core import views as core

urlpatterns = [
    path("", core.home, name="home"),
    path("", include("webapp.accounts.urls")),
    path("admin/audit/", include("webapp.audit.urls")),
    path("grid-test/", include("webapp.gridtest.urls")),
    path("datasets/", include("webapp.explore.urls")),
    path("admin/catalog/", include("webapp.catalog.urls")),
    path("dashboard/", include("webapp.insights.urls")),
    # pages of later phases: the menu items exist now, so rights can be set up front
    path("my-tables/", include("webapp.userdata.urls")),
    path("charts/", include("webapp.charts.urls")),
    path("reports/", include("webapp.reports.urls")),
    path("report-templates/", include("webapp.reports.template_urls")),
    path("assistant/", include("webapp.assistant.urls")),
    path("django-admin/", admin.site.urls),
]

handler403 = "webapp.core.views.forbidden"
handler404 = "webapp.core.views.not_found"
