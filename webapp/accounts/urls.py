from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.SignIn.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("account/", views.my_account, name="my_account"),
    path("admin/users/", views.users, name="users"),
    path("admin/users/new/", views.user_edit, name="user_new"),
    path("admin/users/<int:pk>/", views.user_edit, name="user_edit"),
    path("admin/roles/", views.roles, name="roles"),
    path("admin/roles/rights/", views.rights_save, name="rights_save"),
    path("admin/roles/new/", views.role_edit, name="role_new"),
    path("admin/roles/<int:pk>/", views.role_edit, name="role_edit"),
    path("admin/roles/<int:pk>/delete/", views.role_delete, name="role_delete"),
]
