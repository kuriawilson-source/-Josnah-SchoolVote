import csv
import io
import logging
import smtplib
from urllib.parse import urlencode
from zipfile import BadZipFile
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .forms import (
    AdminLoginForm,
    AdminPasswordResetForm,
    AdminRecoveryEmailForm,
    AdminSetupForm,
    AdmissionNoForm,
    CandidateEditForm,
    CandidateForm,
    ElectoralOfficialForm,
    ElectoralOfficialEditForm,
    ElectionForm,
    OfficialLoginForm,
    PositionForm,
    StudentCreateForm,
    StudentEditForm,
    StudentImportForm,
)
from .models import (
    Ballot,
    Candidate,
    Election,
    ElectoralOfficial,
    Participation,
    Position,
    Student,
    Vote,
)

logger = logging.getLogger(__name__)


def staff_required(view):
    return login_required(user_passes_test(lambda user: user.is_staff)(view))


def is_electoral_official(user):
    return user.is_authenticated and (
        user.is_superuser or hasattr(user, "electoral_official")
    )


def official_required(view):
    return login_required(
        user_passes_test(is_electoral_official, login_url="official-login")(view),
        login_url="official-login",
    )


class _StudentNoLongerEligible(Exception):
    pass


class AdminLoginView(DjangoLoginView):
    template_name = "core/login.html"
    authentication_form = AdminLoginForm

    def form_valid(self, form):
        if not form.get_user().is_staff:
            form.add_error(None, "This account does not have election administrator access.")
            return self.form_invalid(form)
        if self.request.session.pop("admin_password_reset_completed", False):
            messages.info(
                self.request,
                "Your password was reset successfully. You can update it any time from your account settings.",
            )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["setup_available"] = not get_user_model().objects.filter(is_staff=True).exists()
        return context


class OfficialLoginView(DjangoLoginView):
    template_name = "core/official_login_page.html"
    authentication_form = OfficialLoginForm

    def form_valid(self, form):
        response = super().form_valid(form)
        return redirect("vote-home")


class AdminPasswordResetView(PasswordResetView):
    template_name = "core/password_reset_form.html"
    email_template_name = "core/password_reset_email.txt"
    subject_template_name = "core/password_reset_subject.txt"
    form_class = AdminPasswordResetForm
    success_url = reverse_lazy("admin-password-reset-done")

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except (smtplib.SMTPException, OSError):
            logger.exception("Could not send an administrator password reset email.")
            return redirect(self.success_url)


class AdminPasswordResetDoneView(PasswordResetDoneView):
    template_name = "core/password_reset_done.html"


class AdminPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "core/password_reset_confirm.html"
    success_url = reverse_lazy("admin-password-reset-complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.session["admin_password_reset_completed"] = True
        return response


class AdminPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "core/password_reset_complete.html"


@staff_required
def admin_recovery_email(request):
    if request.method == "POST":
        form = AdminRecoveryEmailForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Recovery email saved. Use an inbox you can access if you forget your password.",
            )
            return redirect("dashboard")
    else:
        form = AdminRecoveryEmailForm(instance=request.user)
    return render(request, "core/admin_recovery_email.html", {"form": form})


@staff_required
def admin_password_change(request):
    form = PasswordChangeForm(
        user=request.user,
        data=request.POST if request.method == "POST" else None,
    )
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Administrator password changed successfully.")
        return redirect("dashboard")
    return render(request, "core/admin_password_change.html", {"form": form})


def admin_setup(request):
    if not settings.DEBUG or request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        raise Http404

    user_model = get_user_model()
    if user_model.objects.filter(is_staff=True).exists():
        return redirect("admin-login")

    form = AdminSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            if user_model.objects.select_for_update().filter(is_staff=True).exists():
                form.add_error(None, "An administrator has already been created.")
            else:
                administrator = form.save()
                administrator.is_staff = True
                administrator.is_superuser = True
                administrator.save(update_fields=["is_staff", "is_superuser"])
                messages.success(request, "Administrator created. Sign in to set up your first election.")
                return redirect("admin-login")
    return render(request, "core/admin_setup.html", {"form": form})


