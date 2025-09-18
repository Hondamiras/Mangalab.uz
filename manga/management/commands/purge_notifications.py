# manga/management/commands/purge_notifications.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from manga.models import NewChapterNotification

class Command(BaseCommand):
    help = "Yaroqlilik muddati tugagan notifikatsiyalarni o‘chiradi."

    def handle(self, *args, **options):
        deleted, _ = NewChapterNotification.objects.filter(
            expires_at__lte=timezone.now()
        ).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} expired notifications"))
