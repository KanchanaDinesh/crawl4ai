from django.urls import path

from . import views

urlpatterns = [
    path("", views.overview, name="charts"),
    path("new/", views.builder, name="chart_new"),
    path("fields/", views.builder_fields, name="chart_fields"),
    path("preview/", views.preview, name="chart_preview"),
    path("dashboards/new/", views.new_dashboard, name="dashboard_new"),
    path("dashboards/<int:pk>/", views.dashboard_page, name="dashboard_page"),
    path("dashboards/<int:pk>/change/", views.dashboard_change, name="dashboard_change"),
    path("dashboards/<int:pk>/delete/", views.delete_dashboard, name="dashboard_delete"),
    path("<int:pk>/", views.builder, name="chart"),
    path("<int:pk>/tile/", views.tile, name="chart_tile"),
    path("<int:pk>/delete/", views.delete_chart, name="chart_delete"),
]
