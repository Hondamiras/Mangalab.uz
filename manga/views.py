# apps/manga/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.db.models import Prefetch
import os
from django.conf import settings
from .models import ChapterPurchase, Manga, Chapter, Genre, ReadingProgress, Tag
from accounts.models import ReadingStatus, UserProfile, READING_STATUSES
from django.db.models import Count, Sum
from django.utils.timezone import now, timedelta


# ====== списки ==============================================================

from django.core.cache import cache
from django.utils.http import urlencode
from django.db.models import Q, Count, Sum, Prefetch
from django.shortcuts import render
from django.utils.timezone import now, timedelta
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import cache_page

def get_cached_or_query(cache_key, queryset_func, timeout):
    """
    Helper function to get cached data or execute queryset and cache it
    """
    data = cache.get(cache_key)
    if not data:
        data = queryset_func()
        cache.set(cache_key, data, timeout)
    return data

def manga_list(request):
    # 1) Generate cache key based on request parameters and user auth status
    cache_key = f"manga_list_{urlencode(request.GET)}_{request.user.is_authenticated}"
    
    # For anonymous users, try to get cached response
    if not request.user.is_authenticated:
        cached_response = cache.get(cache_key)
        if cached_response:
            return cached_response

    # 1) Get user profile
    user_profile = None
    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # 2) Base queryset with caching for common parts
    def get_base_queryset():
        return Manga.objects.all()

    qs = get_cached_or_query('base_manga_queryset', get_base_queryset, 60*60)  # Cache for 1 hour

    # 0) Search by keyword
    search_query = request.GET.get('search', '').strip()
    if search_query:
        qs = qs.filter(
            Q(title__icontains=search_query) |
            Q(author__icontains=search_query) |
            Q(description__icontains=search_query)
        ).distinct()

    # 1) Checkbox filters
    filter_mappings = {
        'genre': ('genres__name', True),
        'age_rating': ('age_rating', False),
        'type': ('type', False),
        'tag': ('tags__name', True),
        'status': ('status', False),
        'translation_status': ('translation_status', False),
    }

    for param, (field, distinct) in filter_mappings.items():
        filter_list = request.GET.getlist(param)
        if filter_list:
            filter_kwargs = {f"{field}__in": filter_list}
            qs = qs.filter(**filter_kwargs)
            if distinct:
                qs = qs.distinct()

    # 2) Chapter count range
    min_chap = request.GET.get('min_chapters')
    max_chap = request.GET.get('max_chapters')
    if min_chap or max_chap:
        qs = qs.annotate(chap_count=Count('chapters'))
        if min_chap:
            try:
                iv = int(min_chap)
                if iv > 0:
                    qs = qs.filter(chap_count__gte=iv)
            except ValueError:
                pass
        if max_chap:
            try:
                iv = int(max_chap)
                if iv > 0:
                    qs = qs.filter(chap_count__lte=iv)
            except ValueError:
                pass

    # 3) Publication year range
    min_year = request.GET.get('min_year')
    max_year = request.GET.get('max_year')
    if min_year:
        try:
            iy = int(min_year)
            if iy >= 1:
                qs = qs.filter(publication_date__year__gte=iy)
        except ValueError:
            pass
    if max_year:
        try:
            iy = int(max_year)
            if iy >= 1:
                qs = qs.filter(publication_date__year__lte=iy)
        except ValueError:
            pass

    # 4) Sorting
    sort = request.GET.get('sort', 'chapters')
    if sort == 'chapters':
        if not qs.query.annotations.get('chap_count'):
            qs = qs.annotate(chap_count=Count('chapters'))
        qs = qs.order_by('-chap_count', 'title')
    elif sort == 'title_asc':
        qs = qs.order_by('title')
    elif sort == 'title_desc':
        qs = qs.order_by('-title')
    else:
        qs = qs.order_by('title')

    # 5) Load ReadingStatus for current user
    if user_profile:
        qs = qs.prefetch_related(
            Prefetch(
                'readingstatus_set',
                queryset=ReadingStatus.objects.filter(user_profile=user_profile),
                to_attr='user_status'
            )
        )

    # 6) Pagination
    paginator = Paginator(qs, 16)
    page_obj = paginator.get_page(request.GET.get('page'))

    # 7) Prepare template references with caching
    def get_choices(field_name):
        return Manga._meta.get_field(field_name).choices

    status_choices = get_cached_or_query(
        'status_choices',
        lambda: get_choices('status'),
        60*60*24
    )
    
    age_rating_choices = get_cached_or_query(
        'age_rating_choices',
        lambda: get_choices('age_rating'),
        60*60*24
    )
    
    type_choices = get_cached_or_query(
        'type_choices',
        lambda: get_choices('type'),
        60*60*24
    )
    
    translation_choices = get_cached_or_query(
        'translation_choices',
        lambda: get_choices('translation_status'),
        60*60*24
    )

    # Cache top translators
    def get_top_translators():
        return (
            UserProfile.objects
            .filter(is_translator=True)
            .annotate(
                manga_count   = Count("user__mangas_created", distinct=True),
                follower_count= Count("followers",         distinct=True),
                likes_count   = Count("user__mangas_created__chapters__thanks", distinct=True),
            )
            .order_by("-likes_count", "-follower_count")[:4]
        )

    top_translators = get_cached_or_query(
        'top_translators',
        get_top_translators,
        60*60*12  # 12 hours
    )

    # Currently reading (last 24 hours) - don't cache as it's highly dynamic
    active_progress = (
        ReadingProgress.objects
        .filter(updated_at__gte=now()-timedelta(hours=24))
        .select_related("manga")
        .order_by("-updated_at")[:6]
    )

    # Trending mangas - cache for 1 hour
    def get_trending_mangas():
        trending = (
            ReadingProgress.objects
            .values("manga")
            .annotate(readers=Count("user"))
            .order_by("-readers")[:25]
        )
        return list(Manga.objects.filter(id__in=[x["manga"] for x in trending]))

    trending_mangas = get_cached_or_query(
        'trending_mangas',
        get_trending_mangas,
        60*60  # 1 hour
    )

    # Latest added titles (with >=10 chapters) - cache for 6 hours
    def get_latest_mangas():
        return list(
            Manga.objects.annotate(chap_count=Count("chapters"))
            .filter(chap_count__gte=10)
            .order_by("-id")[:10]
        )

    latest_mangas = get_cached_or_query(
        'latest_mangas',
        get_latest_mangas,
        60*60*6  # 6 hours
    )

    # Get genres and tags with caching
    def get_all_genres():
        return list(Genre.objects.all())

    def get_all_tags():
        return list(Tag.objects.all())

    genres = get_cached_or_query('all_genres', get_all_genres, 60*60*24)
    tags = get_cached_or_query('all_tags', get_all_tags, 60*60*24)

    context = {
        'genres': genres,
        'tags': tags,
        'page_obj': page_obj,
        'search': search_query,
        'sort': sort,

        # Selected filters
        'genre_filter_list': request.GET.getlist('genre'),
        'tag_filter_list': request.GET.getlist('tag'),
        'age_rating_filter_list': request.GET.getlist('age_rating'),
        'type_filter_list': request.GET.getlist('type'),
        'status_filter_list': request.GET.getlist('status'),
        'translation_filter_list': request.GET.getlist('translation_status'),

        # Ranges
        'min_chapters': request.GET.get('min_chapters', ''),
        'max_chapters': request.GET.get('max_chapters', ''),
        'min_year': request.GET.get('min_year', ''),
        'max_year': request.GET.get('max_year', ''),

        # Choices for checkboxes
        'status_choices': status_choices,
        'age_rating_choices': age_rating_choices,
        'type_choices': type_choices,
        'translation_choices': translation_choices,

        "top_translators": top_translators,
        "active_progress": active_progress,
        "trending_mangas": trending_mangas,
        "latest_mangas": latest_mangas,
    }

    response = render(request, 'manga/manga_list.html', context)
    
    # Cache full response only for anonymous users
    if not request.user.is_authenticated:
        cache.set(cache_key, response, 60*15)  # Cache for 15 minutes
        
    return response

