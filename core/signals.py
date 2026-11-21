from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Candidate


@receiver(post_delete, sender=Candidate)
def delete_candidate_photo_file(sender, instance, **kwargs):
    if instance.passport_photo:
        storage = instance.passport_photo.storage
        name = instance.passport_photo.name
        transaction.on_commit(lambda: storage.delete(name))
