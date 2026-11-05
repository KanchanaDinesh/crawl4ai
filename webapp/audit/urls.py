from django.urls import path

from . import views

urlpatterns = [path("", views.audit_log, name="audit")]
