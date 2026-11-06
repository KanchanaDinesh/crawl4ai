from django.urls import path

from . import views

urlpatterns = [
    path("", views.dataset_list, name="datasets"),
    path("<str:ref>/", views.dataset_page, name="dataset"),
    path("<str:ref>/rows/", views.rows, name="dataset_rows"),
    path("<str:ref>/export.<str:fmt>", views.export_file, name="dataset_export"),
    path("<str:ref>/views/", views.view_save, name="dataset_view_save"),
    path("<str:ref>/views/<int:pk>/delete/", views.view_delete, name="dataset_view_delete"),
    path("<str:ref>/edit/", views.edit_cell, name="dataset_edit_cell"),
    path("<str:ref>/edit-many/", views.edit_bulk, name="dataset_edit_bulk"),
    path("<str:ref>/hide/", views.hide_rows, name="dataset_hide"),
    path("<str:ref>/unhide/", views.unhide_row, name="dataset_unhide"),
    path("<str:ref>/history/", views.history, name="dataset_history"),
    path("<str:ref>/restore/", views.restore, name="dataset_restore"),
    path("<str:ref>/review/", views.review, name="dataset_review"),
    path("<str:ref>/changes/", views.changes, name="dataset_changes"),
]
