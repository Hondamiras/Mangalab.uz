from datetime import date
from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils.text import slugify
from PIL import Image
from io import BytesIO
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile
import os
from django.utils import timezone
from unidecode import unidecode
from django.utils.text import slugify
from django.db.models import Q

def make_search_key(s: str) -> str:
    # 'Bir zarbli odam' / 'One-Punch Man' / 'Ванпанчмен' / '원펀맨' → 'birzarbliodam' / 'onepunchman' / 'vanpanchmen' / 'wonpeonmaen'
    return slugify(unidecode(s or ""), allow_unicode=False).replace("-", "")

from django.utils.text import slugify
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

class MangaTelegramLink(models.Model):
    manga = models.ForeignKey(
        "Manga",
        on_delete=models.CASCADE,
        related_name="telegram_links",
        verbose_name="Qaysi Manga uchun"
    )
    name = models.CharField(max_length=100, default="", verbose_name="Link nomi", blank=True)
    link = models.URLField(verbose_name="Telegram havolasi", help_text="To'liq havolani kiriting, masalan: https://t.me/joinchat/AAAAAEg...", blank=True, null=True)

    def __str__(self):
        return self.name or self.link

class MangaLike(models.Model):
    manga = models.ForeignKey("Manga", on_delete=models.CASCADE, related_name="like_set")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="manga_likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("manga", "user")
        verbose_name = "Manga like"
        verbose_name_plural = "Manga likelar"

    def __str__(self):
        return f"{self.user.username} ❤ {self.manga.title}"

# apps/manga/models.py
class MangaTitle(models.Model):
    manga = models.ForeignKey("Manga", on_delete=models.CASCADE, related_name="titles")
    name = models.CharField(max_length=255, db_index=True)
    search_key = models.CharField(max_length=255, editable=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Manga qo'shimcha nomi"
        verbose_name_plural = "Manga qo'shimcha nomlari"
        constraints = [
            models.UniqueConstraint(fields=["manga", "search_key"], name="uniq_manga_title_by_searchkey"),
            models.CheckConstraint(check=~Q(search_key=""), name="mangatitle_search_key_not_blank"),
        ]
        indexes = [
            models.Index(fields=["search_key"]),
            models.Index(fields=["name"]),
        ]

    def save(self, *args, **kwargs):
            self.search_key = make_search_key(self.name)   # <<< shu
            super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class Manga(models.Model): 
    title = models.CharField(max_length=255, verbose_name="Nomi")
    title_search_key = models.CharField(max_length=255, editable=False, db_index=True, default="", verbose_name="Qidirish uchun kalit so'z")
    author = models.CharField(max_length=255, verbose_name="Muallifi")
    description = models.TextField(verbose_name="Ta'rifi")
    cover_image = models.ImageField(upload_to="covers/", verbose_name="Poster rasmi")
    genres = models.ManyToManyField("Genre", related_name="mangas", blank=True, verbose_name="Janrlar")
    tags = models.ManyToManyField("Tag", related_name="mangas", blank=True, verbose_name="Teglar")
    publication_date = models.DateField(null=True, blank=True, verbose_name="Chiqarilgan sana yani Manga qachon chiqgan?")
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
        verbose_name="Manga holati"
    )
    type = models.CharField(
        max_length=50,
        choices=[("Manga", "Manga"), ("Manhwa", "Manhwa"), ("Manhua", "Manhua"), ("Komiks", "Komiks"), ("OEL-manga", "OEL-manga"), ("Rumanga", "Rumanga")],
        default="Manga",
    )
    age_rating = models.CharField(
        max_length=50,
        choices=[("Belgilanmagan", "Belgilanmagan"), ("6+", "6+"), ("12+", "12+"), ("16+", "16+"), ("18+", "18+")],
        default="16+",
        verbose_name="Yosh chegarasi"
    )
    translation_status = models.CharField(
        max_length=50,
        choices=[
            ("Not Translated", "Tarjima qilinmagan"),
            ("In Progress", "Tarjima qilinmoqda"),
            ("Completed", "Tarjima qilingan"),
            ("Dropped", "Tashlab qo'yilgan"),
        ],
        default="In Progress",
        verbose_name="Tarjima holati"
    )
    team = models.ForeignKey(
        "accounts.TranslatorTeam",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="mangas",
        verbose_name="Jamoa (agar jamoa nomidan bo‘lsa)"
    )
    likes = models.ManyToManyField(
        User,
        through='MangaLike',
        related_name='liked_mangas',
        blank=True,
        verbose_name="Like qilgan foydalanuvchilar"
    )
    slug = models.SlugField(max_length=255, unique=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        # editable=False,
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

    def save(self, *args, **kwargs):
        # Slug avto-generatsiyasi, agar bo'sh bo'lsa
        if not self.slug:
            self.slug = slugify(self.title)

        # Convert image only if it's a new upload
        if self.cover_image and isinstance(self.cover_image.file, InMemoryUploadedFile):
            img = Image.open(self.cover_image)
            img = img.convert("RGBA")

            buffer = BytesIO()
            img.save(buffer, format="WEBP", quality=80, method=6)
            buffer.seek(0)

            base, _ = self.cover_image.name.rsplit('.', 1)
            webp_name = f"{slugify(base)}.webp"
            self.cover_image.save(webp_name, ContentFile(buffer.read()), save=False)

        self.title_search_key = make_search_key(self.title)
        super().save(*args, **kwargs)

    @property
    def likes_count(self) -> int:
        return self.likes.count()

class Genre(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="Janr nomi")
    
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
    
class Chapter(models.Model):
    manga = models.ForeignKey(
        Manga,
        on_delete=models.CASCADE,
        related_name="chapters",
        verbose_name="Manga",
    )
    volume = models.PositiveIntegerField(default=1, verbose_name="Jild")
    chapter_number = models.PositiveIntegerField(verbose_name="Bob")
    price_tanga = models.PositiveIntegerField(default=0, verbose_name="Bob narxi (tanga)")
    release_date = models.DateField(default=date.today, verbose_name="Chiqarilgan sana (Tegilmasin!)")
    published_at = models.DateTimeField(default=timezone.now, db_index=True, blank=True, null=True)  # <<— yangi
    updated_at   = models.DateTimeField(auto_now=True)                         

    thanks = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="thanked_chapters",
        blank=True,
    )

    class Meta:
        unique_together = ("manga", "chapter_number", "volume")
        indexes = [models.Index(fields=("manga", "chapter_number"))]
        verbose_name = "Bob "
        verbose_name_plural = "Boblar "

    def __str__(self) -> str:
        return f"{self.manga.title} - Jild: {self.volume}. Bob: {self.chapter_number}"

    @property
    def thanks_count(self):
        return self.thanks.count()
    
