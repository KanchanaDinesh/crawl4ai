from django.urls import path

from . import views

urlpatterns = [
    path("", views.overview, name="reports"),
    path("<int:pk>/", views.report_page, name="report"),
    path("<int:pk>/preview/", views.preview, name="report_preview"),
    path("<int:pk>/make/", views.make_now, name="report_make"),
    path("<int:pk>/delete/", views.delete_report, name="report_delete"),
    path("<int:pk>/files/<int:run_id>/", views.download, name="report_download"),
]
