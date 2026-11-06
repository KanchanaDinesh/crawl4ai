from django.urls import path

from webapp.userdata import views as own

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
    path("<str:ref>/fields/", own.fields_page, name="dataset_fields"),
    path("<str:ref>/fields/options/", own.field_options, name="dataset_field_options"),
    path("<str:ref>/fields/<int:pk>/", own.field_change, name="dataset_field_change"),
    path("<str:ref>/link-options/", own.link_options, name="dataset_link_options"),
    path("<str:ref>/link-values/", own.link_values, name="dataset_link_values"),
    path("<str:ref>/links/", own.set_links, name="dataset_links"),
    path("<str:ref>/choice/", own.add_choice, name="dataset_choice"),
    path("<str:ref>/add-row/", own.add_row, name="dataset_add_row"),
    path("<str:ref>/save-as-table/", own.save_as_table, name="dataset_save_table"),
    path("<str:ref>/share/", own.share, name="dataset_share"),
    path("<str:ref>/delete-table/", own.delete_table, name="dataset_delete_table"),
    path("<str:ref>/add-from-file/", own.add_from_file, name="dataset_add_from_file"),
]
