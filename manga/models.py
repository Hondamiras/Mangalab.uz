from datetime import date
from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db.models.signals import post_save
from django.dispatch import receiver
from wand.image import Image  # pip install Wand
import subprocess
import os

User = get_user_model()

class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    created_by = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    on_delete=models.CASCADE,
    editable=False,
    related_name="tags_created",
    null=True,
    blank=True,
    )

    class Meta:
        ordering = ("name",)
        verbose_name = "Teg "
        verbose_name_plural = "Teglar "

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
        choices=[("Manga", "Manga"), ("Manhwa", "Manhwa"), ("Manhua", "Manhua"), ("Komiks", "Komiks")],
        default="Manga",
    )
    age_rating = models.CharField(
        max_length=50,
        choices=[("Belgilanmagan", "Belgilanmagan"), ("6+", "6+"), ("12+", "12+"), ("16+", "16+"), ("18+", "18+")],
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

    created_by = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    on_delete=models.CASCADE,
    editable=False,
    related_name="mangas_created",
    null=True,
    blank=True,
    )

    class Meta:
        ordering = ("title",)
        verbose_name = "Taytl "
        verbose_name_plural = "Taytlar "
        indexes = [models.Index(fields=("title",))]

    def __str__(self) -> str:
        return self.title


class Genre(models.Model):
    name = models.CharField(max_length=50, unique=True)
    
    created_by = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    on_delete=models.CASCADE,
    editable=False,
    related_name="genres_created",
    null=True,
    blank=True,
    )

    class Meta:
        ordering = ("name",)
        verbose_name = "Janr "
        verbose_name_plural = "Janrlar "

    def __str__(self) -> str:
        return self.name
    
class Contributor(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Hissa qo'shuvchi "
        verbose_name_plural = "Hissa qo'shuvchilar "

    def __str__(self):
        return self.name

class Chapter(models.Model):
    manga = models.ForeignKey(
        Manga,
        on_delete=models.CASCADE,
        related_name="chapters"
    )
    volume = models.PositiveIntegerField(default=1)
    chapter_number = models.PositiveIntegerField()
    release_date = models.DateField(default=date.today)

    pdf = models.FileField(
        upload_to='chapters/pdfs/',
        blank=True,
        null=True,
        validators=[FileExtensionValidator(['pdf'])]
    )
    preview = models.ImageField(
        upload_to='chapters/previews/',
        blank=True,
        null=True,
    )

    contributors = models.ManyToManyField(
        Contributor,
        through='ChapterContributor',
        related_name='chapters'
    )
    thanks = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="thanked_chapters",
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        editable=False,
        related_name="chapters_created",
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = ("manga", "chapter_number", "volume")
        indexes = [models.Index(fields=("manga", "chapter_number"))]

    def __str__(self):
        return f"{self.manga.title} — Ch. {self.chapter_number}"

    @property
    def thanks_count(self):
        return self.thanks.count()

    def save(self, *args, **kwargs):
        # Сначала сохраняем PDF (и возможно прошлое preview)
        super().save(*args, **kwargs)

        # Если PDF есть, а preview ещё не создано — генерируем
        if self.pdf and not self.preview:
            try:
                # Открываем первую страницу PDF
                with Image(filename=f"{self.pdf.path}[0]", resolution=150) as img:
                    img.format = 'jpeg'
                    # Формируем путь для превью внутри MEDIA_ROOT
                    # Например: MEDIA_ROOT/chapters/previews/manga-1-ch1.jpg
                    preview_path = self.pdf.path \
                        .replace('/pdfs/', '/previews/') \
                        .rsplit('.', 1)[0] + '.jpg'
                    img.save(filename=preview_path)

                # Сохраняем относительный путь в поле preview
                self.preview.name = preview_path.split(settings.MEDIA_ROOT + '/', 1)[1]
                # Только обновляем поле preview, чтобы не зациклить save()
                super().save(update_fields=['preview'])

            except Exception as e:
                # Логируем, но не падаем с ошибкой
                import logging
                logging.getLogger(__name__).error(
                    f"Failed to generate preview for Chapter {self.pk}: {e}"
                )   
@receiver(post_save, sender=Chapter)
def optimize_pdf_after_upload(sender, instance, **kwargs):
    if instance.pdf:
        input_path = instance.pdf.path
        optimized_path = input_path.replace('.pdf', '_opt.pdf')

        # Linearize через qpdf
        subprocess.run([
            "qpdf", "--linearize", input_path, optimized_path
        ])

        # Заменяем старый файл
        os.replace(optimized_path, input_path)


class ChapterContributor(models.Model):
    ROLE_CHOICES = [
        ('translator', 'Tarjimon'),
        ('cleaner', 'Cleaner'),
        ('typer', 'Typer'),
        # при необходимости можно добавить: ('letterer','Letterer'), и т.д.
    ]

    chapter     = models.ForeignKey(Chapter,     on_delete=models.CASCADE)
    contributor = models.ForeignKey(Contributor, on_delete=models.CASCADE)
    role        = models.CharField(max_length=12, choices=ROLE_CHOICES)

    class Meta:
        unique_together = ('chapter', 'contributor', 'role')
        verbose_name = "Bobga hissa qo'shuvchi "
        verbose_name_plural = "Bobga hissa qo'shuvchilar "

    def __str__(self):
        return f"{self.contributor.name}  {self.get_role_display()}"


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
        verbose_name = "O'qish jarayoni "
        verbose_name_plural = "O'qish jarayonlari "

    @property
    def last_read_chapter_pk(self):
        """Возвращает id последней прочитанной главы, или None."""
        return self.last_read_chapter.id if self.last_read_chapter else None

    def __str__(self) -> str:
        ch_num = self.last_read_chapter.chapter_number if self.last_read_chapter else "—"
        return f"{self.user.username} — {self.manga.title} (ch.{ch_num}, p.{self.last_read_page})"

