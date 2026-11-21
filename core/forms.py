from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    UserCreationForm,
)

from .models import Candidate, Election, Position, Student


class AdminLoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"autocomplete": "username"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class AdminPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        user_model = get_user_model()
        return (
            user
            for user in user_model._default_manager.filter(
                email__iexact=email,
                is_active=True,
                is_staff=True,
            )
            if user.has_usable_password()
        )


class AdminRecoveryEmailForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ["email"]
        widgets = {
            "email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "admin@example.com"}
            )
        }


class OfficialLoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"autocomplete": "username"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def confirm_login_allowed(self, user):
        if not user.is_superuser and not hasattr(user, "electoral_official"):
            raise forms.ValidationError(
                "This account does not have electoral-official access.",
                code="official_access_required",
            )
        super().confirm_login_allowed(user)


class AdminSetupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username",)


class ElectoralOfficialForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username",)


class ElectoralOfficialEditForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ("username",)


class StudentCreateForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ("admission_no", "name", "grade", "stream")


class StudentEditForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ("name", "grade", "stream")


class ElectionForm(forms.ModelForm):
    class Meta:
        model = Election
        fields = ["name", "description", "starts_at", "ends_at"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("starts_at")
        end = cleaned.get("ends_at")
        if start and end and end <= start:
            self.add_error("ends_at", "The closing time must be after the opening time.")
        return cleaned


class PositionForm(forms.ModelForm):
    voter_grade = forms.ChoiceField(
        required=False,
        label="Eligible grade",
        choices=(),
    )
    voter_stream = forms.ChoiceField(
        required=False,
        label="Eligible stream",
        choices=(),
        help_text="Choose both grade and stream to restrict this position. Leave both blank for all learners.",
    )

    class Meta:
        model = Position
        fields = ["name", "order", "voter_grade", "voter_stream"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        grades = (
            Student.objects.filter(is_active=True)
            .exclude(grade="")
            .values_list("grade", flat=True)
            .distinct()
            .order_by("grade")
        )
        streams = (
            Student.objects.filter(is_active=True)
            .exclude(stream="")
            .values_list("stream", flat=True)
            .distinct()
            .order_by("stream")
        )
        self.fields["voter_grade"].choices = [("", "All grades"), *[(g, g) for g in grades]]
        self.fields["voter_stream"].choices = [("", "All streams"), *[(s, s) for s in streams]]

    def clean(self):
        cleaned = super().clean()
        if bool(cleaned.get("voter_grade")) != bool(cleaned.get("voter_stream")):
            self.add_error(
                None,
                "Choose both an eligible grade and stream, or leave both unrestricted.",
            )
        return cleaned


class CandidateForm(forms.ModelForm):
    class Meta:
        model = Candidate
        fields = ["name", "slogan"]


class CandidateEditForm(forms.ModelForm):
    class Meta:
        model = Candidate
        fields = ["name", "slogan", "passport_photo"]
        widgets = {
            "passport_photo": forms.ClearableFileInput(
                attrs={"accept": "image/*", "capture": "environment"}
            )
        }

    def clean_passport_photo(self):
        photo = self.cleaned_data.get("passport_photo")
        if photo and photo.size > 5 * 1024 * 1024:
            raise forms.ValidationError("The passport photo must be 5 MB or smaller.")
        return photo


class StudentImportForm(forms.Form):
    file = forms.FileField()


class AdmissionNoForm(forms.Form):
    admission_no = forms.CharField(max_length=40, label="Admission No.")
