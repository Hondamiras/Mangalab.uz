import os
import re
from django.contrib import admin, messages
from django.db.models import Max
from django.shortcuts import render, redirect
from django.urls import path, reverse
from django.utils.html import format_html
from .models import ChapterPurchase, MangaTelegramLink, Tag, Genre, Manga, Chapter, Page
from .forms import MultiPageUploadForm


# ===== Global Admin Settings =====
admin.site.site_header = "MangaLab Admin"
admin.site.site_title = "MangaLab Admin Panel"
admin.site.index_title = "MangaLab Admin Paneliga xush kelibsiz!"


# ===== Universal Mixin =====
class OwnMixin:
    def save_model(self, request, obj, form, change):
        # created_by maydoni bor bo'lsa va yangi bo'lsa yozib qo'yamiz
        if hasattr(obj, "created_by") and (not change or not obj.pk):
            if not getattr(obj, 'created_by', None):
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

# ===== Manga =====
@admin.register(Manga)
class MangaAdmin(OwnMixin, admin.ModelAdmin):
    list_display = ("title", "team", "status", "created_by", "chapter_count")
    list_filter  = ("team", "type", "status", "translation_status")
    list_editable = ("created_by",)
    search_fields = ("title",)
    search_help_text = "Manga nomi bo‘yicha qidirish"
    list_filter = ("status", "type")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [MangaTelegramLinkInline]

    # --- Helpers ---
    def _is_translator(self, user) -> bool:
        prof = getattr(user, "userprofile", None)
        return bool(prof and getattr(prof, "is_translator", False))

    # --- Changelist ustunlari ---
    def get_list_display(self, request):
        if request.user.is_superuser:
            # Superuser hammasini ko‘radi
            return ("title", "status", "created_by", "chapter_count")
        # Tarjimon va oddiy staff uchun created_by chiqarilmaydi
        return ("title", "status", "chapter_count")

    # --- Form maydonlarini dinamik boshqarish ---
    def get_form(self, request, obj=None, **kwargs):
        # Tarjimon bo‘lsa slug va created_by formdan butunlay chiqariladi
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

    # ManyToMany fieldlar o‘z holicha qolsin
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name in ["genres", "tags"]:
            kwargs["queryset"] = db_field.related_model.objects.all()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    # Changelistda boblar soni
    def chapter_count(self, obj):
        return obj.chapters.count()
    chapter_count.short_description = "Chapters"

    # created_by ni xavfsiz o‘rnatish:
    def save_model(self, request, obj, form, change):
        # Superuserdan boshqa (jumladan tarjimon) hech kim created_by ni o‘zgartira olmaydi
        if not request.user.is_superuser:
            obj.created_by = getattr(obj, "created_by", None) or request.user
            # Agar mavjud bo‘lsa ham, majburan o‘zingizga tenglab qo‘yish xavfsizroq:
            obj.created_by = request.user
        else:
            # Superuserga erkinlik
            if not obj.created_by_id:
                obj.created_by = request.user
        super().save_model(request, obj, form, change)

        
