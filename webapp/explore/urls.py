from django.urls import path

from . import views

urlpatterns = [
    path("", views.dataset_list, name="datasets"),
    path("<str:ref>/", views.dataset_page, name="dataset"),
    path("<str:ref>/rows/", views.rows, name="dataset_rows"),
    path("<str:ref>/export.<str:fmt>", views.export_file, name="dataset_export"),
    path("<str:ref>/views/", views.view_save, name="dataset_view_save"),
    path("<str:ref>/views/<int:pk>/delete/", views.view_delete, name="dataset_view_delete"),
]
