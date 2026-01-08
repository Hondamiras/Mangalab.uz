# manga/admin.py
import re
from django.utils import timezone
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db.models import Max, Q
from django.shortcuts import render, redirect
from django.urls import path, reverse
from django.utils.html import format_html
import tempfile
from django.db import transaction

from manga.services.pdf_to_pages import render_pdf_to_pages

from .forms import ChapterPDFUploadForm, MultiPageUploadForm, ChapterAdminForm
from .models import (
    ChapterPDFJob,
    ChapterPurchase,
    MangaTelegramLink,
    MangaTitle,
    Tag,
    Genre,
    Manga,
    Chapter,
    Page,
)

# ===== Global Admin Settings =====
admin.site.site_header = "MangaLab Admin"
admin.site.site_title = "MangaLab Admin Panel"
admin.site.index_title = "MangaLab Admin Paneliga xush kelibsiz!"


# ===== Universal Mixin =====
class OwnMixin:
    def save_model(self, request, obj, form, change):
        # created_by maydoni bor bo'lsa va yangi bo'lsa yozib qo'yamiz
        if hasattr(obj, "created_by") and (not change or not obj.pk):
            if not getattr(obj, "created_by", None):
                obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Faqat created_by maydoni bor modellarni cheklash
        if hasattr(self.model, "created_by"):
            return qs.filter(created_by=request.user)
        return qs   # created_by yo'q bo'lsa, cheklov qo'ymaymiz

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        if hasattr(obj, "created_by"):
            return getattr(obj, "created_by", None) == request.user
        return True  # created_by yo'q bo'lsa cheklov qo'ymaymiz

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        if hasattr(obj, "created_by"):
            return getattr(obj, "created_by", None) == request.user
        return True


# ===== Taxonomies =====
@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


class MangaTelegramLinkInline(admin.TabularInline):
    model = MangaTelegramLink
    extra = 1   # yangi qo‘shish uchun bo‘sh qator
    min_num = 0


class MangaTitleInline(admin.TabularInline):
    model = MangaTitle
    extra = 1


