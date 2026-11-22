
import json
import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    Student, Election, Position, Candidate,
    Participation, Ballot, Vote,
)


def parse_date(value):
    if not value:
        return None
    result = parse_datetime(value)
    if result is None:
        raise ValueError("Invalid date in migration file.")
    if timezone.is_naive(result):
        result = timezone.make_aware(result)
    return result


@login_required
@user_passes_test(lambda user: user.is_staff)
def migration_upload(request):
    if request.method == "GET":
        return render(request, "core/migration_upload.html")

    if request.method != "POST":
        return HttpResponse(status=405)

    uploaded = request.FILES.get("migration_file")

    if not uploaded or uploaded.size > 5 * 1024 * 1024:
        messages.error(request, "Select a migration JSON file under 5 MB.")
        return redirect("migration-upload")

    try:
        data = json.loads(uploaded.read().decode("utf-8"))

        required = [
            "students", "elections", "positions", "candidates",
            "participations", "ballots", "votes",
        ]

        if data.get("format_version") != 1:
            raise ValueError("Unsupported migration file version.")

        for key in required:
            if not isinstance(data.get(key), list):
                raise ValueError(f"Invalid or missing section: {key}")

        User = get_user_model()
        admin = User.objects.filter(
            username="Admin",
            is_staff=True,
            is_superuser=True,
        ).first()

        if admin is None:
            raise ValueError(
                "The existing online Admin account was not found."
            )

        # Never overwrite existing online election data.
        models_to_check = [
            Student, Election, Position, Candidate,
            Participation, Ballot, Vote,
        ]

        for model in models_to_check:
            if model.objects.exists():
                raise ValueError(
                    f"Online {model.__name__} records already exist. "
                    "Import stopped to protect existing data."
                )

        student_ids = {int(x["id"]) for x in data["students"]}
        election_ids = {int(x["id"]) for x in data["elections"]}
        position_ids = {int(x["id"]) for x in data["positions"]}
        candidate_ids = {int(x["id"]) for x in data["candidates"]}

        for item in data["positions"]:
            if int(item["election_id"]) not in election_ids:
                raise ValueError("A position references a missing election.")

        for item in data["candidates"]:
            if int(item["position_id"]) not in position_ids:
                raise ValueError("A candidate references a missing position.")

        for item in data["participations"]:
            if (
                int(item["election_id"]) not in election_ids
                or int(item["student_id"]) not in student_ids
            ):
                raise ValueError("Invalid participation reference.")

        for item in data["ballots"]:
            if int(item["election_id"]) not in election_ids:
                raise ValueError("A ballot references a missing election.")

        for item in data["votes"]:
            if (
                int(item["position_id"]) not in position_ids
                or int(item["candidate_id"]) not in candidate_ids
            ):
                raise ValueError("Invalid vote reference.")

            candidate = next(
                c for c in data["candidates"]
                if int(c["id"]) == int(item["candidate_id"])
            )
            if int(candidate["position_id"]) != int(item["position_id"]):
                raise ValueError("A vote has a mismatched candidate and position.")

        student_map = {}
        election_map = {}
        position_map = {}
        candidate_map = {}

        with transaction.atomic():

            for item in data["students"]:
                obj = Student.objects.create(
                    admission_no=item["admission_no"],
                    name=item["name"],
                    grade=item.get("grade", ""),
                    stream=item.get("stream", ""),
                    is_active=item.get("is_active", True),
                )
                student_map[int(item["id"])] = obj

            for item in data["elections"]:
                creator_name = item.get("creator_username")

                if item.get("creator_is_superuser"):
                    if creator_name != "WilsonK":
                        raise ValueError(
                            "Unexpected local election creator. Import stopped."
                        )
                    creator = admin
                elif creator_name:
                    raise ValueError(
                        "An unmapped local election creator was found."
                    )
                else:
                    creator = None

                obj = Election.objects.create(
                    name=item["name"],
                    description=item.get("description", ""),
                    status=item["status"],
                    starts_at=parse_date(item.get("starts_at")),
                    ends_at=parse_date(item.get("ends_at")),
                    created_by=creator,
                )
                Election.objects.filter(pk=obj.pk).update(
                    created_at=parse_date(item["created_at"])
                )
                election_map[int(item["id"])] = obj

            for item in data["positions"]:
                obj = Position.objects.create(
                    election=election_map[int(item["election_id"])],
                    name=item["name"],
                    order=item.get("order", 0),
                    voter_grade=item.get("voter_grade", ""),
                    voter_stream=item.get("voter_stream", ""),
                    is_active=item.get("is_active", True),
                )
                position_map[int(item["id"])] = obj

            for item in data["candidates"]:
                obj = Candidate.objects.create(
                    position=position_map[int(item["position_id"])],
                    name=item["name"],
                    slogan=item.get("slogan", ""),
                    is_active=item.get("is_active", True),
                )
                candidate_map[int(item["id"])] = obj

            for item in data["participations"]:
                Participation.objects.create(
                    id=uuid.UUID(item["id"]),
                    election=election_map[int(item["election_id"])],
                    student=student_map[int(item["student_id"])],
                )

            for item in data["ballots"]:
                Ballot.objects.create(
                    id=uuid.UUID(item["id"]),
                    election=election_map[int(item["election_id"])],
                )

            for item in data["votes"]:
                Vote.objects.create(
                    ballot_id=uuid.UUID(item["ballot_id"]),
                    position=position_map[int(item["position_id"])],
                    candidate=candidate_map[int(item["candidate_id"])],
                )

        messages.success(
            request,
            "Migration completed successfully. "
            f"Imported {len(data['students'])} learners, "
            f"{len(data['elections'])} elections, "
            f"{len(data['positions'])} positions, "
            f"{len(data['candidates'])} candidates, "
            f"{len(data['participations'])} participations, "
            f"{len(data['ballots'])} ballots and "
            f"{len(data['votes'])} votes."
        )
        return redirect("migration-upload")

    except Exception as exc:
        messages.error(request, f"Migration stopped: {exc}")
        return redirect("migration-upload")
