# manga/signals.py
import os
import subprocess
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from PIL import Image
import fitz
from .models import Chapter

@receiver(post_save, sender=Chapter)
def convert_pdf_to_webp(sender, instance, **kwargs):
    """
    После сохранения Chapter:
      — если загружен PDF, он будет постранично конвертирован в WebP.
      — сохраняет страницы в chapters/pages/v{том}_c{глава}_{id}/
    """
    if not instance.pdf:
        return

    pdf_path = instance.pdf.path

    try:
        doc = fitz.open(pdf_path)
        page_count = doc.page_count
        doc.close()
    except Exception as e:
        print(f"Ошибка чтения PDF: {e}")
        return

    out_dir = os.path.join(
        settings.MEDIA_ROOT,
        'chapters', 'pages',
        f'v{instance.volume}_c{instance.chapter_number}_{instance.id}'
    )
    os.makedirs(out_dir, exist_ok=True)

    for p in range(1, page_count + 1):
        png_base = os.path.join(out_dir, f'_p{p}')
        subprocess.run([
            'pdftoppm',
            '-f', str(p), '-l', str(p),
            '-png',
            pdf_path,
            png_base
        ], check=True)

        png_fn = next(
            fn for fn in os.listdir(out_dir)
            if fn.startswith(f'_p{p}-') and fn.endswith('.png')
        )
        png_path = os.path.join(out_dir, png_fn)

        with Image.open(png_path) as img:
            max_w = 1080
            if img.width > max_w:
                new_h = int(max_w * img.height / img.width)
                img = img.resize((max_w, new_h), Image.LANCZOS)

            webp_fn = f'page_{p:04d}.webp'
            webp_path = os.path.join(out_dir, webp_fn)
            img.save(webp_path, 'WEBP', quality=80, method=6)

        os.remove(png_path)