# ====== детали манги ========================================================
def manga_details(request, manga_slug):
    # Generate cache key based on slug, user auth status and order param
    order = request.GET.get('order', 'desc')
    cache_key = f"manga_details_{manga_slug}_{order}_{request.user.is_authenticated}"
    
    # Try to get cached response for anonymous users
    if not request.user.is_authenticated:
        cached_response = cache.get(cache_key)
        if cached_response:
            return cached_response

    # Get manga with basic caching
    manga = get_cached_or_query(
        f'manga_obj_{manga_slug}',
        lambda: get_object_or_404(Manga, slug=manga_slug),
        60 * 60 * 24  # 24 hours
    )

    # --- User data ---
    reading_status = None
    purchased_chapter_ids = []
    if request.user.is_authenticated:
        user_profile = get_cached_or_query(
            f'user_profile_{request.user.pk}',
            lambda: UserProfile.objects.get_or_create(user=request.user)[0],
            60 * 60 * 12  # 12 hours
        )
        
        reading_status = get_cached_or_query(
            f'reading_status_{user_profile.pk}_{manga.pk}',
            lambda: ReadingStatus.objects.filter(
                user_profile=user_profile, 
                manga=manga
            ).first(),
            60 * 60 * 6  # 6 hours
        )
        
        purchased_chapter_ids = get_cached_or_query(
            f'purchased_chapters_{request.user.pk}_{manga.pk}',
            lambda: list(
                ChapterPurchase.objects.filter(
                    user=request.user, 
                    chapter__manga=manga
                ).values_list("chapter_id", flat=True)
            ),
            60 * 60 * 3  # 3 hours
        )

    # --- Chapters ---
    def get_chapters():
        if order == 'asc':
            return manga.chapters.order_by('volume', 'chapter_number')
        return manga.chapters.order_by('-volume', '-chapter_number')
    
    chapters = get_cached_or_query(
        f'chapters_{manga.pk}_{order}',
        get_chapters,
        60 * 60 * 12  # 12 hours
    )

    # Add access flags to chapters
    for ch in chapters:
        if request.user.is_authenticated and ch.created_by == request.user:
            ch.can_read = True
        else:
            ch.can_read = (ch.price_tanga == 0 or ch.id in purchased_chapter_ids)

    # --- First chapter ---
    first_chapter = get_cached_or_query(
        f'first_chapter_{manga.pk}',
        lambda: manga.chapters.order_by('volume', 'chapter_number').first(),
        60 * 60 * 24  # 24 hours
    )

    # --- Similar mangas ---
    def get_similar_mangas():
        user_genres = manga.genres.all()
        return (
            Manga.objects.exclude(pk=manga.pk)
            .annotate(shared_genres=Count('genres', filter=Q(genres__in=user_genres), distinct=True))
            .filter(shared_genres__gt=0)
            .order_by('-shared_genres', 'title')[:10]
        )
    
    similar_mangas = get_cached_or_query(
        f'similar_mangas_{manga.pk}',
        get_similar_mangas,
        60 * 60 * 24  # 24 hours
    )

    # --- Telegram links ---
    telegram_links = get_cached_or_query(
        f'telegram_links_{manga.pk}',
        lambda: list(manga.telegram_links.all()),
        60 * 60 * 24  # 24 hours
    )

    # --- Translator profile ---
    translator_profile = None
    if manga.created_by:
        translator_profile = get_cached_or_query(
            f'translator_profile_{manga.created_by.pk}',
            lambda: getattr(manga.created_by, "userprofile", None),
            60 * 60 * 24  # 24 hours
        )

    context = {
        'manga': manga,
        'reading_status': reading_status,
        'first_chapter': first_chapter,
        'chapters': chapters,
        'current_order': order,
        'similar_mangas': similar_mangas,
        'READING_STATUSES': READING_STATUSES,
        'telegram_links': telegram_links,
        'translator_profile': translator_profile,
    }

    response = render(request, 'manga/manga_details.html', context)
    
    # Cache full response only for anonymous users
    if not request.user.is_authenticated:
        cache.set(cache_key, response, 60 * 15)  # 15 minutes
        
    return response

