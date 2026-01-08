import math
from io import BytesIO
from typing import Callable, Optional, Tuple

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageOps

from django.core.files.base import ContentFile
from django.db.models import Max

from ..models import Page

WEBP_MAX_DIM = 16383
CHUNK_HEIGHT = 12000

# DPI limitlar: xiralik bo'lmasin, lekin server ham o'lmasin :)
MIN_DPI = 144
MAX_DPI = 450   # kerak bo'lsa 600 ham qilsa bo'ladi, lekin sekinlashadi


def _detect_content_crop_units(
    page: "pdfium.PdfPage",
    w_units: float,
    h_units: float,
    *,
    preview_max_width_px: int = 700,
    threshold: int = 18,
    pad_px: int = 12,
) -> Tuple[float, float]:
    """
    Sahifaning yon oq marginlarini topadi (left/right crop) va PDF unitda qaytaradi.
    Past rezolyutsiya preview bilan ishlaydi => tez.
    """
    # preview scale: taxminan 700px kenglik
    if w_units <= 0:
        return 0.0, 0.0

    preview_scale = float(preview_max_width_px) / float(w_units)
    preview_scale = max(0.20, min(preview_scale, 2.0))  # juda kichik/kattani cheklaymiz

    bmp = page.render(scale=preview_scale)
    try:
        img = bmp.to_pil().convert("RGB")

        # oq fon bilan farqini olib, threshold qilamiz
        bg = Image.new("RGB", img.size, (255, 255, 255))
        diff = ImageChops.difference(img, bg).convert("L")
        diff = ImageOps.autocontrast(diff)
        diff = diff.point(lambda p: 255 if p > threshold else 0)

        bbox = diff.getbbox()  # (left, top, right, bottom) px
        if not bbox:
            return 0.0, 0.0

        left_px, _, right_px, _ = bbox

        # padding (kesib yubormasin)
        left_px = max(0, left_px - pad_px)
        right_px = min(img.width, right_px + pad_px)

        # unitga o'tkazamiz: px = unit * scale
        crop_left_units = float(left_px) / float(preview_scale)
        crop_right_units = float(img.width - right_px) / float(preview_scale)

        # safety: haddan oshmasin
        crop_left_units = max(0.0, min(crop_left_units, w_units - 1.0))
        crop_right_units = max(0.0, min(crop_right_units, w_units - 1.0))

        # agar juda agressiv bo'lib qolsa (content juda tor bo'lsa), kesmaymiz
        content_w = w_units - crop_left_units - crop_right_units
        if content_w <= w_units * 0.35:  # juda tor => no crop
            return 0.0, 0.0

        return crop_left_units, crop_right_units
    finally:
        try:
            bmp.close()
        except Exception:
            pass


