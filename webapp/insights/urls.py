from django.urls import path

from . import companies, markets, news, tech, vehicles
from .landing import landing

urlpatterns = [
    path("", landing, name="dashboard"),
    path("news/", news.page_view, name="insights_news"),
    path("news/items/", news.items_view, name="insights_news_items"),
    path("news/analysis/", news.analysis_view, name="insights_news_analysis"),
    path("tech/", tech.page_view, name="insights_tech"),
    path("tech/rows/", tech.rows_view, name="insights_tech_rows"),
    path("tech/site/<str:domain>/", tech.site_view, name="insights_tech_site"),
    path("markets/", markets.page_view, name="insights_markets"),
    path("markets/prices/", markets.prices_view, name="insights_prices"),
    path("markets/compare/", markets.compare_view, name="insights_compare"),
    path("markets/symbol/", markets.symbol_view, name="insights_symbol"),
    path("vehicles/", vehicles.page_view, name="insights_vehicles"),
    path("vehicles/ads/", vehicles.ads_view, name="insights_vehicle_ads"),
    path("companies/", companies.page_view, name="insights_companies"),
    path("companies/rows/", companies.rows_view, name="insights_company_rows"),
    path("companies/company/<path:company_id>/", companies.detail_view, name="insights_company"),
]
