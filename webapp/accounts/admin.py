"""Django's own admin (at /django-admin/, superusers only) as a fallback; the app's screens are under /admin/."""

from django.contrib import admin

from .models import Menu, RoleProfile


@admin.register(Menu)
class MenuAdmin(admin.ModelAdmin):
    list_display = ("label", "code", "section", "order", "phase")
    filter_horizontal = ("roles",)


admin.site.register(RoleProfile)