def home(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("dashboard")
    if is_electoral_official(request.user):
        return redirect("vote-home")
    open_election = (
        Election.objects.filter(status=Election.Status.OPEN)
        .exclude(description="")
        .order_by("-created_at")
        .first()
    )
    return render(request, "core/access.html", {"open_election": open_election})


@require_POST
def admin_logout(request):
    logout(request)
    return redirect("admin-login")


@require_POST
def official_logout(request):
    logout(request)
    return redirect("official-login")


@staff_required
def dashboard(request):
    elections = Election.objects.order_by("-created_at")
    dashboard_title = (
        elections.filter(status=Election.Status.OPEN)
        .exclude(description="")
        .values_list("description", flat=True)
        .first()
        or elections.exclude(description="").values_list("description", flat=True).first()
        or "Election dashboard"
    )
    context = {
        "elections": elections,
        "dashboard_title": dashboard_title,
        "student_count": Student.objects.filter(is_active=True).count(),
        "open_count": elections.filter(status=Election.Status.OPEN).count(),
        "vote_count": Participation.objects.count(),
    }
    return render(request, "core/dashboard.html", context)


@staff_required
def officials(request):
    if request.method == "POST":
        form = ElectoralOfficialForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                ElectoralOfficial.objects.create(user=user)
            messages.success(request, f"Electoral official account '{user.username}' created.")
            return redirect("officials")
    else:
        form = ElectoralOfficialForm()
    accounts = ElectoralOfficial.objects.select_related("user").order_by("user__username")
    return render(request, "core/officials.html", {"form": form, "officials": accounts})


@staff_required
def official_edit(request, official_id):
    official = get_object_or_404(ElectoralOfficial.objects.select_related("user"), pk=official_id)
    if request.method == "POST":
        form = ElectoralOfficialEditForm(request.POST, instance=official.user)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"Electoral official account '{user.username}' updated.")
            return redirect("officials")
    else:
        form = ElectoralOfficialEditForm(instance=official.user)
    return render(request, "core/official_edit.html", {"form": form, "official": official})


@staff_required
@require_POST
def official_reset_password(request, official_id):
    official = get_object_or_404(ElectoralOfficial.objects.select_related("user"), pk=official_id)
    official.user.set_password("12345678")
    official.user.save(update_fields=["password"])
    messages.success(
        request,
        f"Password for '{official.user.username}' has been reset to 12345678.",
    )
    return redirect("officials")


@staff_required
@require_POST
def official_delete(request, official_id):
    official = get_object_or_404(ElectoralOfficial.objects.select_related("user"), pk=official_id)
    username = official.user.username
    official.user.delete()
    messages.success(request, f"Electoral official account '{username}' deleted.")
    return redirect("officials")


@official_required
def official_results(request):
    elections = Election.objects.order_by("-created_at")
    election_id = request.GET.get("election")
    election = elections.filter(pk=election_id).first() if election_id else elections.filter(
        status=Election.Status.OPEN
    ).first()
    if election is None:
        election = elections.first()
    if election is None:
        return render(
            request,
            "core/official_results.html",
            {"elections": elections, "election": None},
        )

    voted_student_ids = set(
        Participation.objects.filter(election=election).values_list("student_id", flat=True)
    )
    registered_students = list(
        Student.objects.filter(Q(is_active=True) | Q(pk__in=voted_student_ids))
        .only("id", "grade", "stream")
        .distinct()
    )
    total_registered = len(registered_students)
    total_voted = len(voted_student_ids)
    turnout_percent = round(total_voted * 100 / total_registered) if total_registered else 0
    votes_by_candidate = dict(
        Vote.objects.filter(ballot__election=election)
        .values("candidate_id")
        .annotate(vote_count=Count("pk"))
        .values_list("candidate_id", "vote_count")
    )
    position_rows = []
    for position in election.positions.prefetch_related("candidates"):
        candidates = [
            {
                "name": candidate.name,
                "votes": votes_by_candidate.get(candidate.pk, 0),
            }
            for candidate in position.candidates.all()
        ]
        position_total = sum(candidate["votes"] for candidate in candidates)
        for candidate in candidates:
            candidate["percent"] = (
                round(candidate["votes"] * 100 / position_total) if position_total else 0
            )
        position_rows.append(
            {"name": position.name, "total_votes": position_total, "candidates": candidates}
        )

    return render(
        request,
        "core/official_results.html",
        {
            "elections": elections,
            "election": election,
            "total_registered": total_registered,
            "total_voted": total_voted,
            "remaining": total_registered - total_voted,
            "turnout_percent": turnout_percent,
            "position_rows": position_rows,
        },
    )


