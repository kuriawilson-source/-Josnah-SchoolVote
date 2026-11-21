import secrets
import uuid

from django.conf import settings
from django.db import models


def generate_pin_salt():
    return secrets.token_hex(16)


class Student(models.Model):
    admission_no = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    grade = models.CharField(max_length=40, blank=True)
    stream = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.admission_no})"


class ElectoralOfficial(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="electoral_official",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.get_username()


class Election(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Position(models.Model):
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="positions")
    name = models.CharField(max_length=120)
    order = models.PositiveSmallIntegerField(default=0)
    voter_grade = models.CharField(max_length=40, blank=True)
    voter_stream = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["election", "name"], name="unique_position_per_election")
        ]

    def __str__(self):
        return self.name


class Candidate(models.Model):
    position = models.ForeignKey(Position, on_delete=models.CASCADE, related_name="candidates")
    name = models.CharField(max_length=160)
    slogan = models.CharField(max_length=240, blank=True)
    passport_photo = models.ImageField(upload_to="candidate_photos/", blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["position", "name"], name="unique_candidate_per_position")
        ]

    def __str__(self):
        return self.name


class Participation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    election = models.ForeignKey(Election, on_delete=models.PROTECT)
    student = models.ForeignKey(Student, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["election", "student"], name="one_participation_per_student_election"
            )
        ]


class Ballot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    election = models.ForeignKey(Election, on_delete=models.PROTECT, related_name="ballots")


class Vote(models.Model):
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name="votes")
    position = models.ForeignKey(Position, on_delete=models.PROTECT)
    candidate = models.ForeignKey(Candidate, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ballot", "position"], name="one_choice_per_position")
        ]
