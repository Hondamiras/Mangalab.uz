import os
import tempfile

import pypdfium2 as pdfium
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from manga.models import ChapterPDFJob
from manga.services.pdf_to_pages import render_pdf_to_pages  # sizdagi funksiya


def _get_local_pdf_path(job: ChapterPDFJob):
    """
    Local storage bo‘lsa job.pdf.path ishlaydi.
    Remote bo‘lsa tempga ko‘chiradi.
    Returns: (path, should_delete_temp)
    """
    try:
        return job.pdf.path, False
    except Exception:
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as out:
            with default_storage.open(job.pdf.name, "rb") as src:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    out.write(chunk)
        return tmp_path, True


class Command(BaseCommand):
    help = "ChapterPDFJob navbatini ishlatadi (PDF->WEBP). Redis shart emas."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Faqat bitta job ishlatib to‘xtaydi")
        parser.add_argument("--sleep", type=int, default=2, help="Navbat bo‘sh bo‘lsa kutish (sec)")

    def handle(self, *args, **opts):
        import time

        once = opts["once"]
        sleep_s = opts["sleep"]

        self.stdout.write(self.style.SUCCESS("PDF worker started..."))

        while True:
            job = None

            # ✅ 2 ta worker bir-biriga urilib ketmasin (skip_locked)
            with transaction.atomic():
                job = (
                    ChapterPDFJob.objects
                    .select_for_update(skip_locked=True)
                    .filter(status="PENDING")
                    .order_by("created_at", "id")
                    .first()
                )
                if job:
                    job.status = "PROCESSING"
                    job.started_at = timezone.now()
                    job.error = ""
                    job.progress = 0
                    job.total = 0
                    job.save(update_fields=["status", "started_at", "error", "progress", "total"])

            if not job:
                if once:
                    self.stdout.write("No pending jobs. Exit.")
                    return
                time.sleep(sleep_s)
                continue

            # PROCESS
            local_path = None
            should_delete_temp = False

            try:
                local_path, should_delete_temp = _get_local_pdf_path(job)

                # total pages
                pdf = pdfium.PdfDocument(local_path)
                total = len(pdf)
                try:
                    pdf.close()
                except Exception:
                    pass

                ChapterPDFJob.objects.filter(pk=job.pk).update(total=total)

                # progress callback
                def _progress(done, total_pages):
                    ChapterPDFJob.objects.filter(pk=job.pk).update(progress=done, total=total_pages)

                created = render_pdf_to_pages(
                    job.chapter,
                    local_path,
                    dpi=job.dpi,
                    max_width=job.max_width,
                    replace_existing=job.replace_existing,
                    quality=job.quality,
                    progress_cb=_progress,      # ✅ agar siz funksiyaga qo‘shgan bo‘lsangiz
                    total_pages=total,          # ✅
                )

                ChapterPDFJob.objects.filter(pk=job.pk).update(
                    status="DONE",
                    finished_at=timezone.now(),
                    progress=total,
                    total=total,
                    error="",
                )

                # ✅ PDFni o‘chiramiz (storage’dan ham)
                try:
                    job.pdf.delete(save=False)
                except Exception:
                    pass

                self.stdout.write(self.style.SUCCESS(f"Done job #{job.pk}: created={created}"))

            except Exception as e:
                ChapterPDFJob.objects.filter(pk=job.pk).update(
                    status="FAILED",
                    finished_at=timezone.now(),
                    error=str(e)[:1500],
                )
                self.stderr.write(self.style.ERROR(f"Failed job #{job.pk}: {e}"))

            finally:
                if should_delete_temp and local_path:
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass

            if once:
                return