# ====== добавление в список чтения ==========================================
@login_required
def add_to_reading_list(request, manga_slug):
    """
    Добавляет или обновляет статус чтения манги для текущего пользователя.
    """
    manga = get_object_or_404(Manga, slug=manga_slug)
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Берём статус из формы (по умолчанию 'planned')
    status = request.POST.get('status', 'planned')

    if status == 'remove':
        # удаляем, если есть
        ReadingStatus.objects.filter(
            user_profile=user_profile,
            manga=manga
        ).delete()
    else:
        # создаём или обновляем
        ReadingStatus.objects.update_or_create(
            user_profile=user_profile,
            manga=manga,
            defaults={'status': status or 'planned'}
        )

    return redirect('manga:manga_details', manga_slug=manga.slug)

# ====== чтение главы ========================================================
from django.contrib import messages

# def chapter_read(request, manga_slug, volume, chapter_number):
#     manga = get_object_or_404(Manga, slug=manga_slug)
#     chapter = get_object_or_404(
#         Chapter,
#         manga=manga,
#         volume=volume,
#         chapter_number=chapter_number
#     )

#     # ======== Pullik boblar uchun tekshiruv =========
#     if chapter.price_tanga > 0:  # bob pullik bo'lsa
#         if not request.user.is_authenticated:
#             messages.warning(request, "Bobni o‘qish uchun tizimga kiring!")
#             return redirect("login")
#         # Sotib olinganmi?
#         is_purchased = ChapterPurchase.objects.filter(user=request.user, chapter=chapter).exists()
#         if not is_purchased:
#             messages.warning(request, f"Ushbu bob {chapter.price_tanga} tanga turadi. Avval sotib oling.")
#             return redirect("manga:purchase_chapter",
#                             manga_slug=manga.slug,
#                             volume=chapter.volume,
#                             chapter_number=chapter.chapter_number)
#     # ================================================

