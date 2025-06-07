# manga/management/commands/convert_existing_pdfs.py
import os
from django.core.management.base import BaseCommand
from django.conf import settings
from pdf2image import convert_from_path
from manga.models import Chapter

class Command(BaseCommand):
    help = "Конвертирует все существующие PDF глав в WebP-страницы"

    def handle(self, *args, **options):
        chapters = Chapter.objects.exclude(pdf__exact='')
        total = chapters.count()
        self.stdout.write(f"Найдено {total} глав для конвертации")
        for idx, ch in enumerate(chapters, 1):
            self.stdout.write(f"({idx}/{total}) Глава {ch.id} — {ch.pdf.name}")
            pdf_path = ch.pdf.path
            out_dir = os.path.join(settings.MEDIA_ROOT, 'chapters', 'pages', str(ch.id))
            os.makedirs(out_dir, exist_ok=True)
            images = convert_from_path(pdf_path, dpi=150, fmt='webp')
            for p, img in enumerate(images, 1):
                img.save(
                    os.path.join(out_dir, f'page_{p}.webp'),
                    'WEBP',
                    quality=85
                )
        self.stdout.write(self.style.SUCCESS("Конвертация завершена."))
