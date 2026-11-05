from django.urls import path

from . import views

urlpatterns = [
    path("", views.catalog, name="catalog"),
    path("scan/", views.scan_now, name="catalog_scan"),
    path("rights/", views.rights, name="dataset_rights"),
    path("<int:pk>/", views.dataset_edit, name="catalog_dataset"),
]
