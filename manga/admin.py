from django.contrib import admin
from .models import Tag, Genre, Manga, Chapter, Contributor, ChapterContributor


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


@admin.register(Chapter)
class ChapterAdmin(OwnMixin, admin.ModelAdmin):
    list_display   = (
        "manga",
        "chapter_number",
        "volume",
        "release_date",
        "created_by",
        "pdf_size",
        "thanks_count",
    )
    list_filter    = ("release_date", "manga")
    list_per_page  = 30

    def get_exclude(self, request, obj=None):
        # скрыть поле thanks для всех, кроме суперпользователя
        excludes = super().get_exclude(request, obj) or []
        if not request.user.is_superuser:
            excludes = list(excludes) + ['thanks']
        return excludes

    def pdf_size(self, obj):
        if not obj.pdf:
            return "-"
        try:
            mb = obj.pdf.size / (1024 * 1024)
            return f"{mb:.2f} MB"
        except (ValueError, OSError):
            return "-"
    pdf_size.short_description = "Размер PDF"


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
