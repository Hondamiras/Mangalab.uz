# manga/admin.py
import re

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db.models import Max, Q
from django.shortcuts import render, redirect
from django.urls import path, reverse
from django.utils.html import format_html

from .forms import MultiPageUploadForm, ChapterAdminForm
from .models import (
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


# ===== Chapter =====
@admin.register(Chapter)
class ChapterAdmin(OwnMixin, admin.ModelAdmin):
    form = ChapterAdminForm

    list_display = (
        "manga",
        "volume",
        "chapter_number",
        "price_tanga",
        "page_count",
        "upload_pages_link",  # 📤 Tugma bob ro‘yxatida
    )
    search_fields = ("manga__title",)
    search_help_text = "Manga nomi bo‘yicha qidirish"
    list_per_page = 40
    list_editable = ("volume", "price_tanga")

    # ================== QUERYSET & FORM KO‘RINISHI ==================
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Faqat o‘ziga tegishli yoki tarjimon bo‘lgan mangalarning boblari
        return qs.filter(
            Q(manga__created_by=request.user) |
            Q(manga__translators__user=request.user)
        ).distinct()

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Agar superuser bo‘lmasa thanks maydonini olib tashlaymiz
        if not request.user.is_superuser and "thanks" in form.base_fields:
            form.base_fields.pop("thanks")
        return form

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return ("manga",)
        return ()

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        last_chapter = Chapter.objects.order_by("-id").first()
        if last_chapter:
            initial["manga"] = last_chapter.manga_id
            initial["chapter_number"] = last_chapter.chapter_number + 1
            initial["volume"] = last_chapter.volume
        return initial

    # ================== KO‘RINADIGAN USTUNLAR ==================
    def page_count(self, obj):
        return obj.pages.count()
    page_count.short_description = "Sahifalar soni"

    def get_list_display(self, request):
        """
        Agar superuser bo'lsa, release_date ustunini ham qo‘shamiz.
        """
        base = [
            "manga",
            "volume",
            "chapter_number",
            "price_tanga",
            "page_count",
            "upload_pages_link",
        ]
        if request.user.is_superuser:
            return base + ["release_date"]
        return base

    def get_exclude(self, request, obj=None):
        """
        Formda ham non-superuserlardan release_date maydonini yashiramiz.
        """
        if not request.user.is_superuser:
            return ["release_date"]
        return []

    # ================== PERMISSION HELPERLAR ==================
    def _can_edit_chapter(self, request, obj):
        if request.user.is_superuser or obj is None:
            return True
        manga = obj.manga
        if manga.created_by_id == request.user.id:
            return True
        return manga.translators.filter(user=request.user).exists()

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None:
            return True
        return self._can_edit_chapter(request, obj)

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None:
            return True
        return self._can_edit_chapter(request, obj)

    # ================== BULK BOB YARATISH ==================
    def save_model(self, request, obj, form, change):
        """
        change=True yoki bulk_total <=1 bo‘lsa – oddiy saqlash.
        bulk_total >1 bo‘lsa – tanlangan Manga+Jild uchun mavjud eng katta
        chapter_number dan boshlab ketma-ket bulk_total ta bob yaratadi,
        birinchisi sifatida esa aynan obj'ni saqlaymiz.
        """
        bulk_total = form.cleaned_data.get("bulk_total") or 1

        # Tahrirlash yoki oddiy bitta bob
        if change or bulk_total <= 1:
            super().save_model(request, obj, form, change)
            return

        manga = form.cleaned_data["manga"]
        volume = form.cleaned_data["volume"]
        price_tanga = form.cleaned_data["price_tanga"]
        release_date = form.cleaned_data["release_date"]

        # Tanlangan manga + jild bo‘yicha eng oxirgi bob raqami
        last_num = (
            Chapter.objects.filter(manga=manga, volume=volume)
            .aggregate(Max("chapter_number"))["chapter_number__max"]
            or 0
        )
        start = last_num + 1

        # Obj'ni birinchi bob sifatida saqlaymiz
        obj.manga = manga
        obj.volume = volume
        obj.chapter_number = start
        obj.price_tanga = price_tanga
        obj.release_date = release_date

        super().save_model(request, obj, form, change=False)

        # Qolgan boblarni bulk_create bilan qo‘shamiz
        new_chapters = []
        for i in range(1, bulk_total):
            new_chapters.append(
                Chapter(
                    manga=manga,
                    volume=volume,
                    chapter_number=start + i,
                    price_tanga=price_tanga,
                    release_date=release_date,
                )
            )

        if new_chapters:
            Chapter.objects.bulk_create(new_chapters)

        self.message_user(
            request,
            f"{bulk_total} ta bob ({manga.title}, jild {volume}) muvaffaqiyatli yaratildi.",
            level=messages.SUCCESS,
        )

    # ================== BULK UPLOAD TUGMASI ==================
    def upload_pages_link(self, obj):
        url = reverse("admin:chapter_upload_pages", args=[obj.pk])
        return format_html('<a class="button" href="{}">📤 Sahifalarni yuklash</a>', url)
    upload_pages_link.short_description = "Bulk Upload"

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if extra_context is None:
            extra_context = {}

        upload_url = reverse("admin:chapter_upload_pages", args=[object_id])
        extra_context["upload_pages_button"] = format_html(
            """
            <div style="margin: 10px 0 20px 0;">
                <a href="{}" class="button" style="
                    background-color: #2e8540;
                    color: white;
                    padding: 6px 12px;
                    border-radius: 5px;
                    text-decoration: none;
                    font-weight: bold;
                ">
                    📤 Sahifalarni yuklash
                </a>
            </div>
            """,
            upload_url,
        )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    # ================== CUSTOM URL & VIEW (MULTI UPLOAD) ==================
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:chapter_id>/upload_pages/",
                self.admin_site.admin_view(self.upload_pages_view),
                name="chapter_upload_pages",
            ),
        ]
        return custom_urls + urls

    def upload_pages_view(self, request, chapter_id):
        """
        Bitta bob uchun bir nechta sahifa (rasm)larni yuklash.
        """
        chapter = (
            Chapter.objects.select_related("manga")
            .filter(pk=chapter_id)
            .first()
        )
        if not chapter:
            messages.error(request, "Bunday bob topilmadi.")
            return redirect("admin:manga_chapter_changelist")

        # Huquq tekshirish: superuser / created_by / translators
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

                def extract_number(filename: str) -> int:
                    m = re.search(r"(\d+)", filename)
                    return int(m.group(1)) if m else 0

                files = form.cleaned_data["images"]
                # MultipleFileField: agar bitta fayl bo'lsa -> obyekt,
                # ko'p bo'lsa -> list. Biz hammasini listga aylantirib olamiz.
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
                    new_pages.append(
                        Page(
                            chapter=chapter,
                            image=f,
                            page_number=existing_max + index + 1,
                        )
                    )

                Page.objects.bulk_create(new_pages)
                messages.success(request, f"{len(files)} ta sahifa yuklandi!")
                return redirect("admin:manga_chapter_changelist")
        else:
            form = MultiPageUploadForm()

        return render(
            request,
            "admin/bulk_upload.html",
            {
                "form": form,
                "chapter": chapter,
            },
        )
    # ================== FOREIGNKEY FILTERLARI ==================
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "manga" and not request.user.is_superuser:
            # Faqat user bog‘langan mangalar:
            kwargs["queryset"] = Manga.objects.filter(
                Q(created_by=request.user) |
                Q(translators__user=request.user)
            ).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


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
