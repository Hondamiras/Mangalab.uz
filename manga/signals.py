from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache

from accounts.models import ReadingStatus, UserProfile
from manga.models import (
    Chapter, ChapterPurchase, Genre, Manga, MangaTelegramLink,
    ReadingProgress, Tag
)

# --------- Cache helper’lar (Redis bo'lmasa ham yiqilmasin) -----------------
def cache_delete_pattern(pattern: str) -> None:
    """Redis backend bo'lmasa ham xavfsiz ishlash uchun."""
    delete_pattern = getattr(cache, "delete_pattern", None)
    if callable(delete_pattern):
        delete_pattern(pattern)
        return
    keys_fn = getattr(cache, "keys", None)
    if callable(keys_fn):
        try:
            for k in keys_fn(pattern):
                cache.delete(k)
        except Exception:
            # locmem backend’da .keys yo‘q bo‘lishi mumkin — shunchaki e'tiborsiz
            pass

def cache_keys(pattern: str):
    keys_fn = getattr(cache, "keys", None)
    if callable(keys_fn):
        try:
            return list(keys_fn(pattern))
        except Exception:
            return []
    return []

# ------------------------ Katalog/keng ko'lamli keshlar --------------------
@receiver([post_save, post_delete], sender=Manga)
@receiver([post_save, post_delete], sender=Genre)
@receiver([post_save, post_delete], sender=Tag)
@receiver([post_save, post_delete], sender=UserProfile)
def clear_catalog_cache(sender, **kwargs):
    # Static kalitlar
    keys_to_delete = [
        "base_manga_queryset",
        "top_translators",
        "trending_mangas",
        "latest_mangas",
        "all_genres",
        "all_tags",
        "status_choices",
        "age_rating_choices",
        "type_choices",
        "translation_choices",
    ]
    # pattern asosida: manga_list_*
    keys_to_delete.extend(cache_keys("manga_list_*"))
    if keys_to_delete:
        cache.delete_many(keys_to_delete)

# ----------------------------- Manga obyektiga oid --------------------------
@receiver([post_save, post_delete], sender=Manga)
def clear_manga_object_cache(sender, instance: Manga, **kwargs):
    patterns = [
        f"manga_obj_{instance.slug}",
        f"similar_mangas_{instance.pk}",
        f"telegram_links_{instance.pk}",
        f"first_chapter_{instance.pk}",
        f"chapters_{instance.pk}_*",
        f"manga_details_{instance.slug}_*",
    ]
    for p in patterns:
        cache_delete_pattern(p)

# ----------------------------- Chapterga oid --------------------------------
@receiver([post_save, post_delete], sender=Chapter)
def clear_chapter_related_cache(sender, instance: Chapter, **kwargs):
    # Manga bo'ylab chapterlar, prev/next, sahifalar va chapter_read keshlarini tozalaymiz
    patterns = [
        f"chapters_{instance.manga.pk}_*",
        f"first_chapter_{instance.manga.pk}",
        f"chapter_{instance.manga.slug}_*",
        f"all_chapters_{instance.manga.slug}",
        f"prev_chapter_{instance.manga.slug}_*",
        f"next_chapter_{instance.manga.slug}_*",
        f"pages_{instance.manga.slug}_*",
        f"chapter_read_{instance.manga.slug}_*",
    ]
    for p in patterns:
        cache_delete_pattern(p)

# ----------------------------- Xaridga oid ----------------------------------
@receiver([post_save, post_delete], sender=ChapterPurchase)
def clear_purchase_cache(sender, instance: ChapterPurchase, **kwargs):
    # Bir joyga jamladik (oldingi ikki xil handler o‘rniga)
    cache.delete(f"chapter_purchased_{instance.user.pk}_{instance.chapter.pk}")
    cache.delete(f"purchased_{instance.user.pk}_{instance.chapter.manga.slug}")
    cache.delete(f"purchased_chapters_{instance.user.pk}_{instance.chapter.manga.pk}")

# ----------------------------- ReadingStatus --------------------------------
@receiver([post_save, post_delete], sender=ReadingStatus)
def clear_reading_status_cache(sender, instance: ReadingStatus, **kwargs):
    cache.delete(f"reading_status_{instance.user_profile.pk}_{instance.manga.pk}")

# ----------------------------- UserProfile ----------------------------------
@receiver([post_save, post_delete], sender=UserProfile)
def clear_user_profile_cache(sender, instance: UserProfile, **kwargs):
    cache.delete(f"user_profile_{instance.user.pk}")

# ----------------------------- Telegram link --------------------------------
@receiver([post_save, post_delete], sender=MangaTelegramLink)
def clear_telegram_link_cache(sender, instance: MangaTelegramLink, **kwargs):
    cache.delete(f"telegram_links_{instance.manga.pk}")

# ----------------------------- ReadingProgress ------------------------------
@receiver([post_save, post_delete], sender=ReadingProgress)
def clear_reading_progress_cache(sender, instance: ReadingProgress, **kwargs):
    cache.delete(f"user_read_{instance.user.pk}_{instance.manga.slug}")
