from django.urls import path

from . import views

urlpatterns = [
    path("", views.overview, name="reports"),
    path("new-newsletter/", views.new_newsletter, name="report_new_newsletter"),
    path("<int:pk>/", views.report_page, name="report"),
    path("<int:pk>/sections/new/", views.section_new, name="report_section_new"),
    path("<int:pk>/sections/fields/", views.section_fields, name="report_section_fields"),
    path("<int:pk>/sections/<int:section_id>/", views.section_edit, name="report_section"),
    path("<int:pk>/sections/<int:section_id>/change/", views.section_change, name="report_section_change"),
    path("<int:pk>/preview/", views.preview, name="report_preview"),
    path("<int:pk>/make/", views.make_now, name="report_make"),
    path("<int:pk>/delete/", views.delete_report, name="report_delete"),
    path("<int:pk>/files/<int:run_id>/", views.download, name="report_download"),
]
