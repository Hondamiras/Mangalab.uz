# apps/manga/models.py
from datetime import date
import os
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image
from io import BytesIO
from unidecode import unidecode


User = get_user_model()

# -------------------------
# Helpers
# -------------------------
def make_search_key(s: str) -> str:
    # 'Bir zarbli odam' / 'One-Punch Man' / 'Ванпанчмен' / '원펀맨'
    # → 'birzarbliodam' / 'onepunchman' / 'vanpanchmen' / 'wonpeonmaen'
    return slugify(unidecode(s or ""), allow_unicode=False).replace("-", "")


def _unique_slug(instance, value: str, field_name: str = "slug") -> str:
    base = slugify(value or "") or "item"
    slug = base
    i = 2
    Model = type(instance)
    qs = Model.objects.exclude(pk=instance.pk) if instance.pk else Model.objects.all()
    while qs.filter(**{f"{field_name}__iexact": slug}).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


# -------------------------
# Tag
# -------------------------
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
        verbose_name = "Teg"
        verbose_name_plural = "Teglar"

    def __str__(self):
        return self.name


# -------------------------
# Manga <-> Telegram link
# -------------------------
class MangaTelegramLink(models.Model):
    manga = models.ForeignKey(
        "Manga",
        on_delete=models.CASCADE,
        related_name="telegram_links",
        verbose_name="Qaysi Manga uchun",
    )
    name = models.CharField(max_length=100, default="", verbose_name="Link nomi", blank=True)
    link = models.URLField(
        verbose_name="Telegram havolasi",
        help_text="To'liq havolani kiriting, masalan: https://t.me/joinchat/AAAAAEg...",
        blank=True,
        null=True,
    )

    def __str__(self):
        return self.name or (self.link or "")


# -------------------------
# Likes (through)
# -------------------------
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


