# manga/signals.py

import os
import subprocess
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Chapter

@receiver(post_save, sender=Chapter)
def optimize_pdf(sender, instance, **kwargs):
    """
    После сохранения Chapter:
      1) Проверяем, есть ли PDF.
      2) Сжимаем его через Ghostscript с профилем /ebook (150–200 dpi).
      3) Заменяем исходник на сжатый файл.
    """
    if not instance.pdf:
        return

    orig_path = instance.pdf.path
    tmp_path = orig_path.replace('.pdf', '_cmp.pdf')

    # Запускаем Ghostscript
    subprocess.run([
        'gs',
        '-sDEVICE=pdfwrite',
        '-dPDFSETTINGS=/ebook',   # или '/screen' для ещё более агрессивного сжатия
        '-dNOPAUSE',
        '-dBATCH',
        '-dQUIET',
        f'-sOutputFile={tmp_path}',
        orig_path
    ], check=True)

    # Заменяем оригинал на сжатую версию
    os.replace(tmp_path, orig_path)
