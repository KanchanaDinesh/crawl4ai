from django.urls import path

from . import views

urlpatterns = [
    path("", views.my_tables, name="my_tables"),
    path("new/", views.new_table, name="my_tables_new"),
    path("import/", views.upload, name="my_tables_upload"),
    path("import/<str:token>/", views.import_preview, name="my_tables_import"),
]
