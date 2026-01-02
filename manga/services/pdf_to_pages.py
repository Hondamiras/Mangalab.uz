from io import BytesIO

import pypdfium2 as pdfium
from PIL import Image

from django.core.files.base import ContentFile
from django.db.models import Max

from ..models import Page


WEBP_MAX_DIM = 16383          # WebP limit
CHUNK_HEIGHT = 12000          # split bo‘lagi (<= 16383 bo‘lishi shart)
HARD_RENDER_MAX_H = 24000     # RAMni himoya qilish uchun (juda uzun bo‘lsa render scale tushadi)


def render_pdf_to_pages(
    chapter,
    pdf_path: str,
    *,
    dpi: int = 144,
    max_width: int = 1400,
    replace_existing: bool = True,
    quality: int = 82,
) -> int:
    """
    PDF -> WEBP Page'lar yaratadi.
    Juda uzun sahifalar WebP limit (16383px) sababli avtomatik bo‘lib saqlanadi.
    Returns: yaratilgan Page soni
    """

    if replace_existing:
        Page.objects.filter(chapter=chapter).delete()

    pdf = pdfium.PdfDocument(pdf_path)

    existing_max = (
        Page.objects.filter(chapter=chapter)
        .aggregate(Max("page_number"))["page_number__max"]
        or 0
    )
    page_no = existing_max
    created = 0

    for i in range(len(pdf)):
        page = pdf[i]

        # --- 1) sahifa o‘lchamini olib, scale hisoblaymiz (render juda katta bo‘lib ketmasin)
        try:
            w_pt, h_pt = page.get_size()  # points
        except Exception:
            # fallback: dpi bo‘yicha
            w_pt, h_pt = 595.0, 842.0  # A4 approx

        scale_dpi = dpi / 72.0
        scale = scale_dpi

        # max_width bo‘yicha scale (eni 1400px atrofida bo‘lsin)
        if max_width and w_pt > 0:
            scale = min(scale, max_width / float(w_pt))

        # RAM himoya: juda uzun bo‘lsa render height ni cheklab scale tushiramiz
        if h_pt > 0:
            predicted_h = float(h_pt) * float(scale)
            if predicted_h > HARD_RENDER_MAX_H:
                scale = HARD_RENDER_MAX_H / float(h_pt)

        # --- 2) Render
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil().convert("RGB")

        # eni max_width dan oshsa kichraytiramiz (odatda oshmaydi)
        if max_width and pil.width > max_width:
            new_h = int(pil.height * (max_width / pil.width))
            pil = pil.resize((max_width, new_h), Image.LANCZOS)

        # WebP eni limitdan oshib qolsa (kamdan-kam), uni ham tushiramiz
        if pil.width > WEBP_MAX_DIM:
            new_h = int(pil.height * (WEBP_MAX_DIM / pil.width))
            pil = pil.resize((WEBP_MAX_DIM, new_h), Image.LANCZOS)

        # --- 3) Agar balandligi WebP limitdan oshsa: bo‘lib saqlaymiz
        y = 0
        while y < pil.height:
            piece = pil.crop((0, y, pil.width, min(y + CHUNK_HEIGHT, pil.height)))

            # xavfsizlik: chunk ham limitdan oshmasin
            if piece.height > WEBP_MAX_DIM:
                # CHUNK_HEIGHT noto‘g‘ri bo‘lsa
                piece = piece.crop((0, 0, piece.width, WEBP_MAX_DIM))

            page_no += 1
            buf = BytesIO()
            piece.save(buf, format="WEBP", quality=quality, method=6)
            buf.seek(0)

            fname = (
                f"chapters/{chapter.manga.slug}/v{chapter.volume}/"
                f"ch{chapter.chapter_number}/{page_no:03d}.webp"
            )

            page_obj = Page(chapter=chapter, page_number=page_no)
            page_obj.image.save(fname, ContentFile(buf.read()), save=True)

            created += 1
            y += CHUNK_HEIGHT

        # --- resurslarni bo‘shatish
        try:
            bitmap.close()
        except Exception:
            pass
        try:
            page.close()
        except Exception:
            pass

    try:
        pdf.close()
    except Exception:
        pass

    return created