#     all_chapters = Chapter.objects.filter(manga=manga).order_by('-volume', '-chapter_number')

#     previous_chapter = all_chapters.filter(
#         Q(volume=chapter.volume, chapter_number__lt=chapter.chapter_number) |
#         Q(volume__lt=chapter.volume)
#     ).order_by('-volume', '-chapter_number').first()

#     next_chapter = all_chapters.filter(
#         Q(volume=chapter.volume, chapter_number__gt=chapter.chapter_number) |
#         Q(volume__gt=chapter.volume)
#     ).order_by('volume', 'chapter_number').first()

#     progress = None
#     user_read_chapters = []
#     if request.user.is_authenticated:
#         progress, created = ReadingProgress.objects.get_or_create(
#             user=request.user,
#             manga=manga,
#             defaults={'last_read_chapter': chapter, 'last_read_page': 1}
#         )

#         # Yangi bob eski bobdan katta bo‘lsa, yangilash
#         if not created and progress.last_read_chapter:
#             is_newer = (
#                 chapter.volume > progress.last_read_chapter.volume or
#                 (chapter.volume == progress.last_read_chapter.volume and
#                  chapter.chapter_number > progress.last_read_chapter.chapter_number)
#             )
#             if is_newer:
#                 progress.last_read_chapter = chapter
#                 progress.last_read_page = 1
#                 progress.save()

#         # O‘qilgan boblar ro‘yxati
#         user_read_chapters = list(
#             ReadingProgress.objects.filter(user=request.user, manga=manga)
#             .exclude(last_read_chapter=None)
#             .values_list('last_read_chapter__pk', flat=True)
#         )

#     pages = chapter.pages.all().order_by('page_number')

#     return render(request, 'manga/chapter_read.html', {
#         'manga': manga,
#         'chapter': chapter,
#         'all_chapters': all_chapters,
#         'previous_chapter': previous_chapter,
#         'next_chapter': next_chapter,
#         'reading_progress': progress,
#         'user_read_chapters': user_read_chapters,
#         'pages': pages,
#     })

# ====== спасибо главе ========================================================
@login_required
def thank_chapter(request, chapter_id):
    chapter = get_object_or_404(Chapter, id=chapter_id)
    user = request.user

    # Toggle
    if user in chapter.thanks.all():
        chapter.thanks.remove(user)
        thanked = False
    else:
        chapter.thanks.add(user)
        thanked = True

    # Новый счётчик
    count = chapter.thanks.count()

    # Если AJAX, вернём JSON
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'thanked': thanked,
            'count': count,
        })

    # Иначе — редирект как раньше
    return redirect('manga:chapter_read', chapter_id=chapter.id)

