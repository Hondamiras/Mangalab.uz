from django.contrib import admin
from .models import Manga, Chapter, Genre, Tag


@admin.register(Manga)
class MangaAdmin(admin.ModelAdmin):
    list_display  = ("title", "author", "status", "publication_date")
    search_fields = ("title", "author")
    list_filter   = ("status", "genres")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ("manga", "chapter_number", "title", "release_date")
    list_filter  = ("release_date",)


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display  = ("name",)
    search_fields = ("name",)


# @admin.register(Page)
# class PageAdmin(admin.ModelAdmin):
#     list_display = ("chapter", "page_number", "image")
#     list_filter  = ("chapter",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display  = ("name",)
    search_fields = ("name",)