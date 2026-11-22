import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core.models import (
    Student, Election, Position, Candidate,
    Participation, Ballot, Vote,
)


class Command(BaseCommand):
    help = "Export SchoolVote election data for secure migration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="schoolvote_migration.json",
        )

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError(
                "This export command must run against the local SQLite database."
            )

        elections = []
        for e in Election.objects.select_related("created_by").order_by("pk"):
            creator = e.created_by
            elections.append({
                "id": e.pk,
                "name": e.name,
                "description": e.description,
                "status": e.status,
                "starts_at": e.starts_at.isoformat() if e.starts_at else None,
                "ends_at": e.ends_at.isoformat() if e.ends_at else None,
                "created_at": e.created_at.isoformat(),
                "creator_is_superuser": bool(
                    creator and creator.is_superuser
                ),
                "creator_username": creator.username if creator else None,
            })

        data = {
            "format_version": 1,
            "source": "Josnah SchoolVote local database",
            "students": [
                {
                    "id": s.pk,
                    "admission_no": s.admission_no,
                    "name": s.name,
                    "grade": s.grade,
                    "stream": s.stream,
                    "is_active": s.is_active,
                }
                for s in Student.objects.order_by("pk")
            ],
            "elections": elections,
            "positions": [
                {
                    "id": p.pk,
                    "election_id": p.election_id,
                    "name": p.name,
                    "order": p.order,
                    "voter_grade": p.voter_grade,
                    "voter_stream": p.voter_stream,
                    "is_active": p.is_active,
                }
                for p in Position.objects.order_by("pk")
            ],
            "candidates": [
                {
                    "id": c.pk,
                    "position_id": c.position_id,
                    "name": c.name,
                    "slogan": c.slogan,
                    "is_active": c.is_active,
                }
                for c in Candidate.objects.order_by("pk")
            ],
            "participations": [
                {
                    "id": str(p.pk),
                    "election_id": p.election_id,
                    "student_id": p.student_id,
                }
                for p in Participation.objects.order_by("pk")
            ],
            "ballots": [
                {
                    "id": str(b.pk),
                    "election_id": b.election_id,
                }
                for b in Ballot.objects.order_by("pk")
            ],
            "votes": [
                {
                    "id": v.pk,
                    "ballot_id": str(v.ballot_id),
                    "position_id": v.position_id,
                    "candidate_id": v.candidate_id,
                }
                for v in Vote.objects.order_by("pk")
            ],
        }

        counts = {
            key: len(data[key])
            for key in (
                "students", "elections", "positions", "candidates",
                "participations", "ballots", "votes",
            )
        }

        output = Path(options["output"]).resolve()
        output.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.stdout.write(self.style.SUCCESS(
            "SchoolVote export created successfully."
        ))
        self.stdout.write(f"File: {output}")
        for name, count in counts.items():
            self.stdout.write(f"{name}: {count}")