@staff_required
def students(request):
    query = request.GET.get("q", "").strip()
    elections = Election.objects.order_by("-created_at")
    selected_election = None
    election_id = request.GET.get("election")
    if election_id:
        selected_election = Election.objects.filter(pk=election_id).first()
    if selected_election is None:
        selected_election = elections.first()
    records = Student.objects.all().order_by("admission_no")
    if query:
        records = records.filter(admission_no__icontains=query) | records.filter(name__icontains=query)
    if selected_election:
        participation = Participation.objects.filter(
            election=selected_election, student_id=OuterRef("pk")
        )
        records = records.annotate(has_voted=Exists(participation))
    records = records.annotate(
        has_voted_open=Exists(
            Participation.objects.filter(
                student_id=OuterRef("pk"), election__status=Election.Status.OPEN
            )
        )
    )
    return render(
        request,
        "core/students.html",
        {
            "students": records[:100],
            "total": Student.objects.filter(is_active=True).count(),
            "query": query,
            "elections": elections,
            "selected_election": selected_election,
        },
    )


@staff_required
def student_create(request):
    if request.method == "POST":
        form = StudentCreateForm(request.POST)
        if form.is_valid():
            student = form.save()
            messages.success(request, f"Learner '{student.name}' added.")
            return redirect("students")
    else:
        form = StudentCreateForm()
    return render(request, "core/student_form.html", {"form": form, "title": "Add learner"})


@staff_required
def student_edit(request, student_id):
    student = get_object_or_404(Student, pk=student_id)
    if _student_has_open_election_vote(student):
        messages.error(request, "Learner records cannot be edited after voting in an open election.")
        return redirect("students")
    if request.method == "POST":
        with transaction.atomic():
            student = Student.objects.select_for_update().get(pk=student.pk)
            if _student_has_open_election_vote(student):
                messages.error(
                    request,
                    "Learner records cannot be edited after voting in an open election.",
                )
                return redirect("students")
            form = StudentEditForm(request.POST, instance=student)
            if form.is_valid():
                student = form.save()
                messages.success(request, f"Learner '{student.name}' updated.")
                return redirect("students")
    else:
        form = StudentEditForm(instance=student)
    return render(
        request,
        "core/student_form.html",
        {"form": form, "title": "Edit learner", "student": student},
    )


def _student_has_open_election_vote(student):
    return Participation.objects.filter(
        student=student, election__status=Election.Status.OPEN
    ).exists()


@staff_required
@require_POST
def student_delete(request, student_id):
    with transaction.atomic():
        student = get_object_or_404(Student.objects.select_for_update(), pk=student_id)
        if _student_has_open_election_vote(student):
            messages.error(
                request,
                "This learner cannot be deleted because they have voted in an open election.",
            )
            return redirect("students")

        learner_name = student.name
        if Participation.objects.filter(student=student).exists():
            student.is_active = False
            student.save(update_fields=["is_active"])
            messages.success(
                request,
                f"'{learner_name}' was removed from the active learner roll. "
                "Closed-election participation records were retained.",
            )
        else:
            student.delete()
            messages.success(request, f"Learner '{learner_name}' deleted.")
    return redirect("students")


@staff_required
@require_POST
def student_eligibility(request, student_id):
    student = get_object_or_404(Student, pk=student_id)
    action = request.POST.get("action")
    if action == "enable":
        student.is_active = True
        student.save(update_fields=["is_active"])
        messages.success(request, f"'{student.name}' is eligible to vote again.")
    elif action == "disable":
        try:
            election_id = int(request.POST.get("election", ""))
        except ValueError:
            election_id = None
        election = Election.objects.filter(pk=election_id).first() if election_id else None
        if election is None:
            messages.error(request, "Select an election before disabling a learner.")
        else:
            with transaction.atomic():
                student = Student.objects.select_for_update().get(pk=student.pk)
                student.is_active = False
                student.save(update_fields=["is_active"])
                if Participation.objects.filter(election=election, student=student).exists():
                    messages.success(
                        request,
                        f"'{student.name}' is disabled. Their anonymous ballot and recorded vote totals are unchanged.",
                    )
                else:
                    messages.success(request, f"'{student.name}' is no longer eligible to vote.")
    else:
        messages.error(request, "Invalid learner eligibility action.")
    query = request.POST.get("q", "").strip()
    election_id = request.POST.get("election", "")
    params = {}
    if query:
        params["q"] = query
    if election_id:
        params["election"] = election_id
    return redirect(f"/students/?{urlencode(params)}" if params else "students")


