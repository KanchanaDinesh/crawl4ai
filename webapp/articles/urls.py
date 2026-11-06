from django.urls import path

from . import views

urlpatterns = [
    path("", views.page_view, name="articles"),
    path("rows/", views.rows_view, name="articles_rows"),
    path("read/", views.read_view, name="articles_read"),
    path("companies/", views.companies_view, name="articles_companies"),
    path("companies/rows/", views.company_rows_view, name="articles_company_rows"),
]
