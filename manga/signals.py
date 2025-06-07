# manga/signals.py
import os
from pdf2image import convert_from_path
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import Chapter

@receiver(post_save, sender=Chapter)
def pdf_to_webp_pages(sender, instance, **kwargs):
    """
    При сохранении Chapter конвертирует его PDF в WebP-страницы:
      - dpi=150, fmt='webp', quality=85
      - сохраняет в MEDIA_ROOT/chapters/pages/<chapter.id>/page_<n>.webp
    """
    if not instance.pdf:
        return

    pdf_path = instance.pdf.path
    out_dir = os.path.join(settings.MEDIA_ROOT, 'chapters', 'pages', str(instance.id))
    os.makedirs(out_dir, exist_ok=True)

    images = convert_from_path(pdf_path, dpi=150, fmt='webp')
    for idx, img in enumerate(images, start=1):
        img.save(
            os.path.join(out_dir, f'page_{idx}.webp'),
            'WEBP',
            quality=85
        )
