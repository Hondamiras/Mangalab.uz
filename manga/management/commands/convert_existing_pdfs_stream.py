# manga/management/commands/convert_existing_pdfs_stream.py
import os
import subprocess
from django.core.management.base import BaseCommand
from django.conf import settings
from PIL import Image
import fitz  # PyMuPDF, чтобы узнать page_count
from manga.models import Chapter

class Command(BaseCommand):
    help = "Постранично конвертирует все PDF глав в WebP без OOM и с правильной сортировкой"

    def handle(self, *args, **options):
        chapters = Chapter.objects.exclude(pdf__exact='')
        total = chapters.count()
        self.stdout.write(f"Найдено {total} глав для конвертации")
        for idx, ch in enumerate(chapters, 1):
            self.stdout.write(f"({idx}/{total}) Глава {ch.id}: {ch.pdf.name}")
            pdf_path = ch.pdf.path

            # Узнаём число страниц через PyMuPDF
            doc = fitz.open(pdf_path)
            page_count = doc.page_count
            doc.close()

            out_dir = os.path.join(settings.MEDIA_ROOT, 'chapters', 'pages', str(ch.id))
            os.makedirs(out_dir, exist_ok=True)

            for p in range(1, page_count + 1):
                # Генерируем PNG для страницы p
                png_base = os.path.join(out_dir, f'_p{p}')
                subprocess.run([
                    'pdftoppm',
                    '-f', str(p), '-l', str(p),
                    '-png',
                    pdf_path,
                    png_base
                ], check=True)

                # Находим файл вида _p{p}-1.png
                png_fn = next(
                    fn for fn in os.listdir(out_dir)
                    if fn.startswith(f'_p{p}-') and fn.endswith('.png')
                )
                png_path = os.path.join(out_dir, png_fn)

                # Конвертируем PNG в WebP с ресайзом и лидирующими нулями
                with Image.open(png_path) as img:
                    # Опциональный ресайз по ширине
                    max_w = 1080
                    if img.width > max_w:
                        new_h = int(max_w * img.height / img.width)
                        img = img.resize((max_w, new_h), Image.LANCZOS)

                    # Сохраняем с лидирующими нулями в имени
                    webp_fn = f'page_{p:04d}.webp'
                    webp_path = os.path.join(out_dir, webp_fn)
                    img.save(webp_path, 'WEBP', quality=80, method=6)

                # Удаляем временный PNG
                os.remove(png_path)

            self.stdout.write(self.style.SUCCESS(f"  → Глава {ch.id} готова!"))
        self.stdout.write(self.style.SUCCESS("Все главы обработаны."))
