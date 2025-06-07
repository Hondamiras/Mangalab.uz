# your_app/signals.py
import os
from pdf2image import convert_from_path
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings

from .models import Chapter

@receiver(post_save, sender=Chapter)
def pdf_to_webp_pages(sender, instance, **kwargs):
    """
    При каждом сохранении Chapter:
      - Если есть PDF, конвертируем его в WebP-страницы.
      - dpi=150, quality=85 (компромисс скорость/качество).
      - Файлы сохраняем в media/chapters/pages/<chapter_id>/page_<n>.webp.
    """
    if not instance.pdf:
        return

    pdf_path = instance.pdf.path
    # Директория для выверенных картинок
    out_dir = os.path.join(
        settings.MEDIA_ROOT,
        'chapters', 'pages',
        str(instance.id)
    )
    os.makedirs(out_dir, exist_ok=True)

    # Конвертация PDF → список PIL.Image
    images = convert_from_path(
        pdf_path,
        dpi=150,
        fmt='webp'
    )

    # Сохраняем каждую страницу
    for idx, img in enumerate(images, start=1):
        img_path = os.path.join(out_dir, f'page_{idx}.webp')
        img.save(img_path, 'WEBP', quality=85)
