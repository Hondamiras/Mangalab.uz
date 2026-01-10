import math
from io import BytesIO
from typing import Callable, Optional, Tuple, List, Dict, Any

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageOps

from django.core.files.base import ContentFile
from django.db.models import Max

from ..models import Page

WEBP_MAX_DIM = 16383
CHUNK_HEIGHT = 12000  # split_long_pages=True bo‘lsa ishlaydi

MIN_DPI = 144
MAX_DPI = 450


def _safe_close(obj) -> None:
    try:
        obj.close()
    except Exception:
        pass


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
    Sahifaning yon (left/right) oq marginlarini topadi va PDF unit’da qaytaradi.
    Tez preview render bilan ishlaydi.
    """
    if w_units <= 0:
        return 0.0, 0.0

    preview_scale = float(preview_max_width_px) / float(w_units)
    preview_scale = max(0.20, min(preview_scale, 2.0))

    bmp = page.render(scale=preview_scale)
    try:
        img = bmp.to_pil().convert("RGB")

        bg = Image.new("RGB", img.size, (255, 255, 255))
        diff = ImageChops.difference(img, bg).convert("L")
        diff = ImageOps.autocontrast(diff)
        diff = diff.point(lambda p: 255 if p > threshold else 0)

        bbox = diff.getbbox()  # (left, top, right, bottom) px
        if not bbox:
            return 0.0, 0.0

        left_px, _, right_px, _ = bbox
        left_px = max(0, left_px - pad_px)
        right_px = min(img.width, right_px + pad_px)

        crop_left_units = float(left_px) / float(preview_scale)
        crop_right_units = float(img.width - right_px) / float(preview_scale)

        crop_left_units = max(0.0, min(crop_left_units, w_units - 1.0))
        crop_right_units = max(0.0, min(crop_right_units, w_units - 1.0))

        content_w = w_units - crop_left_units - crop_right_units
        if content_w <= w_units * 0.35:
            return 0.0, 0.0

        return crop_left_units, crop_right_units
    finally:
        _safe_close(bmp)


def _pick_scale(
    *,
    content_w_units: float,
    h_units: float,
    target_w_px: int,
    min_dpi: int,
    max_dpi: int,
    force_single_image: bool,
) -> float:
    """
    Scale tanlash:
    - width bo‘yicha target_w_px ga chiqadi
    - min_dpi dan pastga tushmaslikka harakat qiladi
    - max_dpi dan oshirmaydi
    - WEBP_MAX_DIM limitdan oshirmaydi
    - force_single_image=True bo‘lsa: height ham WEBP_MAX_DIM dan oshmasin (1 sahifa = 1 WEBP)
    """
    content_w_units = max(1.0, float(content_w_units))
    h_units = max(1.0, float(h_units))

    scale_by_width = float(target_w_px) / float(content_w_units)
    scale_by_dpi = float(min_dpi) / 72.0
    scale = max(scale_by_width, scale_by_dpi)

    scale = min(scale, float(max_dpi) / 72.0)
    scale = min(scale, float(WEBP_MAX_DIM) / float(content_w_units))

    if force_single_image:
        scale = min(scale, float(WEBP_MAX_DIM) / float(h_units))

    return max(scale, 0.10)


def render_pdf_to_pages(
    chapter,
    pdf_path: str,
    *,
    dpi: int = 144,
    max_width: int = 1400,
    replace_existing: bool = True,
    quality: int = 82,
    webp_method: int = 4,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    split_long_pages: bool = False,
) -> Tuple[int, int]:
    """
    PDF -> WEBP.

    split_long_pages=False (default):
      ✅ 1 PDF sahifa = 1 WEBP (ketma-ketlik ideal)
      ✅ Juda uzun sahifada scale pasayadi, lekin bitta rasm bo‘lib qoladi

    split_long_pages=True:
      Uzun sahifalar CHUNK_HEIGHT bo‘yicha bo‘linadi (tepdan pastga).
      (Bu rejimda 1 PDF sahifa bir nechta WEBP bo‘lib ketadi.)

    progress_cb(done, total):
      done = yaratilgan WEBP soni
      total = chiqishi kutilayotgan WEBP soni
    """
    min_dpi = max(int(dpi or MIN_DPI), MIN_DPI)
    max_dpi = int(MAX_DPI)
    target_w = int(max_width or 1400)
    quality = int(quality or 82)
    webp_method = int(webp_method if webp_method is not None else 4)

    # page_number start
    if replace_existing:
        Page.objects.filter(chapter=chapter).delete()
        out_no = 0
    else:
        out_no = (
            Page.objects.filter(chapter=chapter)
            .aggregate(Max("page_number"))["page_number__max"]
            or 0
        )

    pdf = pdfium.PdfDocument(pdf_path)
    created = 0

    plan: List[Dict[str, Any]] = []
    total_outputs = 0

    try:
        page_count = len(pdf)

        # ---------- 1-pass: plan + total estimate ----------
        for i in range(page_count):
            page = pdf[i]
            try:
                try:
                    w_units, h_units = page.get_size()
                except Exception:
                    w_units, h_units = 595.0, 842.0

                crop_l, crop_r = _detect_content_crop_units(page, w_units, h_units)
                content_w_units = max(1.0, float(w_units) - float(crop_l) - float(crop_r))

                scale = _pick_scale(
                    content_w_units=content_w_units,
                    h_units=h_units,
                    target_w_px=target_w,
                    min_dpi=min_dpi,
                    max_dpi=max_dpi,
                    force_single_image=(not split_long_pages),
                )

                predicted_h_px = float(h_units) * float(scale)
                pieces = (
                    max(1, int(math.ceil(predicted_h_px / float(CHUNK_HEIGHT))))
                    if split_long_pages
                    else 1
                )

                plan.append(
                    {
                        "w_units": float(w_units),
                        "h_units": float(h_units),
                        "crop_l": float(crop_l),
                        "crop_r": float(crop_r),
                        "scale": float(scale),
                        "pieces": int(pieces),
                    }
                )
                total_outputs += int(pieces)

            finally:
                _safe_close(page)

        if progress_cb:
            progress_cb(0, total_outputs)

        # ---------- 2-pass: render ----------
        for i, meta in enumerate(plan):
            page = pdf[i]
            try:
                h_units = float(meta["h_units"])
                crop_l = float(meta["crop_l"])
                crop_r = float(meta["crop_r"])
                scale = float(meta["scale"])
                pieces = int(meta["pieces"])

                if pieces <= 1:
                    # 1 PDF sahifa = 1 WEBP
                    # pdfium crop: (left, bottom, right, top) — bu marginlar (unitda)
                    crop = (crop_l, 0.0, crop_r, 0.0)

                    bmp = page.render(scale=scale, crop=crop)
                    try:
                        img = bmp.to_pil().convert("RGB")

                        # safety: limitdan oshsa kichraytirish
                        if img.width > WEBP_MAX_DIM or img.height > WEBP_MAX_DIM:
                            ratio = min(WEBP_MAX_DIM / img.width, WEBP_MAX_DIM / img.height)
                            img = img.resize(
                                (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
                                Image.LANCZOS,
                            )

                        buf = BytesIO()
                        img.save(buf, format="WEBP", quality=quality, method=webp_method)
                        buf.seek(0)

                        out_no += 1
                        fname = (
                            f"chapters/{chapter.manga.slug}/v{chapter.volume}/"
                            f"ch{chapter.chapter_number}/{out_no:03d}.webp"
                        )

                        page_obj = Page(chapter=chapter, page_number=out_no)
                        page_obj.image.save(fname, ContentFile(buf.read()), save=True)

                        created += 1
                        if progress_cb:
                            progress_cb(created, total_outputs)

                    finally:
                        _safe_close(bmp)

                else:
                    # Chunk mode: tepdan pastga
                    chunk_units = float(CHUNK_HEIGHT) / float(scale)
                    slice_top = float(h_units)

                    while slice_top > 0:
                        slice_bottom = max(0.0, slice_top - chunk_units)

                        # Qolsin: [slice_bottom .. slice_top]
                        top_cut = float(h_units) - float(slice_top)   # tepadan kesiladigan
                        bottom_cut = float(slice_bottom)              # pastdan kesiladigan

                        # ✅ pdfium crop: (left, bottom, right, top)
                        crop = (crop_l, bottom_cut, crop_r, top_cut)

                        bmp = page.render(scale=scale, crop=crop)
                        try:
                            img = bmp.to_pil().convert("RGB")

                            if img.width > WEBP_MAX_DIM or img.height > WEBP_MAX_DIM:
                                ratio = min(WEBP_MAX_DIM / img.width, WEBP_MAX_DIM / img.height)
                                img = img.resize(
                                    (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
                                    Image.LANCZOS,
                                )

                            buf = BytesIO()
                            img.save(buf, format="WEBP", quality=quality, method=webp_method)
                            buf.seek(0)

                            out_no += 1
                            fname = (
                                f"chapters/{chapter.manga.slug}/v{chapter.volume}/"
                                f"ch{chapter.chapter_number}/{out_no:03d}.webp"
                            )

                            page_obj = Page(chapter=chapter, page_number=out_no)
                            page_obj.image.save(fname, ContentFile(buf.read()), save=True)

                            created += 1
                            if progress_cb:
                                progress_cb(created, total_outputs)

                        finally:
                            _safe_close(bmp)

                        slice_top = slice_bottom

            finally:
                _safe_close(page)

        if progress_cb:
            progress_cb(total_outputs, total_outputs)

        return created, total_outputs

    finally:
        _safe_close(pdf)
