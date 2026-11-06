from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="foresight"),
    path("forecast/<int:forecast_id>/", views.forecast_view, name="foresight_forecast"),
    path("markets/", views.section_view, {"code": "markets"}, name="foresight_markets"),
    path("news/", views.section_view, {"code": "news"}, name="foresight_news"),
    path("tech/", views.section_view, {"code": "tech"}, name="foresight_tech"),
    path("vehicles/", views.section_view, {"code": "vehicles"}, name="foresight_vehicles"),
    path("companies/", views.section_view, {"code": "companies"}, name="foresight_companies"),
    path("<str:code>/rows/", views.rows_view, name="foresight_rows"),
]