@admin.register(Chapter)
class ChapterAdmin(OwnMixin, admin.ModelAdmin):
    list_display = (
        "manga", "volume", "chapter_number", 'price_tanga', "page_count",  
        "upload_pages_link",  # 📤 Tugma bob ro‘yxatida
    )
    search_fields = ("manga__title",)
    search_help_text = "Manga nomi bo‘yicha qidirish"
    list_per_page = 40
    list_editable = ('volume', 'price_tanga')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Faqat o‘z manga muallifligi bo‘yicha boblar:
        return qs.filter(manga__created_by=request.user)

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
        last_chapter = Chapter.objects.order_by('-id').first()
        if last_chapter:
            initial['manga'] = last_chapter.manga_id
            initial['chapter_number'] = last_chapter.chapter_number + 1
            initial['volume'] = last_chapter.volume
        return initial

    def page_count(self, obj):
        return obj.pages.count()
    page_count.short_description = "Sahifalar soni"

    def get_list_display(self, request):
        """
        Agar superuser bo'lsa, created_by va release_date ustunlarini
        qo'shamiz; aks holda faqat bazaviy ustunlar.
        """
        base = [
            "manga", "volume", "chapter_number", "price_tanga",
            "page_count", "upload_pages_link"
        ]
        if request.user.is_superuser:
            # 'published_date' — modelingizdagi chiqqan sana maydoni
            return base + ["release_date"]
        return base

    def get_exclude(self, request, obj=None):
        """
        Formda ham non-superuserlardan created_by va published_date
        maydonlarini yashiramiz.
        """
        if not request.user.is_superuser:
            return ["release_date"]
        return []

    def has_change_permission(self, request, obj=None):
        # superuser hamma narsaga ruxsat, boshqalar faqat o‘z boblariga
        if obj and not request.user.is_superuser and obj.manga.created_by != request.user:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and not request.user.is_superuser and obj.manga.created_by != request.user:
            return False
        return super().has_delete_permission(request, obj)
    
    # Tugma ro‘yxatda
    def upload_pages_link(self, obj):
        url = reverse('admin:chapter_upload_pages', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">📤 Sahifalarni yuklash</a>', url
        )
    upload_pages_link.short_description = "Bulk Upload"

    # Tugma tahrirlash sahifasida (change_view)
    def change_view(self, request, object_id, form_url='', extra_context=None):
        if extra_context is None:
            extra_context = {}

        upload_url = reverse('admin:chapter_upload_pages', args=[object_id])
        extra_context['upload_pages_button'] = format_html(
            '''
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
            ''',
            upload_url
        )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    # URL qo‘shish
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:chapter_id>/upload_pages/', self.admin_site.admin_view(self.upload_pages_view), name='chapter_upload_pages'),
        ]
        return custom_urls + urls

    # Yuklash view — filename'larni raqam bo‘yicha sort qiladi!
    def upload_pages_view(self, request, chapter_id):
        chapter = Chapter.objects.filter(pk=chapter_id).first()
        if not chapter:
            messages.error(request, "Bunday bob topilmadi.")
            return redirect('admin:manga_chapter_changelist')

        if request.method == 'POST':
            form = MultiPageUploadForm(request.POST, request.FILES)
            if form.is_valid():
                def extract_number(filename):
                    match = re.search(r'(\d+)', filename)
                    return int(match.group(1)) if match else 0

                files = sorted(
                    request.FILES.getlist('images'),
                    key=lambda f: extract_number(f.name)
                )

                existing_max = Page.objects.filter(chapter=chapter).aggregate(Max('page_number'))['page_number__max'] or 0

                new_pages = []
                for index, f in enumerate(files):
                    new_pages.append(Page(
                        chapter=chapter,
                        image=f,
                        page_number=existing_max + index + 1
                    ))

                Page.objects.bulk_create(new_pages)
                messages.success(request, f"{len(files)} ta sahifa yuklandi!")
                return redirect('admin:manga_chapter_changelist')
        else:
            form = MultiPageUploadForm()

        return render(request, 'admin/bulk_upload.html', {
            'form': form,
            'chapter': chapter,
        })

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "manga" and not request.user.is_superuser:
            kwargs["queryset"] = Manga.objects.filter(created_by=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

# ===== Page =====
class IsWebPFilter(admin.SimpleListFilter):
    title = "WebP"
    parameter_name = "is_webp"

    def lookups(self, request, model_admin):
        return (
            ('yes', 'WebP'),
            ('no', 'JPEG/PNG'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(image__iendswith='.webp')
        if self.value() == 'no':
            return queryset.exclude(image__iendswith='.webp')
        return queryset

from django.core.files.storage import default_storage 
@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("chapter", "page_number", "image_size_mb")
    raw_id_fields = ("chapter",)
    ordering = ("-chapter__id", "-page_number")
    list_filter = (IsWebPFilter,)
    
    search_fields = (
        "chapter__manga__title",          # manga nomi
    )
    search_help_text = (
        "Manga nomi bo‘yicha qidiring."
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("chapter", "chapter__manga")
        if request.user.is_superuser:
            return qs
        # Muallifga tegishli mangalar sahifalarinigina ko'rsatamiz
        return qs.filter(chapter__manga__created_by=request.user)

    @admin.display(description="Image Size (MB)")
    def image_size_mb(self, obj):
        f = getattr(obj, "image", None)
        if not f:
            return "No file"

        # Masofaviy storage bilan xavfsiz tekshiruv va o'lcham
        try:
            if not default_storage.exists(f.name):
                return "No file"
            size_bytes = f.size  # storage.size() chaqiradi; S3/Spaces’da ishlaydi
        except Exception:
            # Zaxira varianti
            try:
                size_bytes = default_storage.size(f.name)
            except Exception:
                return "N/A"

        return f"{size_bytes / (1024 * 1024):.2f} MB"
    
    
class MangaTelegramLinkInline(admin.TabularInline):
    model = MangaTelegramLink
    extra = 1   # yangi qo‘shish uchun bo‘sh qator
    min_num = 0

@admin.register(ChapterPurchase)
class ChapterPurchaseAdmin(admin.ModelAdmin):
    list_display = ('user', 'chapter', 'translator', 'price_tanga')
    list_filter = ('chapter__manga__created_by',)  # filtr – kim tarjimonligini tanlash uchun
    search_help_text = "Tarjimon nomi bo‘yicha qidirish"
    search_fields = ('chapter__manga__title', 'user__username')

    def translator(self, obj):
        return obj.chapter.manga.created_by.username
    translator.short_description = "Tarjimon"

    def price_tanga(self, obj):
        return obj.chapter.price_tanga
    price_tanga.short_description = "Tanga" 
