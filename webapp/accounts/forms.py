"""Forms for the administrator's screens. Passwords go through Django's validators (length, common, similar)."""

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import Group

User = get_user_model()


class RolesField(forms.ModelMultipleChoiceField):
    widget = forms.CheckboxSelectMultiple

    def label_from_instance(self, obj):
        return obj.name


class UserForm(forms.ModelForm):
    roles = RolesField(queryset=Group.objects.order_by("name"), required=False)
    password = forms.CharField(label="Password", widget=forms.PasswordInput(render_value=False), required=False,
                               help_text="At least 10 characters. Leave empty to keep the current one.")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]
        labels = {"is_active": "Can sign in"}

    def __init__(self, *args, creating: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.creating = creating
        if creating:
            self.fields["password"].required = True
            self.fields["password"].help_text = "At least 10 characters. Give it to the person; they can change it."
        else:
            self.fields["roles"].initial = self.instance.groups.all()

    def clean_password(self):
        pw = self.cleaned_data.get("password")
        if pw:
            password_validation.validate_password(pw, self.instance)
        return pw


class RoleForm(forms.Form):
    name = forms.CharField(max_length=150)
    description = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, group: Group | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Group.objects.filter(name__iexact=name)
        if self.group is not None:
            clash = clash.exclude(pk=self.group.pk)
        if clash.exists():
            raise forms.ValidationError("A role with this name already exists.")
        return name
