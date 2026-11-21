import io
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from openpyxl import Workbook, load_workbook

from .models import Ballot, Candidate, Election, ElectoralOfficial, Participation, Position, Student, Vote


class SchoolVoteFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="election-admin", password="long-test-password", is_staff=True
        )
        self.student = Student.objects.create(
            admission_no="S-001",
            name="Ari Learner",
            grade="Year 8",
            stream="A",
        )
        self.election = Election.objects.create(name="Student Council", status=Election.Status.OPEN)
        self.position = Position.objects.create(election=self.election, name="President")
        self.candidate = Candidate.objects.create(position=self.position, name="Sam Candidate")
        self.second_candidate = Candidate.objects.create(position=self.position, name="Lee Candidate")

    def test_vote_authentication_records_anonymous_ballot_once(self):
        self.client.force_login(self.create_official())
        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": "S-001", "find_student": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SAM CANDIDATE")
        self.assertContains(response, "Grade: Year 8")
        self.assertContains(response, "Stream: A")
        self.assertContains(response, "Adm: S-001")
        self.assertEqual(self.client.session[f"official_voter_{self.election.pk}"], "S-001")

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"cast_ballot": "1"},
        )
        self.assertContains(response, "Select a candidate for President")
        self.assertEqual(Participation.objects.count(), 0)

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {f"position_{self.position.pk}": str(self.candidate.pk), "cast_ballot": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thank you for voting")
        self.assertEqual(Participation.objects.filter(election=self.election).count(), 1)
        self.assertEqual(Ballot.objects.filter(election=self.election).count(), 1)
        self.assertEqual(Vote.objects.get().candidate, self.candidate)
        self.assertNotIn(f"official_voter_{self.election.pk}", self.client.session)

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": "S-001", "find_student": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Already Voted!")
        self.assertEqual(Ballot.objects.count(), 1)
        self.client.force_login(self.admin)
        response = self.client.get(f"/students/?election={self.election.pk}")
        self.assertContains(response, "Voted")

    def test_unknown_admission_and_unverified_ballot_are_rejected(self):
        self.client.force_login(self.create_official())
        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": "UNKNOWN", "find_student": "1"},
        )
        self.assertContains(response, "Admission No. not found")
        self.assertEqual(Participation.objects.count(), 0)

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {f"position_{self.position.pk}": str(self.candidate.pk), "cast_ballot": "1"},
        )
        self.assertRedirects(response, f"/vote/{self.election.pk}/")
        self.assertEqual(Ballot.objects.count(), 0)

    def test_official_voting_requires_staff_login(self):
        response = self.client.get("/vote/")
        self.assertRedirects(response, "/official/login/?next=/vote/")
        response = self.client.get(f"/vote/{self.election.pk}/")
        self.assertRedirects(response, f"/official/login/?next=/vote/{self.election.pk}/")

        self.client.force_login(self.create_official())
        response = self.client.get("/vote/")
        self.assertRedirects(response, f"/vote/{self.election.pk}/")
        self.assertContains(self.client.get(response.url), "Enter Admission Number")
        self.assertEqual(self.client.get(f"/vote/{self.election.pk}/").status_code, 200)

    def create_official(self, username="official-one"):
        user = get_user_model().objects.create_user(username=username, password="Valid-Official-Pass-73!")
        ElectoralOfficial.objects.create(user=user)
        return user

    def test_admin_and_official_login_and_navigation_are_separate(self):
        official = self.create_official()

        response = self.client.post(
            "/official/login/",
            {"username": official.username, "password": "Valid-Official-Pass-73!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/vote/")
        self.assertEqual(self.client.get("/vote/").status_code, 302)
        self.assertEqual(self.client.get("/official/results/").status_code, 200)
        self.assertRedirects(self.client.get("/dashboard/"), "/login/?next=/dashboard/")
        self.assertRedirects(self.client.get("/students/"), "/login/?next=/students/")
        response = self.client.post(
            "/login/",
            {"username": official.username, "password": "Valid-Official-Pass-73!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not have election administrator access")

        self.client.logout()
        response = self.client.post(
            "/login/",
            {"username": self.admin.username, "password": "long-test-password"},
        )
        self.assertRedirects(response, "/dashboard/")
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_admin_can_change_password_without_losing_session(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/dashboard/"), "Change password")
        response = self.client.get("/admin/password/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Change password")

        response = self.client.post(
            "/admin/password/change/",
            {
                "old_password": "long-test-password",
                "new_password1": "Replacement-Admin-Pass-53!",
                "new_password2": "Replacement-Admin-Pass-53!",
            },
        )
        self.assertRedirects(response, "/dashboard/")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("Replacement-Admin-Pass-53!"))
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_password_recovery_sends_username_and_one_time_link(self):
        self.admin.email = "admin@example.com"
        self.admin.save(update_fields=["email"])

        response = self.client.post(
            "/password-reset/",
            {"email": "admin@example.com"},
        )
        self.assertRedirects(response, "/password-reset/sent/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.admin.username, mail.outbox[0].body)
        self.assertNotIn("long-test-password", mail.outbox[0].body)

        import re

        reset_path = re.search(
            r"http://testserver(/password-reset/[^/\s]+/[^/\s]+/)",
            mail.outbox[0].body,
        ).group(1)
        response = self.client.get(reset_path, follow=True)
        self.assertEqual(response.status_code, 200)
        confirm_path = response.request["PATH_INFO"]
        response = self.client.post(
            confirm_path,
            {
                "new_password1": "New-Strong-Admin-Pass-97!",
                "new_password2": "New-Strong-Admin-Pass-97!",
            },
        )
        self.assertRedirects(response, "/password-reset/complete/")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("New-Strong-Admin-Pass-97!"))

        response = self.client.post(
            "/login/",
            {
                "username": self.admin.username,
                "password": "New-Strong-Admin-Pass-97!",
            },
        )
        self.assertRedirects(response, "/dashboard/")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_password_recovery_is_generic_and_only_sends_to_staff_accounts(self):
        official = self.create_official()
        official.email = "official@example.com"
        official.save(update_fields=["email"])

        unknown_response = self.client.post(
            "/password-reset/",
            {"email": "unknown@example.com"},
        )
        official_response = self.client.post(
            "/password-reset/",
            {"email": "official@example.com"},
        )
        self.assertRedirects(unknown_response, "/password-reset/sent/")
        self.assertRedirects(official_response, "/password-reset/sent/")
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_can_save_recovery_email_while_signed_in(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            "/admin/recovery-email/",
            {"email": "recovery@example.com"},
        )
        self.assertRedirects(response, "/dashboard/")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.email, "recovery@example.com")

    def test_admin_can_create_separate_official_accounts(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            "/officials/",
            {
                "username": "commission-official",
                "password1": "Safe-Official-Pass-92!",
                "password2": "Safe-Official-Pass-92!",
            },
            follow=True,
        )
        self.assertEqual(
            str(list(response.context["messages"])[0]),
            "Electoral official account 'commission-official' created.",
        )
        official = get_user_model().objects.get(username="commission-official")
        self.assertFalse(official.is_staff)
        self.assertTrue(ElectoralOfficial.objects.filter(user=official).exists())

    def test_admin_can_edit_learner_and_disable_only_before_selected_election_vote(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            "/students/new/",
            {
                "admission_no": "S-NEW",
                "name": "New Learner",
                "grade": "Year 7",
                "stream": "C",
            },
        )
        self.assertRedirects(response, "/students/")
        learner = Student.objects.get(admission_no="S-NEW")
        response = self.client.post(
            f"/students/{learner.pk}/edit/",
            {
                "admission_no": "S-EDIT",
                "name": "Edited Learner",
                "grade": "Year 8",
                "stream": "B",
            },
        )
        self.assertRedirects(response, "/students/")
        learner.refresh_from_db()
        self.assertEqual(learner.name, "Edited Learner")
        self.assertEqual(learner.admission_no, "S-NEW")

        response = self.client.post(
            f"/students/{learner.pk}/eligibility/",
            {"action": "disable", "election": self.election.pk},
        )
        self.assertRedirects(response, f"/students/?election={self.election.pk}")
        learner.refresh_from_db()
        self.assertFalse(learner.is_active)

        self.client.force_login(self.create_official())
        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": "S-NEW", "find_student": "1"},
            follow=True,
        )
        self.assertContains(response, "Admission No. not found")
        response = self.client.post(
            f"/students/{learner.pk}/eligibility/",
            {"action": "enable", "election": self.election.pk},
        )
        self.assertEqual(response.status_code, 302)
        learner.refresh_from_db()
        self.assertFalse(learner.is_active)

    def test_admin_can_delete_learner_without_voting_history_from_edit_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"/students/{self.student.pk}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Delete learner")
        self.assertContains(response, "Closed-election participation records will be retained")

        response = self.client.post(f"/students/{self.student.pk}/delete/")
        self.assertRedirects(response, "/students/")
        self.assertFalse(Student.objects.filter(pk=self.student.pk).exists())

    def test_learner_with_closed_vote_can_be_removed_from_active_roll_without_losing_history(self):
        self.election.status = Election.Status.CLOSED
        self.election.save(update_fields=["status"])
        participation = Participation.objects.create(
            election=self.election, student=self.student
        )
        ballot = Ballot.objects.create(election=self.election)
        vote = Vote.objects.create(
            ballot=ballot, position=self.position, candidate=self.candidate
        )
        open_election = Election.objects.create(
            name="Current Election", status=Election.Status.OPEN
        )
        self.client.force_login(self.admin)

        response = self.client.get(f"/students/{self.student.pk}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Delete learner")
        response = self.client.post(f"/students/{self.student.pk}/delete/", follow=True)
        self.assertContains(response, "Closed-election participation records were retained")

        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertTrue(
            Participation.objects.filter(
                pk=participation.pk, election=self.election, student=self.student
            ).exists()
        )
        self.assertTrue(Ballot.objects.filter(pk=ballot.pk, election=self.election).exists())
        self.assertTrue(Vote.objects.filter(pk=vote.pk).exists())
        self.assertFalse(
            Participation.objects.filter(
                election=open_election, student=self.student
            ).exists()
        )

    def test_voted_learner_is_locked_from_edits_but_can_be_disabled_without_spoiling_ballot(self):
        Participation.objects.create(election=self.election, student=self.student)
        ballot = Ballot.objects.create(election=self.election)
        Vote.objects.create(ballot=ballot, position=self.position, candidate=self.candidate)
        self.client.force_login(self.admin)
        response = self.client.get(
            f"/students/?q={self.student.admission_no}&election={self.election.pk}"
        )
        self.assertContains(response, "Locked: voted in open election")
        self.assertNotContains(response, f"/students/{self.student.pk}/edit/")

        response = self.client.post(
            f"/students/{self.student.pk}/edit/",
            {
                "name": "Changed After Voting",
                "grade": "Year 10",
                "stream": "Z",
            },
        )
        self.assertRedirects(response, "/students/")
        self.student.refresh_from_db()
        self.assertEqual(self.student.name, "Ari Learner")
        self.assertEqual(self.student.grade, "Year 8")
        self.assertEqual(self.student.stream, "A")

        response = self.client.post(f"/students/{self.student.pk}/delete/", follow=True)
        self.assertContains(response, "cannot be deleted because they have voted in an open election")
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())

        response = self.client.get(
            f"/official/results/?election={self.election.pk}"
        )
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.create_official())
        response = self.client.get(f"/official/results/?election={self.election.pk}")
        self.assertContains(response, "1</strong><span>voted")
        self.assertContains(response, "1</strong><span>registered")
        self.assertContains(response, "100%")

        self.client.force_login(self.admin)
        response = self.client.post(
            f"/students/{self.student.pk}/eligibility/",
            {"action": "disable", "election": self.election.pk},
            follow=True,
        )
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertContains(response, "anonymous ballot and recorded vote totals are unchanged")
        self.assertTrue(Participation.objects.filter(election=self.election, student=self.student).exists())
        self.assertEqual(Vote.objects.filter(ballot__election=self.election).count(), 1)

        self.client.force_login(self.create_official("official-two"))
        response = self.client.get(f"/official/results/?election={self.election.pk}")
        self.assertContains(response, "1</strong><span>voted")
        self.assertContains(response, "1</strong><span>registered")

    def test_admin_can_edit_reset_and_delete_official_accounts(self):
        official = self.create_official()
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/officials/{official.electoral_official.pk}/edit/",
            {"username": "renamed-official"},
        )
        self.assertRedirects(response, "/officials/")
        official.refresh_from_db()
        self.assertEqual(official.username, "renamed-official")

        response = self.client.post(
            f"/officials/{official.electoral_official.pk}/reset-password/",
            follow=True,
        )
        official.refresh_from_db()
        self.assertTrue(official.check_password("12345678"))
        self.assertContains(response, "has been reset to 12345678")

        official_id = official.electoral_official.pk
        response = self.client.post(f"/officials/{official_id}/delete/", follow=True)
        self.assertFalse(get_user_model().objects.filter(pk=official.pk).exists())
        self.assertEqual(
            str(list(response.context["messages"])[0]),
            "Electoral official account 'renamed-official' deleted.",
        )

    def test_official_results_show_turnout_and_live_candidate_percentages_by_position(self):
        self.client.force_login(self.create_official())
        for admission_no, grade, stream in (
            ("S-002", "Year 8", "A"),
            ("S-003", "Year 9", "B"),
        ):
            Student.objects.create(admission_no=admission_no, name=admission_no, grade=grade, stream=stream)
        Participation.objects.create(election=self.election, student=self.student)
        for candidate in (self.candidate, self.candidate, self.second_candidate):
            ballot = Ballot.objects.create(election=self.election)
            Vote.objects.create(ballot=ballot, position=self.position, candidate=candidate)

        response = self.client.get(f"/official/results/?election={self.election.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1</strong><span>voted")
        self.assertContains(response, "3</strong><span>registered")
        self.assertContains(response, "aria-valuenow=\"33\"")
        self.assertContains(response, "President")
        self.assertContains(response, "Total votes: 3")
        self.assertContains(response, "Sam Candidate")
        self.assertContains(response, "Lee Candidate")
        self.assertContains(response, "67%")
        self.assertContains(response, "33%")
        self.assertContains(response, 'http-equiv="refresh" content="15"')
        self.assertNotContains(response, "Voting progress by grade")
        self.assertNotContains(response, "Voting progress by stream")

        self.election.status = Election.Status.CLOSED
        self.election.save(update_fields=["status"])
        closed_response = self.client.get(f"/official/results/?election={self.election.pk}")
        self.assertContains(closed_response, "Sam Candidate")
        self.assertNotContains(closed_response, 'http-equiv="refresh" content="15"')

        zero_turnout_election = Election.objects.create(name="Zero turnout")
        zero_position = Position.objects.create(election=zero_turnout_election, name="Secretary")
        Candidate.objects.create(position=zero_position, name="Candidate with no votes")
        response = self.client.get(f"/official/results/?election={zero_turnout_election.pk}")
        self.assertContains(response, "aria-valuenow=\"0\"")
        self.assertContains(response, "0</strong><span>voted")
        self.assertContains(response, "Candidate with no votes")
        self.assertContains(response, "Total votes: 0")
        self.assertContains(response, "0%")

    def test_official_voting_link_skips_picker_when_only_one_election_is_open(self):
        self.client.force_login(self.create_official())
        response = self.client.get("/vote/")
        self.assertRedirects(response, f"/vote/{self.election.pk}/")
        response = self.client.get(response.url)
        self.assertContains(response, "Enter Admission Number")

    def test_home_welcome_uses_open_election_description(self):
        self.election.description = "Josnah 2027 Elections"
        self.election.save(update_fields=["description"])
        response = self.client.get("/")
        self.assertContains(response, "Welcome to Josnah 2027 Elections")
        self.assertContains(response, "brand-mark\">S<small>v</small>")

    def test_official_voting_picker_remains_when_multiple_elections_are_open(self):
        Election.objects.create(name="Second election", status=Election.Status.OPEN)
        self.client.force_login(self.create_official())
        response = self.client.get("/vote/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student Council")
        self.assertContains(response, "Second election")

    def test_direct_official_link_explains_scheduled_voting_window(self):
        self.election.starts_at = timezone.now() + timedelta(hours=1)
        self.election.save(update_fields=["starts_at"])
        self.client.force_login(self.create_official())
        response = self.client.get("/vote/")
        self.assertRedirects(response, f"/vote/{self.election.pk}/")
        response = self.client.get(response.url)
        self.assertContains(response, "Voting has not started yet")
        self.assertNotContains(response, "Enter Admission Number")

    def test_staff_dashboard_and_election_detail_render(self):
        self.assertRedirects(self.client.get("/dashboard/"), "/login/?next=/dashboard/")

        self.client.force_login(self.admin)
        dashboard_response = self.client.get("/dashboard/")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Student Council")
        self.assertEqual(self.client.get(f"/elections/{self.election.pk}/").status_code, 200)
        self.assertContains(self.client.get(f"/elections/{self.election.pk}/"), "President")

    def test_admin_can_delete_empty_draft_election_and_its_setup(self):
        draft = Election.objects.create(name="Mistaken draft")
        position = Position.objects.create(election=draft, name="President")
        Candidate.objects.create(position=position, name="Candidate One")
        self.client.force_login(self.admin)
        detail_response = self.client.get(f"/elections/{draft.pk}/")
        self.assertContains(detail_response, "Delete election")
        response = self.client.post(f"/elections/{draft.pk}/delete/")
        self.assertRedirects(response, "/dashboard/")
        self.assertFalse(Election.objects.filter(pk=draft.pk).exists())
        self.assertFalse(Position.objects.filter(pk=position.pk).exists())

    def test_election_with_voting_records_cannot_be_deleted(self):
        ballot = Ballot.objects.create(election=self.election)
        Vote.objects.create(ballot=ballot, position=self.position, candidate=self.candidate)
        Participation.objects.create(election=self.election, student=self.student)
        self.client.force_login(self.admin)
        detail_response = self.client.get(f"/elections/{self.election.pk}/")
        self.assertNotContains(detail_response, "Delete election")
        response = self.client.post(
            f"/elections/{self.election.pk}/delete/",
            follow=True,
        )
        self.assertContains(response, "cannot be deleted because it has voting records")
        self.assertTrue(Election.objects.filter(pk=self.election.pk).exists())
        self.assertTrue(Ballot.objects.filter(pk=ballot.pk).exists())

    def test_candidate_vote_ranking_report_is_closed_only_and_sorted_highest_first(self):
        from .views import _ranked_candidate_rows

        third_candidate = Candidate.objects.create(
            position=self.position, name="No Vote Candidate"
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                f"/elections/{self.election.pk}/candidate-vote-ranking.pdf"
            ).status_code,
            404,
        )

        for candidate in (
            self.candidate,
            self.candidate,
            self.second_candidate,
        ):
            ballot = Ballot.objects.create(election=self.election)
            Vote.objects.create(
                ballot=ballot, position=self.position, candidate=candidate
            )
        self.election.status = Election.Status.CLOSED
        self.election.save(update_fields=["status"])

        rows = _ranked_candidate_rows(self.election)
        self.assertEqual(
            rows,
            [
                ("President", "Sam Candidate", 2),
                ("President", "Lee Candidate", 1),
                ("President", "No Vote Candidate", 0),
            ],
        )
        response = self.client.get(
            f"/elections/{self.election.pk}/candidate-vote-ranking.pdf"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline;", response["Content-Disposition"])

        detail = self.client.get(f"/elections/{self.election.pk}/")
        self.assertContains(detail, "Print candidate vote ranking")

    def test_admin_can_edit_and_delete_candidate_without_recorded_votes(self):
        Election.objects.filter(pk=self.election.pk).update(status=Election.Status.DRAFT)
        self.client.force_login(self.admin)
        response = self.client.get(f"/candidates/{self.candidate.pk}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Delete candidate")

        response = self.client.post(
            f"/candidates/{self.candidate.pk}/edit/",
            {"name": "Updated Candidate", "slogan": "A better school"},
        )
        self.assertRedirects(response, f"/elections/{self.election.pk}/")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.name, "Updated Candidate")
        self.assertEqual(self.candidate.slogan, "A better school")

        response = self.client.post(f"/candidates/{self.candidate.pk}/delete/")
        self.assertRedirects(response, f"/elections/{self.election.pk}/")
        self.assertFalse(Candidate.objects.filter(pk=self.candidate.pk).exists())

    def test_admin_can_upload_candidate_passport_photo_and_it_appears_on_ballot(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                output = io.BytesIO()
                Image.new("RGB", (20, 30), color="green").save(output, format="PNG")
                photo = SimpleUploadedFile(
                    "candidate.png", output.getvalue(), content_type="image/png"
                )
                Election.objects.filter(pk=self.election.pk).update(
                    status=Election.Status.DRAFT
                )
                self.client.force_login(self.admin)

                response = self.client.post(
                    f"/candidates/{self.candidate.pk}/edit/",
                    {"name": self.candidate.name, "slogan": "", "passport_photo": photo},
                )
                self.assertRedirects(response, f"/elections/{self.election.pk}/")
                self.candidate.refresh_from_db()
                self.assertTrue(self.candidate.passport_photo)
                self.assertTrue(
                    self.candidate.passport_photo.name.startswith("candidate_photos/")
                )

                self.election.status = Election.Status.OPEN
                self.election.save(update_fields=["status"])
                self.client.force_login(self.create_official())
                response = self.client.post(
                    f"/vote/{self.election.pk}/",
                    {"admission_no": self.student.admission_no, "find_student": "1"},
                )
                self.assertContains(response, self.candidate.passport_photo.url)

    def test_admin_can_add_candidate_to_open_position_without_changing_existing_votes(self):
        ballot = Ballot.objects.create(election=self.election)
        existing_vote = Vote.objects.create(
            ballot=ballot, position=self.position, candidate=self.candidate
        )
        Participation.objects.create(election=self.election, student=self.student)
        self.client.force_login(self.admin)

        detail = self.client.get(f"/elections/{self.election.pk}/")
        self.assertContains(detail, "Add candidate")
        self.assertNotContains(detail, "Add a position")

        response = self.client.post(
            f"/positions/{self.position.pk}/candidates/",
            {"name": "New Candidate", "slogan": "Fresh ideas"},
        )
        self.assertRedirects(response, f"/elections/{self.election.pk}/")
        added = Candidate.objects.get(position=self.position, name="New Candidate")
        self.assertTrue(added.is_active)
        self.assertTrue(Vote.objects.filter(pk=existing_vote.pk).exists())
        self.assertEqual(
            Vote.objects.filter(ballot__election=self.election).count(),
            1,
        )

        Student.objects.create(
            admission_no="S-002", name="Second Learner", grade="Year 8", stream="A"
        )
        self.client.force_login(self.create_official())
        ballot_page = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": "S-002", "find_student": "1"},
        )
        self.assertContains(ballot_page, "NEW CANDIDATE")

    def test_candidate_with_recorded_votes_cannot_be_deleted_but_can_be_disabled(self):
        third_candidate = Candidate.objects.create(
            position=self.position, name="Third Candidate"
        )
        ballot = Ballot.objects.create(election=self.election)
        vote = Vote.objects.create(
            ballot=ballot, position=self.position, candidate=self.candidate
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            f"/candidates/{self.candidate.pk}/delete/", follow=True
        )
        self.assertContains(response, "cannot be deleted because votes have already been recorded")
        self.assertTrue(Candidate.objects.filter(pk=self.candidate.pk).exists())
        self.assertTrue(Vote.objects.filter(pk=vote.pk).exists())

        response = self.client.post(
            f"/candidates/{self.candidate.pk}/status/",
            {"action": "disable"},
            follow=True,
        )
        self.assertContains(response, "Recorded votes are unchanged")
        self.candidate.refresh_from_db()
        self.assertFalse(self.candidate.is_active)
        self.assertTrue(Vote.objects.filter(pk=vote.pk).exists())

        response = self.client.post(
            f"/candidates/{self.candidate.pk}/status/",
            {"action": "enable"},
            follow=True,
        )
        self.candidate.refresh_from_db()
        self.assertTrue(self.candidate.is_active)
        self.assertTrue(Candidate.objects.filter(pk=third_candidate.pk).exists())

    def test_open_election_candidate_changes_keep_two_active_options_and_hide_disabled(self):
        third_candidate = Candidate.objects.create(
            position=self.position, name="Third Candidate"
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/candidates/{third_candidate.pk}/status/",
            {"action": "disable"},
        )
        self.assertRedirects(response, f"/elections/{self.election.pk}/")
        third_candidate.refresh_from_db()
        self.assertFalse(third_candidate.is_active)

        response = self.client.post(
            f"/candidates/{self.second_candidate.pk}/status/",
            {"action": "disable"},
            follow=True,
        )
        self.assertContains(response, "at least two active candidates")
        self.second_candidate.refresh_from_db()
        self.assertTrue(self.second_candidate.is_active)

        self.client.force_login(self.create_official())
        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": "S-001", "find_student": "1"},
        )
        self.assertContains(response, "SAM CANDIDATE")
        self.assertContains(response, "LEE CANDIDATE")
        self.assertNotContains(response, "THIRD CANDIDATE")

    def test_stream_restricted_positions_are_shown_and_recorded_only_for_matching_stream(self):
        lion_position = Position.objects.create(
            election=self.election,
            name="Class Senator - Lion",
            voter_stream="A",
        )
        lion_candidate = Candidate.objects.create(position=lion_position, name="Lion Candidate One")
        Candidate.objects.create(position=lion_position, name="Lion Candidate Two")
        simba_position = Position.objects.create(
            election=self.election,
            name="Class Senator - Simba",
            voter_stream="B",
        )
        Candidate.objects.create(position=simba_position, name="Simba Candidate One")
        Candidate.objects.create(position=simba_position, name="Simba Candidate Two")
        second_student = Student.objects.create(
            admission_no="S-002", name="Learner B", grade="Year 8", stream="B"
        )
        self.client.force_login(self.create_official())

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": self.student.admission_no, "find_student": "1"},
        )
        self.assertContains(response, "Class Senator - Lion")
        self.assertNotContains(response, "Class Senator - Simba")

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": second_student.admission_no, "find_student": "1"},
        )
        self.assertContains(response, "Class Senator - Simba")
        self.assertNotContains(response, "Class Senator - Lion")

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": self.student.admission_no, "find_student": "1"},
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {
                f"position_{self.position.pk}": str(self.candidate.pk),
                f"position_{lion_position.pk}": str(lion_candidate.pk),
                "cast_ballot": "1",
            },
        )
        self.assertContains(response, "Thank you for voting")
        self.assertEqual(
            set(
                Vote.objects.filter(ballot__election=self.election).values_list(
                    "position_id", flat=True
                )
            ),
            {self.position.pk, lion_position.pk},
        )
        self.assertFalse(
            Vote.objects.filter(ballot__election=self.election, position=simba_position).exists()
        )

    def test_draft_position_can_be_limited_to_a_grade_and_stream(self):
        draft = Election.objects.create(name="Stream scoped setup")
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/elections/{draft.pk}/positions/",
            {
                "name": "Class Senator - Year 8 A",
                "order": "0",
                "voter_grade": "Year 8",
                "voter_stream": "A",
            },
        )
        self.assertRedirects(response, f"/elections/{draft.pk}/")
        position = Position.objects.get(election=draft, name="Class Senator - Year 8 A")
        self.assertEqual(position.voter_grade, "Year 8")
        self.assertEqual(position.voter_stream, "A")

    def test_grade_and_stream_restricted_positions_show_only_to_matching_group(self):
        grade8_lion = Position.objects.create(
            election=self.election,
            name="Class Senator - Grade 8 Lion",
            voter_grade="Year 8",
            voter_stream="A",
        )
        grade8_candidate = Candidate.objects.create(
            position=grade8_lion, name="Grade 8 Lion Candidate"
        )
        Candidate.objects.create(position=grade8_lion, name="Grade 8 Lion Candidate Two")
        grade9_lion = Position.objects.create(
            election=self.election,
            name="Class Senator - Grade 9 Lion",
            voter_grade="Year 9",
            voter_stream="A",
        )
        Candidate.objects.create(position=grade9_lion, name="Grade 9 Lion Candidate")
        Candidate.objects.create(position=grade9_lion, name="Grade 9 Lion Candidate Two")
        grade9_student = Student.objects.create(
            admission_no="S-009", name="Grade Nine Learner", grade="Year 9", stream="A"
        )
        self.client.force_login(self.create_official())

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": self.student.admission_no, "find_student": "1"},
        )
        self.assertContains(response, "Class Senator - Grade 8 Lion")
        self.assertNotContains(response, "Class Senator - Grade 9 Lion")

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": grade9_student.admission_no, "find_student": "1"},
        )
        self.assertContains(response, "Class Senator - Grade 9 Lion")
        self.assertNotContains(response, "Class Senator - Grade 8 Lion")

        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {"admission_no": self.student.admission_no, "find_student": "1"},
        )
        response = self.client.post(
            f"/vote/{self.election.pk}/",
            {
                f"position_{self.position.pk}": str(self.candidate.pk),
                f"position_{grade8_lion.pk}": str(grade8_candidate.pk),
                "cast_ballot": "1",
            },
        )
        self.assertContains(response, "Thank you for voting")
        self.assertFalse(
            Vote.objects.filter(ballot__election=self.election, position=grade9_lion).exists()
        )

    def test_position_form_requires_grade_and_stream_together(self):
        draft = Election.objects.create(name="Scoped position validation")
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/elections/{draft.pk}/positions/",
            {"name": "Invalid scope", "order": "0", "voter_grade": "Year 8"},
        )
        self.assertRedirects(response, f"/elections/{draft.pk}/")
        self.assertFalse(Position.objects.filter(election=draft).exists())

    def test_candidate_changes_are_locked_after_election_closes(self):
        self.election.status = Election.Status.CLOSED
        self.election.save(update_fields=["status"])
        self.client.force_login(self.admin)

        response = self.client.get(f"/candidates/{self.candidate.pk}/edit/", follow=True)
        self.assertContains(response, "cannot be changed after an election is closed")
        self.assertEqual(response.redirect_chain[-1][0], f"/elections/{self.election.pk}/")
        response = self.client.post(
            f"/candidates/{self.candidate.pk}/status/",
            {"action": "disable"},
            follow=True,
        )
        self.assertContains(response, "cannot be changed after an election is closed")
        self.candidate.refresh_from_db()
        self.assertTrue(self.candidate.is_active)

    @override_settings(DEBUG=True)
    def test_first_admin_setup_is_local_one_time_and_uses_password_validation(self):
        user_model = get_user_model()
        user_model.objects.filter(is_staff=True).delete()

        response = self.client.get("/setup/")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/setup/",
            {
                "username": "first-admin",
                "password1": "SchoolVote-Test-Password-81!",
                "password2": "SchoolVote-Test-Password-81!",
            },
        )
        self.assertRedirects(response, "/login/")
        administrator = user_model.objects.get(username="first-admin")
        self.assertTrue(administrator.is_staff)
        self.assertTrue(administrator.is_superuser)
        self.assertTrue(administrator.check_password("SchoolVote-Test-Password-81!"))
        self.assertRedirects(self.client.get("/setup/"), "/login/")

        remote_client = self.client_class()
        response = remote_client.get("/setup/", REMOTE_ADDR="192.168.1.25")
        self.assertEqual(response.status_code, 404)

    def test_import_csv_uses_admission_grade_stream_and_rejects_duplicates_atomically(self):
        csv_content = b"Adm,Name,Grade,Stream\r\nS-002,Jamie Learner,Year 7,B\r\n"
        upload = SimpleUploadedFile("learners.csv", csv_content, content_type="text/csv")
        self.client.force_login(self.admin)
        response = self.client.post("/students/import/", {"file": upload}, follow=True)
        self.assertEqual(response.status_code, 200)
        imported = Student.objects.get(admission_no="S-002")
        self.assertEqual(imported.grade, "Year 7")
        self.assertEqual(imported.stream, "B")
        self.assertContains(response, "Imported 1 eligible learner")

        duplicate_upload = SimpleUploadedFile(
            "duplicate.csv",
            b"Adm,Name,Grade,Stream\r\nS-003,New Learner,Year 7,B\r\n"
            b"S-001,Existing Learner,Year 8,A\r\n",
            content_type="text/csv",
        )
        response = self.client.post("/students/import/", {"file": duplicate_upload})
        self.assertContains(response, "already exist")
        self.assertFalse(Student.objects.filter(admission_no="S-003").exists())

    def test_import_xlsx(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Adm", "Name", "Grade", "Stream"])
        sheet.append(["S-004", "Taylor Learner", "Year 9", "C"])
        sheet.append([3, "Avery Learner", "Year 8", "B"])
        admission_cell = sheet.cell(row=3, column=1, value=3)
        admission_cell.number_format = "000"
        sheet.append(["S-005", "Jordan Learner", "Year 7", "A"])
        output = io.BytesIO()
        workbook.save(output)
        upload = SimpleUploadedFile(
            "learners.xlsx",
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.client.force_login(self.admin)
        response = self.client.post("/students/import/", {"file": upload}, follow=True)
        self.assertEqual(response.status_code, 200)
        imported = Student.objects.get(admission_no="S-004")
        self.assertEqual(imported.stream, "C")
        self.assertTrue(Student.objects.filter(admission_no="003").exists())
        self.assertTrue(Student.objects.filter(admission_no="S-005").exists())

        invalid_upload = SimpleUploadedFile("broken.xlsx", b"not a workbook")
        response = self.client.post("/students/import/", {"file": invalid_upload})
        self.assertContains(response, "valid CSV or XLSX")

    def test_election_requires_multiple_candidates_to_open(self):
        draft = Election.objects.create(name="Draft")
        position = Position.objects.create(election=draft, name="Secretary")
        Candidate.objects.create(position=position, name="One Candidate")
        self.client.force_login(self.admin)
        response = self.client.post(f"/elections/{draft.pk}/status/", {"action": "open"}, follow=True)
        self.assertContains(response, "at least two candidates")
        draft.refresh_from_db()
        self.assertEqual(draft.status, Election.Status.DRAFT)

    def test_exports_are_staff_only_and_results_are_hidden_until_closed(self):
        response = self.client.get(f"/elections/{self.election.pk}/results.xlsx")
        self.assertRedirects(response, f"/login/?next=/elections/{self.election.pk}/results.xlsx")
        self.client.force_login(self.admin)
        response = self.client.get(f"/elections/{self.election.pk}/results.pdf")
        self.assertEqual(response.status_code, 404)

        ballot = Ballot.objects.create(election=self.election)
        Vote.objects.create(ballot=ballot, position=self.position, candidate=self.candidate)
        self.election.status = Election.Status.CLOSED
        self.election.save(update_fields=["status"])
        response = self.client.get(f"/elections/{self.election.pk}/results.xlsx")
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(io.BytesIO(response.content), data_only=False)
        self.assertEqual(workbook.active["A3"].value, "President")
        self.assertEqual(workbook.active["B3"].value, "Lee Candidate")
        self.assertEqual(workbook.active["C3"].value, 0)
        self.assertEqual(workbook.active["B4"].value, "Sam Candidate")
        self.assertEqual(workbook.active["C4"].value, 1)

        response = self.client.get(f"/elections/{self.election.pk}/results.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_ballot_records_have_no_shared_timestamps_or_student_foreign_key(self):
        self.assertNotIn("student", {field.name for field in Ballot._meta.fields})
        self.assertNotIn("cast_at", {field.name for field in Ballot._meta.fields})
        self.assertNotIn("voted_at", {field.name for field in Participation._meta.fields})