@staff_required
def import_students(request):
    if request.method == "POST":
        form = StudentImportForm(request.POST, request.FILES)
        if form.is_valid():
            upload = form.cleaned_data["file"]
            try:
                rows = _read_student_rows(upload)
                imported = _save_student_rows(rows)
            except ValueError as exc:
                form.add_error("file", str(exc))
            else:
                messages.success(request, f"Imported {imported} eligible learner(s).")
                return redirect("students")
    else:
        form = StudentImportForm()
    return render(request, "core/student_import.html", {"form": form})


def _read_student_rows(upload):
    if upload.size > 5 * 1024 * 1024:
        raise ValueError("The file must be 5 MB or smaller.")
    filename = upload.name.lower()
    if not filename.endswith((".csv", ".xlsx")):
        raise ValueError("Upload a .csv or .xlsx file.")
    try:
        if filename.endswith(".csv"):
            text = upload.read().decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
        elif filename.endswith(".xlsx"):
            workbook = load_workbook(upload, read_only=True, data_only=True)
            try:
                worksheet = workbook.active
                values = worksheet.iter_rows()
                header_cells = next(values, None)
                if not header_cells:
                    raise ValueError("The spreadsheet is empty.")
                headers = [cell.value for cell in header_cells]
                rows = [
                    {
                        header: _spreadsheet_value(cell)
                        for header, cell in zip(headers, row)
                    }
                    for row in values
                ]
            finally:
                workbook.close()
    except (UnicodeDecodeError, csv.Error, OSError, BadZipFile, InvalidFileException, ValueError) as exc:
        raise ValueError("The file could not be read. Check that it is a valid CSV or XLSX file.") from exc
    if not rows:
        raise ValueError("The file contains no learner records.")
    if len(rows) > 2000:
        raise ValueError("Import no more than 2,000 learners at a time.")
    return rows


def _spreadsheet_value(cell):
    value = cell.value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number_format = cell.number_format
        if isinstance(value, int) and number_format and set(number_format) == {"0"}:
            return f"{value:0{len(number_format)}d}"
    return value


def _normalize_student_header(header):
    return str(header).strip().lower().replace("_", " ").replace(".", "")


def _save_student_rows(rows):
    aliases = {
        "adm": "admission_no",
        "adm no": "admission_no",
        "adm number": "admission_no",
        "adm #": "admission_no",
        "admission no": "admission_no",
        "admission number": "admission_no",
        "admission #": "admission_no",
        "student id": "admission_no",
        "name": "name",
        "grade": "grade",
        "stream": "stream",
    }
    first_keys = {
        aliases.get(_normalize_student_header(key))
        for key in rows[0]
        if key is not None
    }
    if not {"admission_no", "name", "grade", "stream"}.issubset(first_keys):
        raise ValueError("Required columns: Adm, Name, Grade, Stream.")

    clean_rows = []
    seen = set()
    for row_number, row in enumerate(rows, start=2):
        normalized = {
            aliases[_normalize_student_header(key)]: value
            for key, value in row.items()
            if key is not None and _normalize_student_header(key) in aliases
        }
        admission_no = str(normalized.get("admission_no") or "").strip()
        name = str(normalized.get("name") or "").strip()
        grade = str(normalized.get("grade") or "").strip()
        stream = str(normalized.get("stream") or "").strip()
        if not admission_no or not name or not grade or not stream:
            raise ValueError(f"Row {row_number}: Adm, Name, Grade, and Stream are required.")
        if len(admission_no) > 40 or len(name) > 160 or len(grade) > 40 or len(stream) > 80:
            raise ValueError(f"Row {row_number}: one or more values exceed the allowed length.")
        if admission_no in seen:
            raise ValueError(f"Row {row_number}: duplicate Admission No. {admission_no}.")
        seen.add(admission_no)
        clean_rows.append(
            Student(
                admission_no=admission_no,
                name=name,
                grade=grade,
                stream=stream,
            )
        )

    existing = set(
        Student.objects.filter(admission_no__in=seen).values_list("admission_no", flat=True)
    )
    if existing:
        listed = ", ".join(sorted(existing)[:5])
        suffix = "…" if len(existing) > 5 else ""
        raise ValueError(f"These Admission No. values already exist: {listed}{suffix}. No learners were imported.")
    try:
        with transaction.atomic():
            Student.objects.bulk_create(clean_rows)
    except IntegrityError as exc:
        raise ValueError("An Admission No. was added by another import. No learners were imported; retry the file.") from exc
    return len(clean_rows)


@staff_required
def election_create(request):
    if request.method == "POST":
        form = ElectionForm(request.POST)
        if form.is_valid():
            election = form.save(commit=False)
            election.created_by = request.user
            election.save()
            messages.success(request, "Election created. Add positions and candidates before opening it.")
            return redirect("election-detail", election_id=election.pk)
    else:
        form = ElectionForm()
    return render(request, "core/election_form.html", {"form": form})