class ChapterVisit(models.Model):
    user    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chapter_visits")
    chapter = models.ForeignKey("manga.Chapter", on_delete=models.CASCADE, related_name="visits")
    visited_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ("user", "chapter")
        indexes = [
            models.Index(fields=("user", "chapter")),
            models.Index(fields=("chapter", "visited_at")),  # vaqtli kesim + chapter
        ]

    def __str__(self):
        return f"{self.user} → {self.chapter}"

class ChapterPurchase(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="purchased_chapters")
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="purchases")
    purchase_date = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now=True, editable=False, null=True, blank=True)

    class Meta:
        unique_together = ("user", "chapter")
        verbose_name = "Sotib olingan bob"
        verbose_name_plural = "Sotib olingan boblar"

    def __str__(self):
        return f"{self.user.username} → {self.chapter}"

class Page(models.Model):
    """
    Страница главы в формате изображения (JPEG/PNG).
    Каждая страница привязана к конкретной главе.
    """
    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.CASCADE,
        related_name='pages', verbose_name="Qaysi bobga tegishli?"
    )
    page_number = models.PositiveIntegerField(verbose_name="nechanchi sahifa?")
    image = models.ImageField(
        upload_to='chapters/pages/',
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'webp'])],
        help_text="Rasmni JPEG/WebP formatida yuklang.",
        verbose_name="Rasm (JPEG/WEBP formatida yuklang)"
    )

    class Meta:
        unique_together = ('chapter', 'page_number')
        ordering = ['page_number']
        verbose_name = "Sahifa "
        verbose_name_plural = "Sahifalar "


    def __str__(self):
        return f"{self.chapter} — Page {self.page_number}"
    
    def save(self, *args, **kwargs):
        # Сначала сохраняем оригинальный файл, чтобы self.image.path был доступен
        super().save(*args, **kwargs)

        # Открываем его через Pillow
        img_path = self.image.path
        img = Image.open(img_path).convert('RGB')

        # Генерируем имя для WebP (заменяем расширение)
        base, _ext = os.path.splitext(self.image.name)
        webp_name = f"{base}.webp"

        # Сохраняем в буфер
        buffer = BytesIO()
        img.save(buffer, format='WEBP', quality=80)  # можно подстроить quality
        buffer.seek(0)

        # Записываем в хранилище как новый файл
        self.image.save(webp_name, ContentFile(buffer.read()), save=False)

        # Удаляем старый файл (JPEG/PNG)
        try:
            os.remove(img_path)
        except OSError:
            pass

        # Финальный save, чтобы обновлённое имя image записалось в БД
        super().save(update_fields=['image'])

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

