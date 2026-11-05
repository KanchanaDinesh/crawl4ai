"""Sign in, own password, and the administrator's screens: users, roles, and which roles may open which menu.

Safety rules: users are never deleted (deactivated instead, so their history stays); an administrator can't lock
themselves out (deactivate themselves or drop their own Administrator role); built-in roles can't be renamed or
deleted; a role with members can't be deleted. Every change goes to the audit log in the same transaction.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from webapp.audit.log import record

from .forms import RoleForm, UserForm
from .models import Menu, RoleProfile
from .rights import ADMINISTRATOR, menu_required

User = get_user_model()


class SignIn(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


@login_required
def my_account(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            record(request, "password_changed", f"{user.get_username()} changed their own password",
                   target_type="user", target_id=user.pk)
        update_session_auth_hash(request, user)
        messages.success(request, "Your password is changed.")
        return redirect("my_account")
    return render(request, "accounts/my_account.html", {"form": form})


# ── users ────────────────────────────────────────────────────────────────────────────────────────────────────
@menu_required("admin_users")
def users(request):
    q = request.GET.get("q", "").strip()
    rows = User.objects.prefetch_related("groups").order_by("-is_active", "username")
    if q:
        rows = rows.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                           | Q(email__icontains=q))
    return render(request, "accounts/users.html", {"users": rows, "q": q})


def _would_lock_out(request, user, form) -> str | None:
    if user.pk != request.user.pk:
        return None
    if not form.cleaned_data["is_active"]:
        return "You can't deactivate your own account."
    was_admin = user.groups.filter(name=ADMINISTRATOR).exists()
    stays_admin = any(g.name == ADMINISTRATOR for g in form.cleaned_data["roles"])
    if was_admin and not stays_admin and not user.is_superuser:
        return "You can't remove your own Administrator role (ask another administrator)."
    return None


@menu_required("admin_users")
def user_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else User()
    creating = pk is None
    form = UserForm(request.POST or None, instance=user, creating=creating)
    if request.method == "POST" and form.is_valid():
        problem = None if creating else _would_lock_out(request, user, form)
        if problem:
            form.add_error(None, problem)
        else:
            with transaction.atomic():
                before = {} if creating else {"active": User.objects.get(pk=user.pk).is_active,
                                              "roles": sorted(user.groups.values_list("name", flat=True))}
                user = form.save(commit=False)
                if form.cleaned_data.get("password"):
                    user.set_password(form.cleaned_data["password"])
                user.save()
                user.groups.set(form.cleaned_data["roles"])
                after = {"active": user.is_active, "roles": sorted(g.name for g in form.cleaned_data["roles"])}
                name = user.get_username()
                if creating:
                    record(request, "user_created", f"User {name} created with roles {', '.join(after['roles']) or 'none'}",
                           target_type="user", target_id=user.pk, detail=after)
                else:
                    record(request, "user_updated", f"User {name} changed", target_type="user", target_id=user.pk,
                           detail={"before": before, "after": after, "fields": form.changed_data})
                    if before["active"] != after["active"]:
                        record(request, "user_activated" if after["active"] else "user_deactivated",
                               f"User {name} {'activated' if after['active'] else 'deactivated'}",
                               target_type="user", target_id=user.pk)
                if form.cleaned_data.get("password") and not creating:
                    record(request, "password_set", f"Password set for {name} by an administrator",
                           target_type="user", target_id=user.pk)
            messages.success(request, f"Saved {user.get_username()}.")
            return redirect("users")
    return render(request, "accounts/user_edit.html", {"form": form, "creating": creating, "subject": user})


# ── roles and rights ─────────────────────────────────────────────────────────────────────────────────────────
@menu_required("admin_roles")
def roles(request):
    groups = Group.objects.annotate(members=Count("user", distinct=True)).select_related("profile").order_by("name")
    menus = list(Menu.objects.prefetch_related("roles"))
    rights = {(m.pk, g.pk) for m in menus for g in m.roles.all()}
    matrix = [{"menu": m, "cells": [{"group": g, "on": (m.pk, g.pk) in rights} for g in groups]} for m in menus]
    return render(request, "accounts/roles.html", {"groups": groups, "matrix": matrix,
                                                   "sections": dict(Menu.SECTIONS)})


@require_POST
@menu_required("admin_roles")
def rights_save(request):
    """The whole matrix in one form: a checkbox `r_<menu id>_<group id>` per allowed pair."""
    wanted = set()
    for key in request.POST:
        if key.startswith("r_"):
            _, m, g = key.split("_")
            wanted.add((int(m), int(g)))
    admin = Group.objects.filter(name=ADMINISTRATOR).first()
    with transaction.atomic():
        changes = []
        for menu in Menu.objects.prefetch_related("roles"):
            have = {g.pk for g in menu.roles.all()}
            want = {g for (m, g) in wanted if m == menu.pk}
            if admin is not None and menu.section == Menu.SECTION_ADMIN:
                want.add(admin.pk)       # the Administrator role always keeps the administration menus
            if have != want:
                menu.roles.set(want)
                added = sorted(Group.objects.filter(pk__in=want - have).values_list("name", flat=True))
                removed = sorted(Group.objects.filter(pk__in=have - want).values_list("name", flat=True))
                changes.append({"menu": menu.code, "added": added, "removed": removed})
        if changes:
            record(request, "rights_changed", "Menu rights changed: " + "; ".join(
                f"{c['menu']} " + " ".join([f"+{n}" for n in c["added"]] + [f"-{n}" for n in c["removed"]])
                for c in changes), target_type="menu", detail={"changes": changes})
    messages.success(request, "Menu rights saved." if changes else "Nothing changed.")
    return redirect("roles")


@menu_required("admin_roles")
def role_edit(request, pk=None):
    group = get_object_or_404(Group, pk=pk) if pk else None
    profile = RoleProfile.objects.filter(group=group).first() if group else None
    initial = {"name": group.name, "description": profile.description if profile else ""} if group else {}
    form = RoleForm(request.POST or None, initial=initial, group=group)
    if profile and profile.builtin:
        form.fields["name"].disabled = True
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            if group is None:
                group = Group.objects.create(name=form.cleaned_data["name"])
                RoleProfile.objects.create(group=group, description=form.cleaned_data["description"])
                record(request, "role_created", f"Role {group.name} created", target_type="role", target_id=group.pk)
            else:
                old = group.name
                if not (profile and profile.builtin):
                    group.name = form.cleaned_data["name"]
                    group.save()
                RoleProfile.objects.update_or_create(group=group, defaults={
                    "description": form.cleaned_data["description"]})
                record(request, "role_updated", f"Role {old} changed" + (f" (now {group.name})" if old != group.name else ""),
                       target_type="role", target_id=group.pk)
        messages.success(request, f"Saved role {group.name}. Set what it may open in the table below.")
        return redirect("roles")
    return render(request, "accounts/role_edit.html", {"form": form, "group": group, "profile": profile})


@require_POST
@menu_required("admin_roles")
def role_delete(request, pk):
    group = get_object_or_404(Group, pk=pk)
    profile = RoleProfile.objects.filter(group=group).first()
    if profile and profile.builtin:
        messages.error(request, f"{group.name} is a built-in role and can't be deleted.")
    elif group.user_set.exists():
        messages.error(request, f"{group.name} still has members. Take them out of the role first.")
    else:
        with transaction.atomic():
            name = group.name
            record(request, "role_deleted", f"Role {name} deleted", target_type="role", target_id=group.pk)
            group.delete()
        messages.success(request, f"Deleted role {name}.")
    return redirect("roles")