@staff_required
def election_detail(request, election_id):
    election = get_object_or_404(Election, pk=election_id)
    positions = list(election.positions.prefetch_related("candidates"))
    for position in positions:
        position.active_candidate_count = sum(
            candidate.is_active for candidate in position.candidates.all()
        )
    has_voting_data = (
        Participation.objects.filter(election=election).exists()
        or Ballot.objects.filter(election=election).exists()
    )
    return render(
        request,
        "core/election_detail.html",
        {
            "election": election,
            "positions": positions,
            "turnout": Participation.objects.filter(election=election).count(),
            "can_delete": not has_voting_data,
            "eligible_count": Student.objects.filter(is_active=True).count(),
            "can_show_results": election.status == Election.Status.CLOSED,
            "result_rows": _result_rows(election) if election.status == Election.Status.CLOSED else [],
            "position_form": PositionForm(),
            "candidate_forms": {position.pk: CandidateForm() for position in positions},
        },
    )


@staff_required
@require_POST
def election_delete(request, election_id):
    with transaction.atomic():
        election = get_object_or_404(Election.objects.select_for_update(), pk=election_id)
        has_voting_data = (
            Participation.objects.filter(election=election).exists()
            or Ballot.objects.filter(election=election).exists()
        )
        if has_voting_data:
            messages.error(
                request,
                "This election cannot be deleted because it has voting records. Close the election instead.",
            )
            return redirect("election-detail", election_id=election.pk)
        election_name = election.name
        election.delete()
    messages.success(request, f"Empty election '{election_name}' deleted.")
    return redirect("dashboard")


@staff_required
@require_POST
def position_create(request, election_id):
    election = get_object_or_404(Election, pk=election_id)
    if election.status != Election.Status.DRAFT:
        messages.error(request, "Positions can only be changed while the election is a draft.")
        return redirect("election-detail", election_id=election.pk)
    form = PositionForm(request.POST)
    if form.is_valid():
        if Position.objects.filter(election=election, name__iexact=form.cleaned_data["name"]).exists():
            messages.error(request, "That position already exists in this election.")
            return redirect("election-detail", election_id=election.pk)
        position = form.save(commit=False)
        position.election = election
        position.save()
        if position.voter_stream:
            messages.success(request, f"Position added for stream '{position.voter_stream}'.")
        else:
            messages.success(request, "Position added for all streams.")
    else:
        messages.error(request, "Enter a valid position name.")
    return redirect("election-detail", election_id=election.pk)


@staff_required
@require_POST
def candidate_create(request, position_id):
    position = get_object_or_404(Position.objects.select_related("election"), pk=position_id)
    if position.election.status not in (Election.Status.DRAFT, Election.Status.OPEN):
        messages.error(request, "Candidates cannot be changed after an election is closed.")
        return redirect("election-detail", election_id=position.election_id)
    form = CandidateForm(request.POST)
    if form.is_valid():
        if Candidate.objects.filter(position=position, name__iexact=form.cleaned_data["name"]).exists():
            messages.error(request, "That candidate already exists for this position.")
            return redirect("election-detail", election_id=position.election_id)
        candidate = form.save(commit=False)
        candidate.position = position
        candidate.save()
        messages.success(
            request,
            "Candidate added. Existing ballots and recorded votes are unchanged.",
        )
    else:
        messages.error(request, "Enter a valid candidate name.")
    return redirect("election-detail", election_id=position.election_id)


def _candidate_management_allowed(candidate):
    return candidate.position.is_active and candidate.position.election.status in (
        Election.Status.DRAFT,
        Election.Status.OPEN,
    )


@staff_required
def candidate_edit(request, candidate_id):
    candidate = get_object_or_404(
        Candidate.objects.select_related("position__election"), pk=candidate_id
    )
    election = candidate.position.election
    if not _candidate_management_allowed(candidate):
        messages.error(
            request,
            "Candidates cannot be changed after an election is closed or their position is retired.",
        )
        return redirect("election-detail", election_id=election.pk)

    if request.method == "POST":
        form = CandidateEditForm(request.POST, request.FILES, instance=candidate)
        if form.is_valid():
            duplicate = Candidate.objects.filter(
                position=candidate.position, name__iexact=form.cleaned_data["name"]
            ).exclude(pk=candidate.pk).exists()
            if duplicate:
                form.add_error("name", "That candidate already exists for this position.")
            else:
                old_photo = candidate.passport_photo
                old_photo_name = old_photo.name
                old_photo_storage = old_photo.storage
                with transaction.atomic():
                    form.save()
                    if old_photo_name and old_photo_name != candidate.passport_photo.name:
                        transaction.on_commit(
                            lambda: old_photo_storage.delete(old_photo_name)
                        )
                messages.success(request, f"Candidate '{candidate.name}' updated.")
                return redirect("election-detail", election_id=election.pk)
    else:
        form = CandidateEditForm(instance=candidate)
    return render(
        request,
        "core/candidate_form.html",
        {"form": form, "candidate": candidate, "title": "Edit candidate"},
    )


