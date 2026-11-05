from django.urls import path

from . import views

urlpatterns = [
    path("", views.page, name="grid_test"),
    path("api/rows/", views.rows, name="grid_test_rows"),
    path("api/edit/", views.edit, name="grid_test_edit"),
    path("api/bulk/", views.bulk, name="grid_test_bulk"),
    path("api/reset/", views.reset, name="grid_test_reset"),
]