# ===== Manga =====
@admin.register(Manga)
class MangaAdmin(OwnMixin, admin.ModelAdmin):
    # Asosiy konfiguratsiya
    list_display = ("title", "team", "status", "created_by", "translator_list", "chapter_count")
    list_filter = ("status", "type", "translation_status", "team", "translators")
    search_fields = ("title",)
    search_help_text = "Manga nomi bo‘yicha qidirish"
    prepopulated_fields = {"slug": ("title",)}
    inlines = [MangaTelegramLinkInline, MangaTitleInline]

    # M2M larni yon panel bilan qulayroq tanlash
    filter_horizontal = ("genres", "tags", "translators")

    # --- Helpers ---
    def _is_translator(self, user) -> bool:
        prof = getattr(user, "userprofile", None)
        return bool(prof and getattr(prof, "is_translator", False))

    # --- Changelist ustunlari dinamik ---
    def get_list_display(self, request):
        """
        Superuser: title, team, status, created_by, tarjimonlar, boblar soni
        Oddiy staff/tarjimon: created_by ni yashiramiz
        """
        if request.user.is_superuser:
            return ("title", "team", "status", "created_by", "translator_list", "chapter_count")
        return ("title", "team", "status", "translator_list", "chapter_count")

    # --- Form maydonlarini dinamik boshqarish ---
    def get_form(self, request, obj=None, **kwargs):
        """
        Tarjimonlar uchun: slug va created_by formdan chiqarib tashlaymiz.
        """
        if not request.user.is_superuser and self._is_translator(request.user):
            exclude = list(kwargs.get("exclude", []))
            for f in ("slug", "created_by"):
                if f not in exclude:
                    exclude.append(f)
            kwargs["exclude"] = exclude
        return super().get_form(request, obj, **kwargs)

    # prepopulated_fields'ni tarjimonlar uchun o‘chirib qo‘yamiz
    def get_prepopulated_fields(self, request, obj=None):
        if not request.user.is_superuser and self._is_translator(request.user):
            return {}
        return super().get_prepopulated_fields(request, obj)

    # ManyToMany fieldlar: genres, tags, translators
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        # Janr va teglar – oddiy queryset
        if db_field.name in ["genres", "tags"]:
            kwargs["queryset"] = db_field.related_model.objects.all()

        # Tarjimonlar – faqat is_translator=True bo'lgan profillar
        if db_field.name == "translators":
            from accounts.models import UserProfile
            kwargs["queryset"] = (
                UserProfile.objects
                .filter(is_translator=True)
                .select_related("user")
                .order_by("user__username")
            )
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    # Changelistda boblar soni
    def chapter_count(self, obj):
        return obj.chapters.count()
    chapter_count.short_description = "Boblar"

    # Changelistda tarjimonlarni chiroyli ko‘rsatish
    def translator_list(self, obj):
        qs = obj.translators.select_related("user")
        total = qs.count()
        names = [p.user.username for p in qs[:3]]
        if not names:
            return "—"
        label = ", ".join(names)
        extra = total - len(names)
        if extra > 0:
            label += f" +{extra}"
        return label
    translator_list.short_description = "Tarjimonlar"

    # created_by ni xavfsiz o‘rnatish:
    def save_model(self, request, obj, form, change):
        """
        Superuserdan boshqa hech kim created_by ni o‘zgartira olmaydi.
        Tarjimon / oddiy staff uchun created_by = current user.
        """
        if not request.user.is_superuser:
            obj.created_by = request.user
        else:
            # Superuserga erkinlik — created_by bo'sh bo'lsa o'zi bo'ladi
            if not obj.created_by_id:
                obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        created_by uchun faqat tarjimon bo‘lgan userlar chiqsin (admin formda).
        """
        if db_field.name == "created_by":
            UserModel = get_user_model()
            kwargs["queryset"] = UserModel.objects.filter(userprofile__is_translator=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

from django.db.models import Q, Max, OuterRef, Subquery
# ===== Chapter =====
@admin.register(Chapter)
class ChapterAdmin(OwnMixin, admin.ModelAdmin):
    form = ChapterAdminForm

    # ⚠️ list_display baribir get_list_display bilan override bo‘ladi,
    # lekin qoldirsangiz ham mayli (asosiy sozlash get_list_display’da)
    list_display = (
        "manga",
        "volume",
        "chapter_number",
        "price_tanga",
        "page_count",
        "upload_pdf_link",
        "pdf_status",
        "upload_pages_link",
    )

    search_fields = (
        "manga__title",
        "manga__titles__name",
        "manga__slug",
    )
    search_help_text = "Manga nomi / qo‘shimcha nomlari / slug bo‘yicha qidiring."
    list_per_page = 40
    list_editable = ("volume", "price_tanga")

    # ================== QUERYSET (✅ tez + pdf_status uchun annotate) ==================
    def get_queryset(self, request):
        qs = super().get_queryset(request)

        if not request.user.is_superuser:
            qs = qs.filter(
                Q(manga__created_by=request.user) |
                Q(manga__translators__user=request.user)
            ).distinct()

        # ✅ pdf_status uchun (N+1 bo‘lmasin): latest job fieldlarini annotate qilamiz
        try:
            latest_job = ChapterPDFJob.objects.filter(chapter=OuterRef("pk")).order_by("-id")
            qs = qs.annotate(
                _pdf_job_status=Subquery(latest_job.values("status")[:1]),
                _pdf_job_progress=Subquery(latest_job.values("progress")[:1]),
                _pdf_job_total=Subquery(latest_job.values("total")[:1]),
            )
        except Exception:
            # ChapterPDFJob yo‘q bo‘lsa / import bo‘lmasa — admin yiqilmasin
            pass

        return qs

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser and "thanks" in form.base_fields:
            form.base_fields.pop("thanks")
        return form

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return ("manga", "volume")
        return ()

    # ================== USTUNLAR ==================
    def page_count(self, obj):
        return obj.pages.count()
    page_count.short_description = "Sahifalar soni"

    def pdf_status(self, obj):
        """
        ✅ listda job status/progress ko‘rsatadi.
        Annotate bo‘lsa — query qilmaydi.
        """
        status = getattr(obj, "_pdf_job_status", None)
        prog = getattr(obj, "_pdf_job_progress", None)
        total = getattr(obj, "_pdf_job_total", None)

        if not status:
            # fallback: annotate ishlamasa
            try:
                job = obj.pdf_jobs.order_by("-id").first()
                if not job:
                    return "—"
                status = job.status
                prog = job.progress
                total = job.total
            except Exception:
                return "—"

        # chiroyli ko‘rinish (xohlasangiz olib tashlang)
        badge = {
            "PENDING":  ("⏳ PENDING",  "#999"),
            "PROCESSING": ("⚙️ PROCESSING", "#2d7"),
            "DONE":     ("✅ DONE",     "#4caf50"),
            "FAILED":   ("❌ FAILED",   "#e53935"),
        }.get(str(status), (str(status), "#888"))

        label, color = badge
        if total:
            text = f"{label} ({prog}/{total})"
        else:
            text = f"{label}"

        return format_html('<span style="font-weight:600;color:{}">{}</span>', color, text)

    pdf_status.short_description = "PDF Status"

    def get_list_display(self, request):
        """
        ✅ Siz xohlagan tartib:
        PDF yuklash tugmasi -> PDF status -> Bulk upload tugmasi
        """
        base = [
            "manga",
            "volume",
            "chapter_number",
            "price_tanga",
            "page_count",
            "upload_pdf_link",
            "pdf_status",
            "upload_pages_link",
        ]
        if request.user.is_superuser:
            return base + ["release_date"]
        return base

    def get_exclude(self, request, obj=None):
        if not request.user.is_superuser:
            return ["release_date"]
        return []

    # ================== LINKLAR ==================
    def upload_pages_link(self, obj):
        url = reverse("admin:chapter_upload_pages", args=[obj.pk])
        return format_html('<a class="button" href="{}">📤 Sahifalarni yuklash</a>', url)
    upload_pages_link.short_description = "Bulk Upload"

    def upload_pdf_link(self, obj):
        url = reverse("admin:chapter_upload_pdf", args=[obj.pk])
        return format_html('<a class="button" href="{}">📄 PDF yuklash</a>', url)
    upload_pdf_link.short_description = "PDF Upload"

    # ================== URLS ==================
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("<int:chapter_id>/upload_pages/", self.admin_site.admin_view(self.upload_pages_view), name="chapter_upload_pages"),
            path("<int:chapter_id>/upload_pdf/", self.admin_site.admin_view(self.upload_pdf_view), name="chapter_upload_pdf"),
        ]
        return custom_urls + urls

    # ================== BULK UPLOAD VIEW (tegmadim) ==================
    def upload_pages_view(self, request, chapter_id):
        # ... sizning mavjud kodingiz o‘z holicha qoladi ...
        chapter = Chapter.objects.select_related("manga").filter(pk=chapter_id).first()
        if not chapter:
            messages.error(request, "Bunday bob topilmadi.")
            return redirect("admin:manga_chapter_changelist")

        if not request.user.is_superuser:
            can_edit = (
                chapter.manga.created_by_id == request.user.id
                or chapter.manga.translators.filter(user=request.user).exists()
            )
            if not can_edit:
                messages.error(request, "Bu bob uchun sahifa yuklash huquqingiz yo'q.")
                return redirect("admin:manga_chapter_changelist")

        if request.method == "POST":
            form = MultiPageUploadForm(request.POST, request.FILES)
            if form.is_valid():
                import re

                def extract_number(filename: str) -> int:
                    m = re.search(r"(\d+)", filename)
                    return int(m.group(1)) if m else 0

                files = form.cleaned_data["images"]
                if not isinstance(files, (list, tuple)):
                    files = [files]
                files = sorted(files, key=lambda f: extract_number(f.name))

                existing_max = (
                    Page.objects.filter(chapter=chapter)
                    .aggregate(Max("page_number"))["page_number__max"]
                    or 0
                )

                new_pages = []
                for index, f in enumerate(files):
                    new_pages.append(Page(chapter=chapter, image=f, page_number=existing_max + index + 1))

                Page.objects.bulk_create(new_pages)
                messages.success(request, f"{len(files)} ta sahifa yuklandi!")
                return redirect("admin:manga_chapter_changelist")
        else:
            form = MultiPageUploadForm()

        return render(request, "admin/bulk_upload.html", {"form": form, "chapter": chapter})

    # ================== PDF UPLOAD VIEW (✅ navbatga qo‘yadi, listga qaytaradi) ==================
    def upload_pdf_view(self, request, chapter_id):
        chapter = Chapter.objects.select_related("manga").filter(pk=chapter_id).first()
        if not chapter:
            messages.error(request, "Bunday bob topilmadi.")
            return redirect("admin:manga_chapter_changelist")

        if not request.user.is_superuser:
            can_edit = (
                chapter.manga.created_by_id == request.user.id
                or chapter.manga.translators.filter(user=request.user).exists()
            )
            if not can_edit:
                messages.error(request, "Bu bob uchun PDF yuklash huquqingiz yo'q.")
                return redirect("admin:manga_chapter_changelist")

        if request.method == "POST":
            form = ChapterPDFUploadForm(request.POST, request.FILES)
            if form.is_valid():
                f = form.cleaned_data["pdf"]

                # PDF header tez tekshiruv
                try:
                    head = f.read(4)
                    f.seek(0)
                    if head != b"%PDF":
                        messages.error(request, "Bu fayl PDFga o‘xshamaydi (header %PDF emas).")
                        return redirect("admin:manga_chapter_changelist")
                except Exception:
                    pass

                # 1 ta chapterga 1 ta active job
                if ChapterPDFJob.objects.filter(chapter=chapter, status__in=["PENDING", "PROCESSING"]).exists():
                    messages.warning(request, "Bu bob uchun PDF allaqachon navbatda yoki ishlovda.")
                    return redirect("admin:manga_chapter_changelist")

                # ✅ Model fieldlari har xil bo‘lsa ham yiqilmasin
                allowed = {fld.name for fld in ChapterPDFJob._meta.fields}
                create_kwargs = {
                    "chapter": chapter,
                    "pdf": f,
                    "status": "PENDING",
                    "progress": 0,
                    "total": 0,
                    "replace_existing": form.cleaned_data.get("replace_existing", True),
                    "dpi": form.cleaned_data.get("dpi") or 144,
                    "max_width": form.cleaned_data.get("max_width") or 1400,
                    "quality": 82,
                    "created_by": request.user,
                    "created_at": timezone.now(),
                }
                create_kwargs = {k: v for k, v in create_kwargs.items() if k in allowed}

                job = ChapterPDFJob.objects.create(**create_kwargs)

                messages.success(request, "PDF qabul qilindi ✅ Navbatga qo‘yildi. Konvert fon rejimida ishlaydi.")

                # ✅ “PDF navbatga qo‘yildi” bo‘lsa RO‘YXATGA qaytarsin
                # Agar xohlasangiz next param orqali filtr/searchni ham saqlab qaytarasiz:
                next_url = request.POST.get("next") or request.GET.get("next")
                if next_url:
                    return redirect(next_url)

                return redirect("admin:manga_chapter_changelist")
        else:
            form = ChapterPDFUploadForm()

        return render(request, "admin/upload_pdf.html", {"form": form, "chapter": chapter})

# ===== Page =====
class IsWebPFilter(admin.SimpleListFilter):
    title = "WebP"
    parameter_name = "is_webp"

    def lookups(self, request, model_admin):
        return (
            ("yes", "WebP"),
            ("no", "JPEG/PNG"),
        )

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(image__iendswith=".webp")
        if self.value() == "no":
            return queryset.exclude(image__iendswith=".webp")
        return queryset


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("chapter", "page_number", "image_size_mb")
    raw_id_fields = ("chapter",)
    ordering = ("-chapter__id", "-page_number")
    list_filter = (IsWebPFilter,)

    search_fields = (
        "chapter__manga__title",          # manga nomi
    )
    search_help_text = "Manga nomi bo‘yicha qidiring."

    def get_queryset(self, request):
        qs = (
            super()
            .get_queryset(request)
            .select_related("chapter", "chapter__manga")
        )
        if request.user.is_superuser:
            return qs
        # Muallif YOKI tarjimon bo‘lgan mangalar sahifalarinigina ko'rsatamiz
        return qs.filter(
            Q(chapter__manga__created_by=request.user) |
            Q(chapter__manga__translators__user=request.user)
        ).distinct()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "chapter" and not request.user.is_superuser:
            kwargs["queryset"] = Chapter.objects.filter(
                Q(manga__created_by=request.user) |
                Q(manga__translators__user=request.user)
            ).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Image Size (MB)")
    def image_size_mb(self, obj):
        f = getattr(obj, "image", None)
        if not f:
            return "No file"

        try:
            if not default_storage.exists(f.name):
                return "No file"
            size_bytes = f.size
        except Exception:
            try:
                size_bytes = default_storage.size(f.name)
            except Exception:
                return "N/A"

        return f"{size_bytes / (1024 * 1024):.2f} MB"


# ===== ChapterPurchase =====
@admin.register(ChapterPurchase)
class ChapterPurchaseAdmin(admin.ModelAdmin):
    list_display = ("user", "chapter", "translator", "price_tanga")
    list_filter = ("chapter__manga__created_by",)
    search_help_text = "Tarjimon nomi bo‘yicha qidirish"
    search_fields = ("chapter__manga__title", "user__username")

    def translator(self, obj):
        """
        Avval Manga.translators dan ko‘rsatamiz.
        Agar bo‘sh bo‘lsa, fallback sifatida created_by.
        """
        manga = obj.chapter.manga
        qs = manga.translators.select_related("user")
        names = [p.user.username for p in qs[:3]]
        if names:
            label = ", ".join(names)
            extra = qs.count() - len(names)
            if extra > 0:
                label += f" +{extra}"
            return label
        if manga.created_by_id:
            return manga.created_by.username
        return "—"

    translator.short_description = "Tarjimon(lar)"

    def price_tanga(self, obj):
        return obj.chapter.price_tanga
    price_tanga.short_description = "Tanga"
