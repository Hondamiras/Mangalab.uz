# accounts/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from .models import UserProfile
from manga.models import Manga, Chapter, Page

@receiver(post_save, sender=UserProfile)
def grant_translator_perms(sender, instance, created, **kwargs):
    if instance.is_translator and instance.user.is_staff:
        for model in [Manga, Chapter, Page]:
            ct = ContentType.objects.get_for_model(model)
            perms = Permission.objects.filter(content_type=ct)
            instance.user.user_permissions.add(*perms)