# -------------------------
# Additional titles
# -------------------------
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
    # Tezkor qidiruv uchun alohida indekslar ham qoldirilgan
        indexes = [
            models.Index(fields=["search_key"]),
            models.Index(fields=["name"]),
        ]

    def save(self, *args, **kwargs):
        self.search_key = make_search_key(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# -------------------------
# Genre
# -------------------------
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
        verbose_name = "Janr"
        verbose_name_plural = "Janrlar"

    def __str__(self) -> str:
        return self.name


# -------------------------
# Manga
# -------------------------
TYPE_CHOICES = [
    ("Manga", "Manga"),
    ("Manhwa", "Manhwa"),
    ("Manhua", "Manhua"),
    ("Komiks", "Komiks"),
    ("OEL-manga", "OEL-manga"),
    ("Rumanga", "Rumanga"),
]
STATUS_CHOICES = [
    ("Ongoing", "Davom etmoqda"),
    ("Completed", "To'liq chiqarilgan"),
    ("Stopped", "Bekor qilingan"),
    ("Paused", "To'xtatilgan"),
    ("Announced", "E'lon qilingan"),
]
AGE_CHOICES = [
    ("Belgilanmagan", "Belgilanmagan"),
    ("6+", "6+"),
    ("12+", "12+"),
    ("16+", "16+"),
    ("18+", "18+"),
]
TRANS_CHOICES = [
    ("Not Translated", "Tarjima qilinmagan"),
    ("In Progress", "Tarjima qilinmoqda"),
    ("Completed", "Tarjima qilingan"),
    ("Dropped", "Tashlab qo'yilgan"),
]


class Manga(models.Model):
    title = models.CharField(max_length=255, verbose_name="Nomi")
    title_search_key = models.CharField(
        max_length=255, editable=False, db_index=True, default="", verbose_name="Qidirish uchun kalit so'z"
    )
    author = models.CharField(max_length=255, verbose_name="Muallifi")
    description = models.TextField(verbose_name="Ta'rifi")
    cover_image = models.ImageField(upload_to="covers/", verbose_name="Poster rasmi")
    genres = models.ManyToManyField("Genre", related_name="mangas", blank=True, verbose_name="Janrlar")
    tags = models.ManyToManyField("Tag", related_name="mangas", blank=True, verbose_name="Teglar")
    publication_date = models.DateField(null=True, blank=True, verbose_name="Chiqarilgan sana")
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="Ongoing", verbose_name="Manga holati")
    type = models.CharField(max_length=50, choices=TYPE_CHOICES, default="Manhwa")
    age_rating = models.CharField(max_length=50, choices=AGE_CHOICES, default="16+", verbose_name="Yosh chegarasi")
    translation_status = models.CharField(max_length=50, choices=TRANS_CHOICES, default="In Progress", verbose_name="Tarjima holati")
    team = models.ForeignKey(
        "accounts.TranslatorTeam",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="mangas",
        verbose_name="Jamoa (agar jamoa nomidan bo‘lsa)"
    )
    likes = models.ManyToManyField(
        User, through='MangaLike', related_name='liked_mangas', blank=True, verbose_name="Like qilgan foydalanuvchilar"
    )
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    # 👇 YANGI – aynan qaysi tarjimon(lar) bu mangani tarjima qilayotganini qo'l bilan belgilaysiz
    translators = models.ManyToManyField(
        "accounts.UserProfile",
        related_name="translated_mangas",
        blank=True,
        verbose_name="Tarjimonlar",
        limit_choices_to={"is_translator": True},
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mangas_created",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("title",)
        verbose_name = "Taytl"
        verbose_name_plural = "Taytlar"
        indexes = [models.Index(fields=("title",))]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        # Slug avto-generatsiyasi (unikal)
        if not self.slug:
            self.slug = _unique_slug(self, self.title)

        # Yangi upload bo‘lsa (InMemory) — WEBP ga o‘tkazamiz
        fobj = getattr(self.cover_image, "file", None)
        if self.cover_image and isinstance(fobj, InMemoryUploadedFile):
            img = Image.open(self.cover_image).convert("RGBA")
            buf = BytesIO()
            img.save(buf, format="WEBP", quality=80, method=6)
            buf.seek(0)
            base, _ = os.path.splitext(self.cover_image.name)
            webp_name = f"{slugify(base)}.webp"
            self.cover_image.save(webp_name, ContentFile(buf.read()), save=False)

        self.title_search_key = make_search_key(self.title)
        super().save(*args, **kwargs)

    @property
    def likes_count(self) -> int:
        return self.likes.count()


# -------------------------
# Chapter
# -------------------------
class Chapter(models.Model):
    manga = models.ForeignKey(Manga, on_delete=models.CASCADE, related_name="chapters", verbose_name="Manga")
    volume = models.PositiveIntegerField(default=1, verbose_name="Jild")
    chapter_number = models.PositiveIntegerField(verbose_name="Bob")
    price_tanga = models.PositiveIntegerField(default=0, verbose_name="Bob narxi (tanga)")
    release_date = models.DateField(default=date.today, verbose_name="Chiqarilgan sana (Tegilmasin!)")
    published_at = models.DateTimeField(default=timezone.now, db_index=True, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    thanks = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="thanked_chapters", blank=True)

    class Meta:
        unique_together = ("manga", "chapter_number", "volume")
        indexes = [models.Index(fields=("manga", "chapter_number"))]
        verbose_name = "Bob"
        verbose_name_plural = "Boblar"

    def __str__(self) -> str:
        return f"{self.manga.title} - Jild: {self.volume}. Bob: {self.chapter_number}"

    @property
    def thanks_count(self):
        return self.thanks.count()


# -------------------------
# Visits & Purchases
# -------------------------
class ChapterVisit(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chapter_visits")
    chapter = models.ForeignKey("manga.Chapter", on_delete=models.CASCADE, related_name="visits")
    visited_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ("user", "chapter")
        indexes = [
            models.Index(fields=("user", "chapter")),
            models.Index(fields=("chapter", "visited_at")),
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


# -------------------------
# Page (images)
# -------------------------
class Page(models.Model):
    """
    Bob sahifasi rasm ko‘rinishida.
    Yangi upload (InMemory) bo‘lsa — WEBP ga RAM’da o‘tkaziladi.
    """
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='pages', verbose_name="Qaysi bobga tegishli?")
    page_number = models.PositiveIntegerField(verbose_name="nechanchi sahifa?")
    image = models.ImageField(
        upload_to='chapters/pages/',
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'webp'])],
        help_text="Rasmni JPEG/WebP formatida yuklang.",
        verbose_name="Rasm (JPEG/WEBP)"
    )

    class Meta:
        unique_together = ('chapter', 'page_number')
        ordering = ['page_number']
        verbose_name = "Sahifa"
        verbose_name_plural = "Sahifalar"

    def __str__(self):
        return f"{self.chapter} — Page {self.page_number}"

    def save(self, *args, **kwargs):
        # 1) Validatsiya
        self.full_clean()

        # 2) Eski fayl nomini (update bo‘lsa) saqlab qolamiz
        old_name = None
        if self.pk:
            old = type(self).objects.only("image").filter(pk=self.pk).first()
            if old and old.image and old.image.name != self.image.name:
                old_name = old.image.name

        # 3) InMemory upload bo‘lsa — WEBP ga o‘tkazamiz
        fobj = getattr(self.image, "file", None)
        if self.image and isinstance(fobj, InMemoryUploadedFile):
            img = Image.open(self.image).convert('RGB')
            buf = BytesIO()
            img.save(buf, format='WEBP', quality=80)
            buf.seek(0)
            base, _ = os.path.splitext(self.image.name)
            webp_name = f"{slugify(base)}.webp"
            self.image.save(webp_name, ContentFile(buf.read()), save=False)

        # 4) Saqlash
        super().save(*args, **kwargs)

        # 5) Eski faylni storage’dan o‘chirish (local/S3/GCS)
        if old_name and old_name != (self.image.name or "") and default_storage.exists(old_name):
            try:
                default_storage.delete(old_name)
            except Exception:
                pass


# Faylni model o‘chirilganda ham storage’dan o‘chirish
from django.db.models.signals import post_delete
from django.dispatch import receiver

@receiver(post_delete, sender=Page)
def _delete_page_file_on_remove(sender, instance, **kwargs):
    if instance.image:
        try:
            instance.image.delete(save=False)
        except Exception:
            pass


# -------------------------
# Reading progress
# -------------------------
class ReadingProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reading_progress")
    manga = models.ForeignKey(Manga, on_delete=models.CASCADE)
    last_read_chapter = models.ForeignKey(Chapter, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_read_page = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "manga")
        verbose_name = "O'qish jarayoni"
        verbose_name_plural = "O'qish jarayonlari"

    @property
    def last_read_chapter_pk(self):
        return self.last_read_chapter.id if self.last_read_chapter else None

    def __str__(self) -> str:
        ch_num = self.last_read_chapter.chapter_number if self.last_read_chapter else "—"
        return f"{self.user.username} — {self.manga.title} (ch.{ch_num}, p.{self.last_read_page})"
