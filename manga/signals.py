from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache

from accounts.models import ReadingStatus, UserProfile
from manga.models import Chapter, ChapterPurchase, Genre, Manga, MangaTelegramLink, ReadingProgress, Tag

@receiver([post_save, post_delete], sender=Manga)
@receiver([post_save, post_delete], sender=Genre)
@receiver([post_save, post_delete], sender=Tag)
@receiver([post_save, post_delete], sender=UserProfile)
def clear_manga_cache(sender, **kwargs):
    # Clear pattern-based cache keys (requires Redis)
    keys_to_delete = [
        'base_manga_queryset',
        'top_translators',
        'trending_mangas',
        'latest_mangas',
        'all_genres',
        'all_tags',
        'status_choices',
        'age_rating_choices',
        'type_choices',
        'translation_choices',
    ]
    
    # Also clear all manga_list_* keys
    all_keys = cache.keys('manga_list_*')
    keys_to_delete.extend(all_keys)
    
    cache.delete_many(keys_to_delete)


@receiver([post_save, post_delete], sender=Manga)
def clear_manga_cache(sender, instance, **kwargs):
    cache.delete_pattern(f'manga_obj_{instance.slug}')
    cache.delete_pattern(f'similar_mangas_{instance.pk}')
    cache.delete_pattern(f'telegram_links_{instance.pk}')
    cache.delete_pattern(f'first_chapter_{instance.pk}')
    cache.delete_pattern(f'chapters_{instance.pk}_*')
    cache.delete_pattern(f'manga_details_{instance.slug}_*')

@receiver([post_save, post_delete], sender=Chapter)
def clear_chapter_cache(sender, instance, **kwargs):
    cache.delete_pattern(f'chapters_{instance.manga.pk}_*')
    cache.delete_pattern(f'first_chapter_{instance.manga.pk}')

@receiver([post_save, post_delete], sender=ChapterPurchase)
def clear_purchase_cache(sender, instance, **kwargs):
    cache.delete(f'purchased_chapters_{instance.user.pk}_{instance.chapter.manga.pk}')

@receiver([post_save, post_delete], sender=ReadingStatus)
def clear_reading_status_cache(sender, instance, **kwargs):
    cache.delete(f'reading_status_{instance.user_profile.pk}_{instance.manga.pk}')

@receiver([post_save, post_delete], sender=UserProfile)
def clear_user_profile_cache(sender, instance, **kwargs):
    cache.delete(f'user_profile_{instance.user.pk}')

@receiver([post_save, post_delete], sender=MangaTelegramLink)
def clear_telegram_link_cache(sender, instance, **kwargs):
    cache.delete(f'telegram_links_{instance.manga.pk}')

@receiver([post_save, post_delete], sender=Chapter)
def clear_chapter_cache(sender, instance, **kwargs):
    manga_slug = instance.manga.slug
    cache.delete_pattern(f'chapter_{manga_slug}_*')
    cache.delete_pattern(f'all_chapters_{manga_slug}')
    cache.delete_pattern(f'prev_chapter_{manga_slug}_*')
    cache.delete_pattern(f'next_chapter_{manga_slug}_*')
    cache.delete_pattern(f'pages_{manga_slug}_*')
    cache.delete_pattern(f'chapter_read_{manga_slug}_*')

@receiver([post_save, post_delete], sender=ChapterPurchase)
def clear_purchase_cache(sender, instance, **kwargs):
    cache.delete(f'chapter_purchased_{instance.user.pk}_{instance.chapter.pk}')
    cache.delete(f'purchased_{instance.user.pk}_{instance.chapter.manga.slug}')

@receiver([post_save, post_delete], sender=ReadingProgress)
def clear_reading_progress_cache(sender, instance, **kwargs):
    cache.delete(f'user_read_{instance.user.pk}_{instance.manga.slug}')