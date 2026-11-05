"""Roles and menu rights.

A role is a Django group (auth.Group), with a RoleProfile for its description and whether it's built in. Rights
are given per menu: Menu.roles lists the roles that may open it. A user may open a menu when they are a
superuser or one of their roles is in its list; their rights add up over all their roles.

Dataset rights (view, edit, hide, export, use in AI per dataset) come with the catalog in phase 2.
"""

from django.contrib.auth.models import Group
from django.db import models


class Menu(models.Model):
    SECTION_WORK = "work"
    SECTION_ADMIN = "admin"
    SECTIONS = [(SECTION_WORK, "Work"), (SECTION_ADMIN, "Administration")]

    code = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=60)
    url_name = models.CharField(max_length=60, help_text="Django URL name the menu item opens")
    section = models.CharField(max_length=10, choices=SECTIONS, default=SECTION_WORK)
    order = models.PositiveSmallIntegerField(default=100)
    description = models.CharField(max_length=200, blank=True)
    phase = models.PositiveSmallIntegerField(null=True, blank=True,
                                             help_text="Plan phase that builds this page; empty when it's built")
    roles = models.ManyToManyField(Group, blank=True, related_name="menus")

    class Meta:
        ordering = ["section", "order", "label"]

    def __str__(self) -> str:
        return self.label


class RoleProfile(models.Model):
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="profile")
    description = models.CharField(max_length=200, blank=True)
    builtin = models.BooleanField(default=False, help_text="Built-in roles can't be renamed or deleted")

    def __str__(self) -> str:
        return self.group.name
