from django.urls import path

from . import template_views as views

urlpatterns = [
    path("", views.overview, name="report_templates"),
    path("new/", views.new, name="report_template_new"),
    path("preview/", views.preview, name="report_template_preview"),
    path("<int:pk>/", views.page, name="report_template"),
    path("<int:pk>/copy/", views.copy, name="report_template_copy"),
    path("<int:pk>/delete/", views.delete, name="report_template_delete"),
]
