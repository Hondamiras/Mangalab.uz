import os
from django.contrib import admin
from .models import Page, Tag, Genre, Manga, Chapter, Contributor, ChapterContributor

admin.site.site_header = "MangaLab Admin"
admin.site.site_title = "MangaLab Admin Panel"
admin.site.index_title = "MangaLab Admin Paneliga xush kelibsiz!"

class OwnMixin:
    """Mixin для ограничения видимости и создания "своих" записей"""
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(created_by=request.user)

    def has_change_permission(self, request, obj=None):
        has_class_perm = super().has_change_permission(request, obj)
        if not has_class_perm:
            return False
        if obj is None or request.user.is_superuser:
            return True
        return obj.created_by == request.user

    def has_delete_permission(self, request, obj=None):
        has_class_perm = super().has_delete_permission(request, obj)
        if not has_class_perm:
            return False
        if obj is None or request.user.is_superuser:
            return True
        return obj.created_by == request.user


# 1. Таксономии
# --------------
@admin.register(Tag)
class TagAdmin(OwnMixin, admin.ModelAdmin):
    list_display   = ("name", "created_by")
    search_fields  = ("name",)


@admin.register(Genre)
class GenreAdmin(OwnMixin, admin.ModelAdmin):
    list_display   = ("name", "created_by")
    search_fields  = ("name",)


# 2. Контент
# ------------
@admin.register(Manga)
class MangaAdmin(OwnMixin, admin.ModelAdmin):
    list_display        = ("title", "author", "status", "publication_date", "created_by")
    search_fields       = ("title", "author")
    list_filter         = ("status", "genres")
    prepopulated_fields = {"slug": ("title",)}


from django.contrib import admin
from django.db.models import Max
from .models import Chapter, Page

class PageInline(admin.TabularInline):
    model = Page
    fields = ('page_number', 'image')
    extra = 20
    ordering = ('page_number',)

    def get_formset(self, request, obj=None, **kwargs):
        FormSet = super().get_formset(request, obj, **kwargs)

        class NumberingFormSet(FormSet):
            def __init__(self, *args, **kws):
                super().__init__(*args, **kws)
                # Определяем последний занятый номер страниц:
                if obj:
                    last = obj.pages.aggregate(
                        max_num=Max('page_number')
                    )['max_num'] or 0
                else:
                    # Для создания новой главы — начинаем с нуля
                    last = 0

                # Берём только новые (пустые) формы:
                new_forms = [f for f in self.forms if not f.instance.pk]
                # Заполняем initial.page_number последовательно:
                for idx, form in enumerate(new_forms, start=1):
                    form.initial['page_number'] = last + idx

        return NumberingFormSet

@admin.register(Chapter)
class ChapterAdmin(OwnMixin, admin.ModelAdmin):
    inlines       = [PageInline]
    list_display  = (
        "manga",
        "chapter_number",
        "volume",
        "release_date",
        "created_by",
        "thanks_count",
    )
    list_filter   = ("release_date", "manga")
    list_per_page = 20


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
class PageAdmin(OwnMixin, admin.ModelAdmin):
    list_display  = ("chapter", "page_number", "image_size_mb")
    list_filter   = (IsWebPFilter, "chapter", "page_number", "chapter__manga__title")
    raw_id_fields = ("chapter",)
    search_fields = ("chapter__manga__title", "chapter__chapter_number")
    ordering      = ("chapter", "page_number")

    def image_size_mb(self, obj):
        if obj.image and os.path.isfile(obj.image.path):
            size_mb = os.path.getsize(obj.image.path) / (1024 * 1024)
            return f"{size_mb:.2f} MB"
        return "No file"

    image_size_mb.short_description = "Image Size (MB)"




# 3. Контрибьюторы
# -----------------
class ChapterContributorInline(admin.TabularInline):
    model   = ChapterContributor
    extra   = 0
    fk_name = "contributor"


@admin.register(Contributor)
class ContributorAdmin(admin.ModelAdmin):
    list_display  = ("name", "is_translator", "is_cleaner", "is_typer")
    search_fields = ("name",)
    ordering      = ("name",)
    inlines       = [ChapterContributorInline]

    @admin.display(boolean=True, description="Tarjimon")
    def is_translator(self, obj):
        return obj.chaptercontributor_set.filter(role="translator").exists()

    @admin.display(boolean=True, description="Clean")
    def is_cleaner(self, obj):
        return obj.chaptercontributor_set.filter(role="cleaner").exists()

    @admin.display(boolean=True, description="Type")
    def is_typer(self, obj):
        return obj.chaptercontributor_set.filter(role="typer").exists()


@admin.register(ChapterContributor)
class ChapterContributorAdmin(admin.ModelAdmin):
    list_display   = ("chapter", "contributor", "role")
    list_filter    = ("role",)
    raw_id_fields  = ("chapter", "contributor")
