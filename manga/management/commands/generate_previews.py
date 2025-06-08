from django.core.management.base import BaseCommand
from manga.models import Chapter

class Command(BaseCommand):
    help = "Генерирует превью для всех глав без preview"

    def handle(self, *args, **opts):
        qs = Chapter.objects.exclude(pdf__isnull=True).filter(preview__isnull=True)
        total = qs.count()
        self.stdout.write(f"Будем обрабатывать {total} глав…")
        for ch in qs:
            ch.save()
            self.stdout.write(f"Сгенерено: {ch}")
        self.stdout.write("Готово.")