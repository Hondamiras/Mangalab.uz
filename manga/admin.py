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
    list_display = ("title", "status", "created_by", "chapter_count")
    list_editable = ("created_by",)
    search_fields = ("title",)
    search_help_text = "Manga nomi bo‘yicha qidirish"
    list_filter = ("status", "type")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [MangaTelegramLinkInline]
    
    def chapter_count(self, obj):
        return obj.chapters.count()
    chapter_count.short_description = "Chapters"
    
    def get_list_display(self, request):
        if request.user.is_superuser:
            return super().get_list_display(request)
        return ("title", "status", "chapter_count")
    
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name in ["genres", "tags"]:
            kwargs["queryset"] = db_field.related_model.objects.all()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

# ===== Chapter =====
# @admin.register(Chapter)
# class ChapterAdmin(OwnMixin, admin.ModelAdmin):
#     list_display = (
#         "manga", "volume", "chapter_number",'price_tanga', "page_count",    
#         "upload_pages_link",  # 📤 Tugma bob ro‘yxatida
#     )
#     search_fields = ("manga__title",)
#     search_help_text = "Manga nomi bo‘yicha qidirish"
#     list_per_page = 40
#     list_editable = ('volume', 'price_tanga')

#     def get_changeform_initial_data(self, request):
#         initial = super().get_changeform_initial_data(request)
#         last_chapter = Chapter.objects.order_by('-id').first()
#         if last_chapter:
#             initial['manga'] = last_chapter.manga_id
#             initial['chapter_number'] = last_chapter.chapter_number + 1
#             initial['volume'] = last_chapter.volume
#         return initial

#     def page_count(self, obj):
#         return obj.pages.count()
#     page_count.short_description = "Sahifalar soni"


#     # Tugma ro‘yxatda
#     def upload_pages_link(self, obj):
#         url = reverse('admin:chapter_upload_pages', args=[obj.pk])
#         return format_html(
#             '<a class="button" href="{}">📤 Sahifalarni yuklash</a>', url
#         )
#     upload_pages_link.short_description = "Bulk Upload"

#     # Tugma tahrirlash sahifasida (change_view)
#     def change_view(self, request, object_id, form_url='', extra_context=None):
#         if extra_context is None:
#             extra_context = {}

#         upload_url = reverse('admin:chapter_upload_pages', args=[object_id])
#         extra_context['upload_pages_button'] = format_html(
#             '''
#             <div style="margin: 10px 0 20px 0;">
#                 <a href="{}" class="button" style="
#                     background-color: #2e8540;
#                     color: white;
#                     padding: 6px 12px;
#                     border-radius: 5px;
#                     text-decoration: none;
#                     font-weight: bold;
#                 ">
#                     📤 Sahifalarni yuklash
#                 </a>
#             </div>
#             ''',
#             upload_url
#         )
#         return super().change_view(request, object_id, form_url, extra_context=extra_context)

#     # URL qo‘shish
#     def get_urls(self):
#         urls = super().get_urls()
#         custom_urls = [
#             path('<int:chapter_id>/upload_pages/', self.admin_site.admin_view(self.upload_pages_view), name='chapter_upload_pages'),
#         ]
#         return custom_urls + urls

#     # Yuklash view — filename'larni raqam bo‘yicha sort qiladi!
#     def upload_pages_view(self, request, chapter_id):
#         chapter = Chapter.objects.filter(pk=chapter_id).first()
#         if not chapter:
#             messages.error(request, "Bunday bob topilmadi.")
#             return redirect('admin:manga_chapter_changelist')

#         if request.method == 'POST':
#             form = MultiPageUploadForm(request.POST, request.FILES)
#             if form.is_valid():
#                 def extract_number(filename):
#                     match = re.search(r'(\d+)', filename)
#                     return int(match.group(1)) if match else 0

#                 files = sorted(
#                     request.FILES.getlist('images'),
#                     key=lambda f: extract_number(f.name)
#                 )

#                 existing_max = Page.objects.filter(chapter=chapter).aggregate(Max('page_number'))['page_number__max'] or 0

#                 new_pages = []
#                 for index, f in enumerate(files):
#                     new_pages.append(Page(
#                         chapter=chapter,
#                         image=f,
#                         page_number=existing_max + index + 1
#                     ))

#                 Page.objects.bulk_create(new_pages)
#                 messages.success(request, f"{len(files)} ta sahifa yuklandi!")
#                 return redirect('admin:manga_chapter_changelist')
#         else:
#             form = MultiPageUploadForm()

#         return render(request, 'admin/bulk_upload.html', {
#             'form': form,
#             'chapter': chapter,
#         })

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

@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("chapter", "page_number", "image_size_mb")
    raw_id_fields = ("chapter",)
    ordering = ("chapter", "page_number")
    list_filter = (IsWebPFilter,)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Chapter modelida `created_by` yo‘qligi uchun:
        # manga.created_by ga qaraymiz
        return qs.filter(chapter__manga__created_by=request.user)

    def image_size_mb(self, obj):
        if obj.image and os.path.isfile(obj.image.path):
            size_mb = os.path.getsize(obj.image.path) / (1024 * 1024)
            return f"{size_mb:.2f} MB"
        return "No file"
    image_size_mb.short_description = "Image Size (MB)"

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