@staff_required
@require_POST
def candidate_delete(request, candidate_id):
    with transaction.atomic():
        candidate = get_object_or_404(
            Candidate.objects.select_for_update().select_related("position__election"),
            pk=candidate_id,
        )
        election = candidate.position.election
        if not _candidate_management_allowed(candidate):
            messages.error(
                request,
                "Candidates cannot be changed after an election is closed or their position is retired.",
            )
        elif Vote.objects.filter(candidate=candidate).exists():
            messages.error(
                request,
                "This candidate cannot be deleted because votes have already been recorded for them.",
            )
        elif (
            election.status == Election.Status.OPEN
            and candidate.is_active
            and candidate.position.candidates.filter(is_active=True).count() <= 2
        ):
            messages.error(
                request,
                "An open election must keep at least two active candidates for each position.",
            )
        else:
            candidate_name = candidate.name
            candidate.delete()
            messages.success(request, f"Candidate '{candidate_name}' deleted.")
    return redirect("election-detail", election_id=election.pk)


@staff_required
@require_POST
def candidate_status(request, candidate_id):
    with transaction.atomic():
        candidate = get_object_or_404(
            Candidate.objects.select_for_update().select_related("position__election"),
            pk=candidate_id,
        )
        election = candidate.position.election
        if not _candidate_management_allowed(candidate):
            messages.error(
                request,
                "Candidates cannot be changed after an election is closed or their position is retired.",
            )
        else:
            action = request.POST.get("action")
            if action == "disable":
                if (
                    election.status == Election.Status.OPEN
                    and candidate.is_active
                    and candidate.position.candidates.filter(is_active=True).count() <= 2
                ):
                    messages.error(
                        request,
                        "An open election must keep at least two active candidates for each position.",
                    )
                else:
                    candidate.is_active = False
                    candidate.save(update_fields=["is_active"])
                    messages.success(
                        request,
                        f"'{candidate.name}' is disabled. Recorded votes are unchanged.",
                    )
            elif action == "enable":
                candidate.is_active = True
                candidate.save(update_fields=["is_active"])
                messages.success(request, f"'{candidate.name}' is enabled.")
            else:
                messages.error(request, "Invalid candidate status action.")
    return redirect("election-detail", election_id=election.pk)


@staff_required
@require_POST
def election_status(request, election_id):
    election = get_object_or_404(Election, pk=election_id)
    action = request.POST.get("action")
    if action == "open" and election.status == Election.Status.DRAFT:
        positions = election.positions.prefetch_related("candidates")
        if not positions.exists():
            messages.error(request, "Add at least one position before opening the election.")
        elif any(position.candidates.filter(is_active=True).count() < 2 for position in positions):
            messages.error(request, "Every position needs at least two candidates before opening.")
        elif not Student.objects.filter(is_active=True).exists():
            messages.error(request, "Import eligible learners before opening the election.")
        else:
            election.status = Election.Status.OPEN
            election.save(update_fields=["status"])
            messages.success(request, "Election is open for voting.")
    elif action == "close" and election.status == Election.Status.OPEN:
        election.status = Election.Status.CLOSED
        election.save(update_fields=["status"])
        messages.success(request, "Election closed. Results and exports are now available.")
    else:
        messages.error(request, "That election status change is not allowed.")
    return redirect("election-detail", election_id=election.pk)


def _result_rows(election):
    positions = election.positions.prefetch_related("candidates")
    turnout = Participation.objects.filter(election=election).count()
    rows = []
    for position in positions:
        candidates = list(position.candidates.all())
        counts = dict(
            Vote.objects.filter(position=position)
            .values_list("candidate_id")
            .annotate(total=Count("id"))
        )
        for candidate in candidates:
            rows.append((position.name, candidate.name, counts.get(candidate.pk, 0), turnout))
    return rows


