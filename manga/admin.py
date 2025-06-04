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
    list_display = (
        "manga",
        "chapter_number",
        "volume",
        "release_date",
        "pdf_size",       # ← добавляем сюда метод, который покажет размер файла в МБ
        "thanks_count",   # если вам нужно и количество «Спасибо» тоже
    )
    list_filter = ("release_date", "manga")
    list_per_page = 10

    def pdf_size(self, obj):
        """
        Возвращает размер связанного PDF-файла в мегабайтах (2 знака после запятой).
        Если файла нет или он пустой → возвращает «–».
        """
        if not obj.pdf:
            return "-"   # нет файла вовсе
        try:
            size_bytes = obj.pdf.size  # размер в байтах
        except (ValueError, OSError):
            return "-"
        mb = size_bytes / (1024 * 1024)
        return f"{mb:.2f} MB"
    pdf_size.short_description = "Размер PDF"
    # (опционально) чтобы можно было сортировать по размеру, можно указать:
    # pdf_size.admin_order_field = 'pdf'  
    # но по «pdf» и так сортируется по названию/пути – сортировка по реальному size потребует аннотации.

@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display  = ("name",)
    search_fields = ("name",)

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display  = ("name",)
    search_fields = ("name",)