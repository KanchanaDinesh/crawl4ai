from django.urls import path

from . import views

urlpatterns = [
    path("", views.page_view, name="annualreports"),
    path("rows/", views.rows_view, name="annualreports_rows"),
    path("report/<int:doc_id>/", views.report_view, name="annualreports_report"),
    path("report/<int:doc_id>/page/<int:page_no>/", views.page_text_view, name="annualreports_page"),
    path("search/", views.search_view, name="annualreports_search"),
    path("search/rows/", views.hits_view, name="annualreports_hits"),
    path("people/", views.people_view, name="annualreports_people"),
    path("people/rows/", views.people_rows_view, name="annualreports_people_rows"),
]
