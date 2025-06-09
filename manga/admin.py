from django.contrib import admin
from .models import Page, Tag, Genre, Manga, Chapter, Contributor, ChapterContributor


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
        "thanks_count",
    )
    list_filter    = ("release_date", "manga")
    list_per_page  = 25

@admin.register(Page)
class PageAdmin(OwnMixin, admin.ModelAdmin):
    list_display   = ("chapter", "page_number")
    list_filter    = ("chapter", "page_number", "chapter__manga__title")
    raw_id_fields  = ("chapter",)
    search_fields  = ("chapter__manga__title", "chapter__chapter_number")
    ordering       = ("chapter", "page_number")

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