def render_pdf_to_pages(
    chapter,
    pdf_path: str,
    *,
    dpi: int = 144,                 # endi bu "min dpi" sifatida ishlaydi
    max_width: int = 1400,          # target content width
    replace_existing: bool = True,
    quality: int = 82,
    webp_method: int = 4,
    progress_cb: Optional[Callable[[int, int], None]] = None,

    # ✅ worker eski kodida total_pages= yuborishi mumkin
    total_pages: Optional[int] = None,
) -> int:
    """
    PDF -> WEBP.
    - Kattalashtirib resize QILMAYDI (xiralik yo'q).
    - Content bbox aniqlab, yon marginlarni kesib render qiladi (sharp + tezroq).
    - Uzun sahifalar chunk bo'lib render bo'ladi (RAM yengil).
    """

    min_dpi = max(int(dpi or MIN_DPI), MIN_DPI)
    max_dpi = MAX_DPI
    target_w = int(max_width or 1400)
    quality = int(quality or 82)
    webp_method = int(webp_method if webp_method is not None else 4)

    if replace_existing:
        Page.objects.filter(chapter=chapter).delete()
        page_no = 0
    else:
        page_no = (
            Page.objects.filter(chapter=chapter)
            .aggregate(Max("page_number"))["page_number__max"]
            or 0
        )

    created = 0
    pdf = pdfium.PdfDocument(pdf_path)

    try:
        # total bo'laklarni hisoblash (progress uchun)
        total_pieces = 0

        # 1-pass: total pieces estimate
        for i in range(len(pdf)):
            p = pdf[i]
            try:
                try:
                    w_units, h_units = p.get_size()
                except Exception:
                    w_units, h_units = 595.0, 842.0

                # content crop topamiz (tez preview)
                crop_l, crop_r = _detect_content_crop_units(p, w_units, h_units)

                content_w_units = max(1.0, float(w_units) - float(crop_l) - float(crop_r))

                # scale: target_w bo'yicha + min_dpi
                scale_by_width = float(target_w) / float(content_w_units)
                scale_by_dpi = float(min_dpi) / 72.0
                scale = max(scale_by_width, scale_by_dpi)

                # max_dpi limit
                scale = min(scale, float(max_dpi) / 72.0)

                # webp width limit
                max_scale_webp = float(WEBP_MAX_DIM) / float(content_w_units)
                scale = min(scale, max_scale_webp)

                predicted_h_px = float(h_units) * float(scale)
                pieces = max(1, int(math.ceil(predicted_h_px / float(CHUNK_HEIGHT))))
                total_pieces += pieces
            finally:
                try:
                    p.close()
                except Exception:
                    pass

        if progress_cb:
            progress_cb(0, total_pieces)

        # 2-pass: real render
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                try:
                    w_units, h_units = page.get_size()
                except Exception:
                    w_units, h_units = 595.0, 842.0

                crop_l, crop_r = _detect_content_crop_units(page, w_units, h_units)
                content_w_units = max(1.0, float(w_units) - float(crop_l) - float(crop_r))

                scale_by_width = float(target_w) / float(content_w_units)
                scale_by_dpi = float(min_dpi) / 72.0
                scale = max(scale_by_width, scale_by_dpi)

                scale = min(scale, float(max_dpi) / 72.0)
                scale = min(scale, float(WEBP_MAX_DIM) / float(content_w_units))
                scale = max(scale, 0.10)

                chunk_units = float(CHUNK_HEIGHT) / float(scale)

                slice_top = float(h_units)
                while slice_top > 0:
                    slice_bottom = max(0.0, slice_top - chunk_units)

                    top_margin = float(h_units) - float(slice_top)
                    bottom_margin = float(slice_bottom)

                    # crop tuple: (left, top, right, bottom) marginlar (unitda)
                    crop = (float(crop_l), float(top_margin), float(crop_r), float(bottom_margin))

                    bmp = page.render(scale=scale, crop=crop)
                    try:
                        img = bmp.to_pil().convert("RGB")

                        # ✅ bu yerda kattalashtirish YO'Q.
                        # faqat safety (kamdan-kam limitdan oshsa) kichraytirish
                        if img.width > WEBP_MAX_DIM:
                            new_h = int(img.height * (WEBP_MAX_DIM / img.width))
                            img = img.resize((WEBP_MAX_DIM, new_h), Image.LANCZOS)
                        if img.height > WEBP_MAX_DIM:
                            new_w = int(img.width * (WEBP_MAX_DIM / img.height))
                            img = img.resize((new_w, WEBP_MAX_DIM), Image.LANCZOS)

                        buf = BytesIO()
                        img.save(buf, format="WEBP", quality=quality, method=webp_method)
                        buf.seek(0)

                        page_no += 1
                        fname = (
                            f"chapters/{chapter.manga.slug}/v{chapter.volume}/"
                            f"ch{chapter.chapter_number}/{page_no:03d}.webp"
                        )

                        page_obj = Page(chapter=chapter, page_number=page_no)
                        page_obj.image.save(fname, ContentFile(buf.read()), save=True)

                        created += 1
                        if progress_cb:
                            progress_cb(created, total_pieces)

                    finally:
                        try:
                            bmp.close()
                        except Exception:
                            pass

                    slice_top = slice_bottom

            finally:
                try:
                    page.close()
                except Exception:
                    pass

    finally:
        try:
            pdf.close()
        except Exception:
            pass

    return created
