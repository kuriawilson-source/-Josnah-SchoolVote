
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the first SchoolVote administrator."

    def handle(self, *args, **options):
        username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()

        if not username or not password:
            self.stdout.write("Administrator setup not configured. Skipping.")
            return

        User = get_user_model()

        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write(
                "An administrator already exists. Skipping creation."
            )
            return

        if User.objects.filter(username=username).exists():
            raise CommandError(
                "That username already exists. No account was created."
            )

        user = User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Administrator '{user.username}' created successfully."
            )
        )
