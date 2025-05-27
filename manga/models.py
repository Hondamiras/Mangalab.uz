from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings

User = get_user_model()

class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Тег"
        verbose_name_plural = "Теги"

    def __str__(self):
        return self.name

class Manga(models.Model):
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255)
    description = models.TextField()
    cover_image = models.ImageField(upload_to="covers/")
    genres = models.ManyToManyField("Genre", related_name="mangas", blank=True)
    tags = models.ManyToManyField("Tag", related_name="mangas", blank=True)
    publication_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=50,
        choices=[
            ("Ongoing", "Davom etmoqda"),
            ("Completed", "To'liq chiqarilgan"),
            ("Stopped", "Bekor qilingan"),
            ("Paused", "To'xtatilgan"),
            ("Announced", "E'lon qilingan"),
        ],
        default="Ongoing",
    )
    type = models.CharField(
        max_length=50,
        choices=[("Manga", "Manga"), ("Manhwa", "Manhwa"), ("Manhua", "Manhua")],
        default="Manga",
    )
    age_rating = models.CharField(
        max_length=50,
        choices=[("None", "None"), ("6+", "6+"), ("12+", "12+"), ("16+", "16+"), ("18+", "18+")],
        default="None",
    )
    translation_status = models.CharField(
        max_length=50,
        choices=[
            ("Not Translated", "Tarjima qilinmagan"),
            ("In Progress", "Tarjima qilinmoqda"),
            ("Completed", "Tarjima qilingan"),
            ("Dropped", "Tashlab qo'yilgan"),
        ],
        default="Not Translated",
    )
    slug = models.SlugField(max_length=255, unique=True, blank=True)

    class Meta:
        ordering = ("title",)
        verbose_name = "Taytl"
        verbose_name_plural = "Taytlar"
        indexes = [models.Index(fields=("title",))]

    def __str__(self) -> str:
        return self.title


class Genre(models.Model):
    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Жанр"
        verbose_name_plural = "Жанры"

    def __str__(self) -> str:
        return self.name

from django.core.validators import FileExtensionValidator

class Chapter(models.Model):
    manga = models.ForeignKey(Manga, on_delete=models.CASCADE, related_name="chapters")
    title = models.CharField(max_length=255)
    volume = models.PositiveIntegerField(default=1)
    chapter_number = models.PositiveIntegerField()
    release_date = models.DateField()

    pdf = models.FileField(
        upload_to='chapters/pdfs/',
        blank=True,
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])]
    )

    thanks = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="thanked_chapters",
        blank=True,
    )
    class Meta:
        ordering = ("chapter_number",)
        indexes = [models.Index(fields=("manga", "chapter_number"))]
        unique_together = ("manga", "chapter_number")
        verbose_name = "Bob"
        verbose_name_plural = "Boblar"

    def __str__(self) -> str:
        return f"{self.manga.title} — Ch. {self.chapter_number}: {self.title}"
    
    @property
    def thanks_count(self):
        """Возвращает число пользователей, нажавших «Спасибо»."""
        return self.thanks.count()


# class Page(models.Model):
#     chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="pages")
#     page_number = models.PositiveIntegerField()
#     image = models.ImageField(upload_to="pages/")

#     class Meta:
#         ordering = ("page_number",)
#         unique_together = ("chapter", "page_number")
#         verbose_name = "Страница"
#         verbose_name_plural = "Страницы"

#     def __str__(self) -> str:
#         return f"{self.chapter} — p.{self.page_number}"


class ReadingProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reading_progress")
    manga = models.ForeignKey(Manga, on_delete=models.CASCADE)
    last_read_chapter = models.ForeignKey(
        Chapter, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_read_page = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "manga")
        verbose_name = "Прогресс чтения"
        verbose_name_plural = "Прогресс чтения"

    @property
    def last_read_chapter_pk(self):
        """Возвращает id последней прочитанной главы, или None."""
        return self.last_read_chapter.id if self.last_read_chapter else None

    def __str__(self) -> str:
        ch_num = self.last_read_chapter.chapter_number if self.last_read_chapter else "—"
        return f"{self.user.username} — {self.manga.title} (ch.{ch_num}, p.{self.last_read_page})"