def _ranked_candidate_rows(election):
    votes_by_candidate = dict(
        Vote.objects.filter(ballot__election=election)
        .values("candidate_id")
        .annotate(total=Count("id"))
        .values_list("candidate_id", "total")
    )
    rows = [
        (position.name, candidate.name, votes_by_candidate.get(candidate.pk, 0))
        for position in election.positions.prefetch_related("candidates")
        for candidate in position.candidates.all()
    ]
    return sorted(
        rows,
        key=lambda row: (-row[2], row[0].casefold(), row[1].casefold()),
    )


def _closed_election_or_404(election_id):
    election = get_object_or_404(Election, pk=election_id, status=Election.Status.CLOSED)
    return election


@staff_required
def results_xlsx(request, election_id):
    election = _closed_election_or_404(election_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Election results"
    title_cell = sheet.cell(row=1, column=1, value=election.name)
    title_cell.data_type = "s"
    sheet.append(["Position", "Candidate", "Votes", "Turnout"])
    for cell in sheet[2]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="253F35")
    for row in _result_rows(election):
        sheet.append(row)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    sheet.freeze_panes = "A3"
    sheet.auto_filter.ref = f"A2:D{sheet.max_row}"
    for column, width in {"A": 24, "B": 32, "C": 12, "D": 12}.items():
        sheet.column_dimensions[column].width = width
    output = io.BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="election-{election.pk}-results.xlsx"'
    return response


