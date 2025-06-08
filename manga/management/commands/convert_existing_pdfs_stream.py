# manga/management/commands/convert_existing_pdfs_stream.py
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from django.core.management.base import BaseCommand
from django.conf import settings
from PIL import Image
import fitz
from manga.models import Chapter

def convert_chapter(chapter_id):
    ch = Chapter.objects.get(id=chapter_id)
    pdf_path = ch.pdf.path

    # сколько страниц
    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    doc.close()

    # структура: …/pages/<volume>/<chapter_id>/
    out_dir = os.path.join(
        settings.MEDIA_ROOT, 'chapters', 'pages',
        str(ch.volume), str(ch.id)
    )
    os.makedirs(out_dir, exist_ok=True)

    for p in range(1, page_count+1):
        # промежуточный PNG
        base = os.path.join(out_dir, f'_p{p}')
        subprocess.run([
            'pdftoppm', '-f', str(p), '-l', str(p),
            '-png', pdf_path, base
        ], check=True)

        # найти файл _p{p}-1.png
        png_fn = next(fn for fn in os.listdir(out_dir)
                      if fn.startswith(f'_p{p}-') and fn.endswith('.png'))
        png_path = os.path.join(out_dir, png_fn)

        # конвертировать + ресайз
        with Image.open(png_path) as img:
            max_w = 1080
            if img.width > max_w:
                new_h = int(max_w * img.height / img.width)
                img = img.resize((max_w, new_h), Image.LANCZOS)

            webp_fn = f'page_{p:04d}.webp'
            img.save(os.path.join(out_dir, webp_fn),
                     'WEBP', quality=80, method=6)

        os.remove(png_path)

    return chapter_id

class Command(BaseCommand):
    help = "Параллельно конвертирует PDF→WebP по томам и главам (page-by-page)"
    def handle(self, *args, **options):
        qs = Chapter.objects.exclude(pdf__exact='')
        total = qs.count()
        self.stdout.write(f"Всего глав: {total}")

        # отбираем только новые или ещё не конвертированные
        to_do = []
        for ch in qs:
            out_dir = os.path.join(
                settings.MEDIA_ROOT, 'chapters', 'pages',
                str(ch.volume), str(ch.id)
            )
            if not (os.path.isdir(out_dir)
                    and any(f.endswith('.webp') for f in os.listdir(out_dir))):
                to_do.append(ch.id)

        self.stdout.write(f"Будем конвертировать {len(to_do)} глав…")

        workers = min(len(to_do), os.cpu_count() or 2)
        with ProcessPoolExecutor(max_workers=workers) as exe:
            futures = {exe.submit(convert_chapter, cid): cid for cid in to_do}
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    fut.result()
                    self.stdout.write(self.style.SUCCESS(f"Глава {cid} готова"))
                except Exception as e:
                    self.stderr.write(f"Ошибка главы {cid}: {e}")

        self.stdout.write(self.style.SUCCESS("Готово!"))