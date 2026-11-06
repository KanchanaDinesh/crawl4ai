from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="assistant"),
    path("ask/", views.ask, name="assistant_ask"),
    path("usage/", views.usage, name="assistant_usage"),
    path("questions/<int:pk>/", views.question_page, name="assistant_question"),
    path("questions/<int:pk>/save/", views.save, name="assistant_save"),
    path("saved/<int:pk>/", views.saved_page, name="assistant_saved"),
]