@staff_required
def results_pdf(request, election_id):
    election = _closed_election_or_404(election_id)
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ElectionTitle", parent=styles["Title"], alignment=TA_CENTER)
    story = [
        Paragraph("Election results", title_style),
        Paragraph(escape(election.name), styles["Heading2"]),
        Paragraph(
            f"Turnout: {Participation.objects.filter(election=election).count()} "
            f"of {Student.objects.filter(is_active=True).count()} eligible learners",
            styles["Normal"],
        ),
        Spacer(1, 8 * mm),
    ]
    data = [["Position", "Candidate", "Votes", "Turnout"]]
    data.extend([list(row) for row in _result_rows(election)])
    table = Table(data, repeatRows=1, colWidths=[43 * mm, 70 * mm, 24 * mm, 24 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#253f35")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8dfd9")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7f4")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(table)
    document.build(story)
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="election-{election.pk}-results.pdf"'
    return response


@staff_required
def ranked_candidate_report(request, election_id):
    election = _closed_election_or_404(election_id)
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("RankedResultsTitle", parent=styles["Title"], alignment=TA_CENTER)
    story = [
        Paragraph("Candidate vote ranking", title_style),
        Paragraph(escape(election.name), styles["Heading2"]),
        Paragraph(
            f"Voting closed · {Participation.objects.filter(election=election).count()} "
            "learner ballots recorded",
            styles["Normal"],
        ),
        Spacer(1, 8 * mm),
    ]
    data = [["Rank", "Position", "Candidate", "Votes"]]
    data.extend(
        [[rank, position, candidate, votes]
         for rank, (position, candidate, votes) in enumerate(
             _ranked_candidate_rows(election), start=1
         )]
    )
    table = Table(data, repeatRows=1, colWidths=[18 * mm, 52 * mm, 85 * mm, 20 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#253f35")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8dfd9")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7f4")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 1), (0, -1), "CENTER"),
                ("ALIGN", (-1, 1), (-1, -1), "CENTER"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(table)
    document.build(story)
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="election-{election.pk}-candidate-vote-ranking.pdf"'
    )
    return response


@official_required
def vote_home(request):
    elections = Election.objects.filter(status=Election.Status.OPEN).order_by("name")
    if elections.count() == 1:
        return redirect("vote", election_id=elections.first().pk)
    return render(request, "core/vote_home.html", {"elections": elections})


def _open_election(election_id):
    return get_object_or_404(Election, pk=election_id, status=Election.Status.OPEN)


def _positions_for_student(election, student):
    return list(
        election.positions.filter(is_active=True)
        .filter(Q(voter_stream="") | Q(voter_stream=student.stream))
        .filter(Q(voter_grade="") | Q(voter_grade=student.grade))
        .prefetch_related("candidates")
    )


def _positions_missing_candidates(positions):
    return [
        position
        for position in positions
        if position.candidates.filter(is_active=True).count() < 2
    ]


def _render_ballot_or_not_ready(request, election, student, positions):
    missing_positions = _positions_missing_candidates(positions)
    if missing_positions:
        names = ", ".join(position.name for position in missing_positions)
        messages.error(
            request,
            f"Voting cannot continue for stream '{student.stream}' until at least two active "
            f"candidates are added to: {names}.",
        )
        return redirect("vote", election_id=election.pk)
    return render(
        request,
        "core/ballot.html",
        {"election": election, "student": student, "positions": positions},
    )


@official_required
def vote(request, election_id):
    election = _open_election(election_id)
    now = timezone.now()
    if election.starts_at and now < election.starts_at:
        return render(
            request,
            "core/voting_unavailable.html",
            {"election": election, "opens_at": election.starts_at, "not_started": True},
        )
    if election.ends_at and now >= election.ends_at:
        return render(
            request,
            "core/voting_unavailable.html",
            {"election": election, "closes_at": election.ends_at, "not_started": False},
        )

    voter_key = f"official_voter_{election.pk}"
    admission_form = AdmissionNoForm()

    if request.method == "POST" and "find_student" in request.POST:
        request.session.pop(voter_key, None)
        admission_form = AdmissionNoForm(request.POST)
        if admission_form.is_valid():
            student = Student.objects.filter(
                admission_no=admission_form.cleaned_data["admission_no"].strip(),
                is_active=True,
            ).first()
            if student is None:
                messages.error(request, "Admission No. not found. Check the number with your school.")
            elif Participation.objects.filter(election=election, student=student).exists():
                return render(
                    request,
                    "core/already_voted.html",
                    {"election": election, "student": student},
                )
            else:
                positions = _positions_for_student(election, student)
                ballot_response = _render_ballot_or_not_ready(
                    request, election, student, positions
                )
                if ballot_response.status_code == 302:
                    return ballot_response
                request.session[voter_key] = student.admission_no
                return ballot_response

    if request.method == "POST" and "cast_ballot" in request.POST:
        admission_no = request.session.get(voter_key)
        if not admission_no:
            messages.error(request, "Enter the learner's Admission No. before opening a ballot.")
            return redirect("vote", election_id=election.pk)
        student = Student.objects.filter(admission_no=admission_no, is_active=True).first()
        if student is None:
            request.session.pop(voter_key, None)
            messages.error(request, "This learner is no longer eligible to vote.")
            return redirect("vote", election_id=election.pk)
        positions = _positions_for_student(election, student)
        missing_positions = _positions_missing_candidates(positions)
        if missing_positions:
            request.session.pop(voter_key, None)
            return _render_ballot_or_not_ready(request, election, student, positions)
        if Participation.objects.filter(election=election, student=student).exists():
            request.session.pop(voter_key, None)
            return render(
                request,
                "core/already_voted.html",
                {"election": election, "student": student},
            )
        selections = []
        for position in positions:
            candidate_id = request.POST.get(f"position_{position.pk}", "")
            if not candidate_id:
                messages.error(request, f"Select a candidate for {position.name} before submitting.")
                return render(
                    request,
                    "core/ballot.html",
                    {"election": election, "student": student, "positions": positions},
                )
            candidate = position.candidates.filter(pk=candidate_id, is_active=True).first()
            if candidate is None:
                messages.error(request, "Invalid candidate selection. Please try again.")
                return render(
                    request,
                    "core/ballot.html",
                    {"election": election, "student": student, "positions": positions},
                )
            selections.append((position, candidate))
        try:
            with transaction.atomic():
                student = Student.objects.select_for_update().get(pk=student.pk)
                if not student.is_active:
                    raise _StudentNoLongerEligible
                Participation.objects.create(election=election, student=student)
                ballot = Ballot.objects.create(election=election)
                Vote.objects.bulk_create(
                    [Vote(ballot=ballot, position=position, candidate=candidate) for position, candidate in selections]
                )
        except _StudentNoLongerEligible:
            request.session.pop(voter_key, None)
            messages.error(request, "This learner is no longer eligible to vote.")
            return redirect("vote", election_id=election.pk)
        except IntegrityError:
            request.session.pop(voter_key, None)
            return render(
                request,
                "core/already_voted.html",
                {"election": election, "student": student},
            )
        request.session.pop(voter_key, None)
        return render(request, "core/vote_done.html", {"election": election})

    admission_no = request.session.get(voter_key)
    student = Student.objects.filter(admission_no=admission_no, is_active=True).first() if admission_no else None
    if student:
        positions = _positions_for_student(election, student)
        return _render_ballot_or_not_ready(request, election, student, positions)
    return render(
        request,
        "core/official_login.html",
        {"election": election, "form": admission_form},
    )
