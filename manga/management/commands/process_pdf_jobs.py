# python manage.py process_pdf_jobs --sleep 1

# manga/management/commands/pdf_worker.py
import os
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple, Any

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from manga.models import ChapterPDFJob
from manga.services.pdf_to_pages import render_pdf_to_pages


# -------------------------
# Helpers
# -------------------------
def _get_local_pdf_path(job: ChapterPDFJob) -> Tuple[str, bool]:
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


@dataclass
class ProgressThrottler:
    """
    DB update’larni kamroq qilish (har chunkda UPDATE qilmaslik uchun).
    """
    min_interval_sec: float = 0.5   # kamida 0.5s da 1 marta update
    min_step: int = 3               # yoki 3ta progress o‘tganda update
    _last_ts: float = 0.0
    _last_done: int = 0

    def should_flush(self, done: int, total: int) -> bool:
        now = time.monotonic()
        if total > 0 and done >= total:
            return True
        if (done - self._last_done) >= self.min_step:
            return True
        if (now - self._last_ts) >= self.min_interval_sec:
            return True
        return False

    def mark_flushed(self, done: int):
        self._last_done = done
        self._last_ts = time.monotonic()


# -------------------------
# Command
# -------------------------
class Command(BaseCommand):
    help = "ChapterPDFJob navbatini ishlatadi (PDF->WEBP). Redis shart emas."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Faqat bitta job ishlatib to‘xtaydi")
        parser.add_argument("--sleep", type=float, default=2.0, help="Navbat bo‘sh bo‘lsa kutish (sec)")
        parser.add_argument(
            "--requeue-stale-minutes",
            type=int,
            default=0,
            help="PROCESSING holatida uzoq qolgan job’larni PENDING’ga qaytarish (0 = o‘chirilgan).",
        )

    def handle(self, *args, **opts):
        once: bool = bool(opts["once"])
        sleep_s: float = float(opts["sleep"])
        stale_min: int = int(opts["requeue_stale_minutes"])

        self.stdout.write(self.style.SUCCESS("PDF worker started... (CTRL+C to stop)"))

        while True:
            try:
                if stale_min > 0:
                    self._requeue_stale_jobs(minutes=stale_min)

                job = self._pick_next_job()
                if not job:
                    if once:
                        self.stdout.write("No pending jobs. Exit.")
                        return
                    time.sleep(sleep_s)
                    continue

                self._process_job(job)

                if once:
                    return

            except KeyboardInterrupt:
                self.stdout.write("\nStopped by user.")
                return

    # -------------------------
    # DB ops
    # -------------------------
    def _requeue_stale_jobs(self, *, minutes: int):
        """
        Server o‘chib qolib job PROCESSING bo‘lib qolsa, qayta PENDING qilish.
        """
        cutoff = timezone.now() - timedelta(minutes=minutes)  # ✅ FIX

        updated = (
            ChapterPDFJob.objects
            .filter(status=ChapterPDFJob.STATUS_PROCESSING, started_at__lt=cutoff)
            .update(
                status=ChapterPDFJob.STATUS_PENDING,
                error="Requeued: stale PROCESSING job.",
                progress=0,
                total=0,
                started_at=None,
                finished_at=None,
            )
        )
        if updated:
            self.stdout.write(self.style.WARNING(f"Requeued stale jobs: {updated}"))

    def _pick_next_job(self) -> Optional[ChapterPDFJob]:
        """
        Navbatdan bitta PENDING jobni xavfsiz olib PROCESSING’ga o‘tkazadi.
        skip_locked=True -> bir nechta worker bo‘lsa ham urilmaydi.
        """
        with transaction.atomic():
            job = (
                ChapterPDFJob.objects
                .select_for_update(skip_locked=True)
                .filter(status=ChapterPDFJob.STATUS_PENDING)
                .order_by("created_at", "id")
                .first()
            )
            if not job:
                return None

            job.status = ChapterPDFJob.STATUS_PROCESSING
            job.started_at = timezone.now()
            job.finished_at = None
            job.error = ""
            job.progress = 0
            job.total = 0
            job.save(update_fields=["status", "started_at", "finished_at", "error", "progress", "total"])
            return job

    # -------------------------
    # Processing
    # -------------------------
    def _process_job(self, job: ChapterPDFJob):
        local_path = None
        should_delete_temp = False

        try:
            if not job.pdf:
                raise RuntimeError("Job PDF file is missing (job.pdf is empty).")

            local_path, should_delete_temp = _get_local_pdf_path(job)

            throttler = ProgressThrottler(min_interval_sec=0.5, min_step=3)

            def _progress(done: int, total: int):
                done = int(done or 0)
                total = int(total or 0)

                if throttler.should_flush(done, total):
                    ChapterPDFJob.objects.filter(pk=job.pk).update(progress=done, total=total)
                    throttler.mark_flushed(done)

            # ✅ render_pdf_to_pages int ham qaytarishi mumkin, (created,total) ham
            result: Any = render_pdf_to_pages(
                job.chapter,
                local_path,
                dpi=job.dpi,
                max_width=job.max_width,
                replace_existing=job.replace_existing,
                quality=job.quality,
                progress_cb=_progress,
            )

            created = 0
            if isinstance(result, tuple) and len(result) >= 1:
                created = int(result[0] or 0)
                # agar tuple ichida total ham bo‘lsa, DBga yozib qo‘yamiz
                if len(result) >= 2 and result[1] is not None:
                    ChapterPDFJob.objects.filter(pk=job.pk).update(total=int(result[1]))
            else:
                created = int(result or 0)

            # Final statusni “to‘liq” qilish
            fresh = ChapterPDFJob.objects.only("total", "progress").get(pk=job.pk)  # ✅ FIX
            total = int(fresh.total or 0)
            prog = int(fresh.progress or 0)

            ChapterPDFJob.objects.filter(pk=job.pk).update(
                status=ChapterPDFJob.STATUS_DONE,
                finished_at=timezone.now(),
                progress=(total if total > 0 else prog),
                total=(total if total > 0 else prog),
                error="",
            )

            # PDFni storage’dan o‘chirish + fieldni tozalash
            job = ChapterPDFJob.objects.get(pk=job.pk)
            try:
                job.pdf.delete(save=True)  # save=True -> DB field ham bo‘shaydi
            except Exception:
                pass

            self.stdout.write(self.style.SUCCESS(f"Done job #{job.pk}: created={created}"))

        except Exception as e:
            err = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            ChapterPDFJob.objects.filter(pk=job.pk).update(
                status=ChapterPDFJob.STATUS_FAILED,
                finished_at=timezone.now(),
                error=(err or str(e) or "")[:4000],
            )
            self.stderr.write(self.style.ERROR(f"Failed job #{job.pk}: {e}"))

        finally:
            if should_delete_temp and local_path:
                try:
                    os.remove(local_path)
                except Exception:
                    pass