def chapter_read(request, manga_slug, volume, chapter_number):
    # Generate cache key based on chapter and user auth status
    cache_key = f"chapter_read_{manga_slug}_{volume}_{chapter_number}_{request.user.is_authenticated}"
    
    # For anonymous users, try to get cached response
    if not request.user.is_authenticated:
        cached_response = cache.get(cache_key)
        if cached_response:
            return cached_response

    # Get manga with basic caching
    manga = get_object_or_404(Manga, slug=manga_slug)
    
    # Get chapter with caching
    chapter = get_cached_or_query(
        f'chapter_{manga_slug}_{volume}_{chapter_number}',
        lambda: get_object_or_404(Chapter, manga=manga, volume=volume, chapter_number=chapter_number),
        60 * 60 * 24  # 24 hours
    )

    # === Paid chapter check ===
    if chapter.price_tanga > 0:
        if not request.user.is_authenticated:
            messages.warning(request, "Bobni o'qish uchun tizimga kiring!")
            return redirect("login")

        if chapter.created_by != request.user:
            is_purchased = get_cached_or_query(
                f'chapter_purchased_{request.user.pk}_{chapter.pk}',
                lambda: ChapterPurchase.objects.filter(
                    user=request.user, 
                    chapter=chapter
                ).exists(),
                60 * 60 * 3  # 3 hours
            )
            if not is_purchased:
                return redirect("manga:purchase_chapter",
                              manga_slug=manga.slug,
                              volume=chapter.volume,
                              chapter_number=chapter.chapter_number)

    # === All chapters list ===
    all_chapters = get_cached_or_query(
        f'all_chapters_{manga_slug}',
        lambda: list(Chapter.objects.filter(manga=manga).order_by('-volume', '-chapter_number')),
        60 * 60 * 12  # 12 hours
    )

    # === Previous chapter ===
    previous_chapter = get_cached_or_query(
        f'prev_chapter_{manga_slug}_{volume}_{chapter_number}',
        lambda: Chapter.objects.filter(
            manga=manga
        ).filter(
            Q(volume=chapter.volume, chapter_number__lt=chapter.chapter_number) |
            Q(volume__lt=chapter.volume)
        ).order_by('-volume', '-chapter_number').first(),
        60 * 60 * 24  # 24 hours
    )

    # === Next chapter ===
    next_chapter = get_cached_or_query(
        f'next_chapter_{manga_slug}_{volume}_{chapter_number}',
        lambda: (
            Chapter.objects.filter(
                manga=manga,
                volume=chapter.volume,
                chapter_number__gt=chapter.chapter_number
            ).order_by('chapter_number').first() or
            Chapter.objects.filter(
                manga=manga,
                volume__gt=chapter.volume
            ).order_by('volume', 'chapter_number').first()
        ),
        60 * 60 * 24  # 24 hours
    )

    # === Next chapter price ===
    next_chapter_price = None
    if next_chapter and next_chapter.price_tanga > 0:
        if request.user.is_authenticated and next_chapter.created_by != request.user:
            is_purchased = get_cached_or_query(
                f'chapter_purchased_{request.user.pk}_{next_chapter.pk}',
                lambda: ChapterPurchase.objects.filter(
                    user=request.user, 
                    chapter=next_chapter
                ).exists(),
                60 * 60 * 3  # 3 hours
            )
            if not is_purchased:
                next_chapter_price = next_chapter.price_tanga

    # === Reading progress ===
    progress = None
    user_read_chapters = []
    
    if request.user.is_authenticated:
        # Don't cache progress as it's user-specific
        progress, created = ReadingProgress.objects.get_or_create(
            user=request.user,
            manga=manga,
            defaults={'last_read_chapter': chapter, 'last_read_page': 1}
        )

        if not created and progress.last_read_chapter:
            is_newer = (
                chapter.volume > progress.last_read_chapter.volume or
                (chapter.volume == progress.last_read_chapter.volume and
                 chapter.chapter_number > progress.last_read_chapter.chapter_number)
            )
            if is_newer:
                progress.last_read_chapter = chapter
                progress.last_read_page = 1
                progress.save()

        # Read chapters list (cache per user)
        user_read_chapters = get_cached_or_query(
            f'user_read_{request.user.pk}_{manga_slug}',
            lambda: list(
                Chapter.objects.filter(manga=manga).filter(
                    Q(volume__lt=progress.last_read_chapter.volume) |
                    Q(volume=progress.last_read_chapter.volume,
                      chapter_number__lte=progress.last_read_chapter.chapter_number)
                ).values_list('id', flat=True)
            ),
            60 * 60 * 6  # 6 hours
        )

    # === Pages ===
    pages = get_cached_or_query(
        f'pages_{manga_slug}_{volume}_{chapter_number}',
        lambda: list(chapter.pages.all().order_by('page_number')),
        60 * 60 * 24  # 24 hours
    )

    # === Purchased chapters ===
    purchased_chapters = []
    if request.user.is_authenticated:
        purchased_chapters = get_cached_or_query(
            f'purchased_{request.user.pk}_{manga_slug}',
            lambda: list(
                ChapterPurchase.objects.filter(
                    user=request.user,
                    chapter__manga=manga
                ).values_list("chapter_id", flat=True)
            ),
            60 * 60 * 3  # 3 hours
        )

    is_last_chapter = not next_chapter

    context = {
        'manga': manga,
        'chapter': chapter,
        'all_chapters': all_chapters,
        'previous_chapter': previous_chapter,
        'next_chapter': next_chapter,
        'next_chapter_price': next_chapter_price,
        'reading_progress': progress,
        'user_read_chapters': user_read_chapters,
        'pages': pages,
        'purchased_chapters': purchased_chapters,
        'is_last_chapter': is_last_chapter,
    }

    response = render(request, 'manga/chapter_read.html', context)
    
    # Cache full response only for anonymous users
    if not request.user.is_authenticated:
        cache.set(cache_key, response, 60 * 15)  # 15 minutes
        
    return response


