# apps/manga/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.http import HttpResponseBadRequest, JsonResponse
from django.db.models import Prefetch
import os
from django.conf import settings

from manga.service import _is_translator, can_read
from .models import ChapterPurchase, ChapterVisit, Manga, Chapter, Genre, ReadingProgress, Tag
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

@login_required
@require_POST
def toggle_manga_like(request, slug):
    manga = get_object_or_404(Manga, slug=slug)

    # 1) Agar Manga.likes (M2M -> User) mavjud bo'lsa:
    if hasattr(manga, "likes"):
        if manga.likes.filter(pk=request.user.pk).exists():
            manga.likes.remove(request.user)
            liked = False
        else:
            manga.likes.add(request.user)
            liked = True
        likes_count = manga.likes.count()
        return JsonResponse({"success": True, "liked": liked, "likes_count": likes_count})

    # 2) Fallback: alohida MangaLike modeli bo'lsa
    try:
        from .models import MangaLike  # agar mavjud bo'lmasa ImportError bo'ladi
    except Exception:
        return JsonResponse({"success": False, "error": "Like modeli topilmadi"}, status=400)

    obj, created = MangaLike.objects.get_or_create(manga=manga, user=request.user)
    if created:
        liked = True
    else:
        obj.delete()
        liked = False
    likes_count = MangaLike.objects.filter(manga=manga).count()
    return JsonResponse({"success": True, "liked": liked, "likes_count": likes_count})

def get_cached_or_query(cache_key, queryset_func, timeout):
    """
    Helper function to get cached data or execute queryset and cache it
    """
    data = cache.get(cache_key)
    if not data:
        data = queryset_func()
        cache.set(cache_key, data, timeout)
    return data


# ====== Umumiy yordamchi funksiyalar (Discover + Browse uchun qayta ishlatiladi) ======

def _get_top_translators():
    return (
        UserProfile.objects
        .filter(is_translator=True)
        .annotate(
            manga_count    = Count("user__mangas_created", distinct=True),
            follower_count = Count("followers", distinct=True),
            likes_count    = Count("user__mangas_created__chapters__thanks", distinct=True),
        )
        .order_by("-likes_count", "-follower_count")[:4]
    )

def _get_trending_mangas():
    trending = (
        ReadingProgress.objects
        .values("manga")
        .annotate(readers=Count("user"))
        .order_by("-readers")[:25]
    )
    return list(Manga.objects.filter(id__in=[x["manga"] for x in trending]))

def _get_latest_mangas():
    return list(
        Manga.objects
        .annotate(chap_count=Count("chapters"))
        .filter(chap_count__gte=10)
        .order_by("-id")[:25]
    )

from django.db.models import Count, Max
from django.db import connection
from collections import defaultdict
# from datetime import timedelta
from django.utils import timezone

TOP_TRANSLATORS_KEY = "discover_top_translators_v1"
RECENT_FEED_KEY     = "discover_recent_feed_v1"

TOP_TRANSLATORS_TTL = 60 * 60 * 12   # 12 soat
RECENT_FEED_TTL     = 60 * 30        # 30 daqiqa

def _ago_uz(dt):
    """
    'N daqiqa/soat/kun oldin' ko‘rinishida bitta birlik qaytaradi.
    TZ-aware datetime bo‘lsa, lokal (Asia/Tashkent) ga o‘tkaziladi.
    """
    if not dt:
        return ""
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    now_local = timezone.localtime(timezone.now())
    delta = now_local - dt
    s = int(delta.total_seconds())

    if s < 60:
        return f"{s} soniya oldin"
    m = s // 60
    if m < 60:
        return f"{m} daqiqa oldin"
    h = m // 60
    if h < 24:
        return f"{h} soat oldin"
    d = h // 24
    if d < 30:
        return f"{d} kun oldin"
    mo = d // 30
    if mo < 12:
        return f"{mo} oy oldin"
    y = mo // 12
    return f"{y} yil oldin"


def manga_discover(request):
    """
    Trendlar, hozir o‘qilayotganlar, top tarjimonlar, yangi qo‘shilganlar (karusel),
    yangi qo‘shilgan boblar (har taytdan bitta), recent feed (har taytdan 3 ta),
    agar user kirgan bo‘lsa — 'Mening ro‘yxatim'.
    """

    # Profil (kerak bo‘lsa)
    if request.user.is_authenticated:
        UserProfile.objects.get_or_create(user=request.user)

    # ---------------- TOP TRANSLATORS (12 soat cache) ----------------------
    def _get_top_translators():
        return list(
            UserProfile.objects.filter(is_translator=True)
            .select_related("user")
            .annotate(
                manga_count=Count("user__mangas_created", distinct=True),
                follower_count=Count("followers", distinct=True),
                # translator yaratgan barcha mangalardagi like'lar yig'indisi (through=MangaLike)
                likes_count=Count("user__mangas_created__likes", distinct=True),
                # Agar denormal maydon bo'lsa: Coalesce(Sum("user__mangas_created__likes_count"), 0)
            )
            .order_by("-likes_count", "-follower_count")[:4]
        )

    top_translators = get_cached_or_query(
        TOP_TRANSLATORS_KEY, _get_top_translators, TOP_TRANSLATORS_TTL
    )

    # ---------------- TRENDING (1 soat cache) ------------------------------
    def _get_trending_mangas():
        trending = (
            ReadingProgress.objects
            .values("manga")
            .annotate(readers=Count("user", distinct=True))
            .order_by("-readers")[:25]
        )
        ids = [row["manga"] for row in trending]
        order = {mid: i for i, mid in enumerate(ids)}
        items = list(Manga.objects.filter(id__in=ids))
        items.sort(key=lambda m: order.get(m.id, 10**9))
        return items

    trending_mangas = get_cached_or_query(
        "discover_trending_mangas_v1", _get_trending_mangas, 60 * 60
    )

    # ---------------- LATEST TITLES (2 soat cache) -------------------------
    def _get_latest_mangas():
        return list(
            Manga.objects
            .annotate(chap_count=Count("chapters", distinct=True))
            .filter(chap_count__gte=1)
            .order_by("-id")[:16]
        )

    latest_mangas = get_cached_or_query(
        "discover_latest_mangas_carousel_v1", _get_latest_mangas, 60 * 60 * 2
    )

    # ---------------- HOZIR O‘QILAYOTGANLAR (unique by manga) --------------
    def _get_active_progress():
        since = timezone.now() - timedelta(hours=24)
        recent = (
            ReadingProgress.objects
            .filter(updated_at__gte=since)
            .values("manga")
            .annotate(last=Max("updated_at"))
            .order_by("-last")[:18]
        )
        manga_ids = [row["manga"] for row in recent]
        m_map = {m.id: m for m in Manga.objects.filter(id__in=manga_ids)}
        out = []
        for row in recent:
            m = m_map.get(row["manga"])
            if m:
                out.append({"manga": m, "updated_at": row["last"]})
        return out

    active_progress = get_cached_or_query(
        "discover_active_progress_24h_v1", _get_active_progress, 15 * 60
    )

    # ---------------- YANGI 15 TA QO‘SHILGAN BOB (unique by manga) --------
    def _get_latest_updates_unique():
        raw = (
            Chapter.objects
            .select_related("manga")
            .order_by("-release_date", "-volume", "-chapter_number", "-id")[:300]
        )
        seen, items = set(), []
        for ch in raw:
            mid = ch.manga_id
            if mid in seen:
                continue
            seen.add(mid)
            items.append({"manga": ch.manga, "chapter": ch})
            if len(items) >= 15:
                break
        return items

    latest_updates = get_cached_or_query(
        "discover_latest_updates_unique_v1", _get_latest_updates_unique, 60 * 30
    )

    # ---------------- RECENT FEED (yepadagi uslub) -------------------------
    def _build_recent_feed(limit_titles=10, per_title=3, window_hours=72):
        since = timezone.now() - timedelta(hours=window_hours)
        qs = (
            Chapter.objects.select_related("manga")
            .filter(release_date__gte=since)
            .order_by("-release_date", "-id")[:800]  # safety cap
        )

        feed_map = {}  # mid -> {"manga": m, "chapters":[{"obj": ch, "translators":[...] }], "last": dt, "total": int}
        for ch in qs:
            m = ch.manga
            if not m:
                continue

            box = feed_map.setdefault(
                m.id, {"manga": m, "chapters": [], "last": ch.release_date, "total": 0}
            )
            box["total"] += 1
            if ch.release_date and ch.release_date > box["last"]:
                box["last"] = ch.release_date

            if len(box["chapters"]) < per_title:
                translators = []
                try:
                    translators = list(ch.translators.all()[:3])  # mavjud bo'lsa
                except Exception:
                    pass
                box["chapters"].append({"obj": ch, "translators": translators})

        items = list(feed_map.values())
        for it in items:
            it["shown"] = len(it["chapters"])
            it["more"]  = max(0, it["total"] - it["shown"])
            it["ago"]   = _ago_uz(it["last"])  # <<< templateda foydalanasiz

        items.sort(key=lambda x: x["last"], reverse=True)
        return items[:limit_titles]

    recent_feed = cache.get(RECENT_FEED_KEY)
    if recent_feed is None:
        recent_feed = _build_recent_feed(limit_titles=10, per_title=3, window_hours=72)
        cache.set(RECENT_FEED_KEY, recent_feed, RECENT_FEED_TTL)

    # ---------------- Context -------------------------
    context = {
        "top_translators": top_translators,
        "trending_mangas": trending_mangas,
        "latest_mangas":   latest_mangas,
        "active_progress": active_progress,
        "latest_updates":  latest_updates,
        "recent_feed":     recent_feed,
    }
    return render(request, "manga/discover.html", context)

def manga_browse(request):
    """
    Barcha taytlar (grid) + qidiruv, filtrlar, sort va paginate.
    """

    # 1) Response-cache faqat anonim uchun (GET paramlar hammasi kiritiladi)
    cache_key = f"manga_browse:{request.user.is_authenticated}:{urlencode(request.GET, doseq=True)}"
    if not request.user.is_authenticated:
        cached = cache.get(cache_key)
        if cached:
            return cached

    # 2) UserProfile (foydalanuvchi statusini ko‘rsatish uchun)
    user_profile = None
    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # 3) Bazaviy queryset
    qs = Manga.objects.all()

    # 4) Qidiruv
    search_query = request.GET.get('search', '').strip()
    if search_query:
        qs = qs.filter(
            Q(title__icontains=search_query) |
            Q(author__icontains=search_query) |
            Q(description__icontains=search_query)
        ).distinct()

    # 5) Checkbox filtrlar
    filter_mappings = {
        'genre': ('genres__name', True),
        'age_rating': ('age_rating', False),
        'type': ('type', False),
        'tag': ('tags__name', True),
        'status': ('status', False),
        'translation_status': ('translation_status', False),
    }
    for param, (field, need_distinct) in filter_mappings.items():
        vals = request.GET.getlist(param)
        if vals:
            qs = qs.filter(**{f"{field}__in": vals})
            if need_distinct:
                qs = qs.distinct()

    # 6) Boblar soni oralig‘i
    min_chap = request.GET.get('min_chapters')
    max_chap = request.GET.get('max_chapters')
    if min_chap or max_chap:
        qs = qs.annotate(chap_count=Count('chapters', distinct=True))
        if min_chap:
            try:
                v = int(min_chap)
                if v > 0:
                    qs = qs.filter(chap_count__gte=v)
            except ValueError:
                pass
        if max_chap:
            try:
                v = int(max_chap)
                if v > 0:
                    qs = qs.filter(chap_count__lte=v)
            except ValueError:
                pass

    # 7) Noshirlik yili oralig‘i
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

    # 8) Sortlash
    sort = request.GET.get('sort', 'chapters')
    if sort == 'chapters':
        qs = qs.annotate(chap_count=Count('chapters', distinct=True)).order_by('-chap_count', 'title')
    elif sort == 'title_asc':
        qs = qs.order_by('title')
    elif sort == 'title_desc':
        qs = qs.order_by('-title')
    else:
        qs = qs.order_by('title')

    # 9) Prefetch: foydalanuvchi statusi
    if user_profile:
        qs = qs.prefetch_related(
            Prefetch(
                'readingstatus_set',
                queryset=ReadingStatus.objects.filter(user_profile=user_profile),
                to_attr='user_status'
            )
        )

    # 10) Paginatsiya
    paginator = Paginator(qs, 16)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Elided page range (… bilan qisqartirilgan raqamlar)
    elided_page_range = list(
        paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)
    )

    # 11) Choices (24 soat cache)
    def _choices(field): return Manga._meta.get_field(field).choices
    status_choices      = get_cached_or_query('choices_status',      lambda: _choices('status'),              60*60*24)
    age_rating_choices  = get_cached_or_query('choices_age_rating',  lambda: _choices('age_rating'),          60*60*24)
    type_choices        = get_cached_or_query('choices_type',        lambda: _choices('type'),                60*60*24)
    translation_choices = get_cached_or_query('choices_translation', lambda: _choices('translation_status'),  60*60*24)

    # 12) Janr va teglar (24 soat cache)
    genres = get_cached_or_query('all_genres', lambda: list(Genre.objects.all()), 60*60*24)
    tags   = get_cached_or_query('all_tags',   lambda: list(Tag.objects.all()),   60*60*24)

    # 13) Paginatsiya linklarida GET’larni saqlash (page’dan tashqari)
    qs_preserve = request.GET.copy()
    qs_preserve.pop('page', None)
    preserve_qs = qs_preserve.urlencode()

    context = {
        'genres': genres,
        'tags': tags,
        'page_obj': page_obj,
        'elided_page_range': elided_page_range,  # <-- template shu bilan ishlaydi
        'preserve_qs': preserve_qs,              # <-- "?{preserve}&page=N"
        'search': search_query,
        'sort': sort,

        # Tanlangan filtrlar (checkboxlar uchun)
        'genre_filter_list': request.GET.getlist('genre'),
        'tag_filter_list': request.GET.getlist('tag'),
        'age_rating_filter_list': request.GET.getlist('age_rating'),
        'type_filter_list': request.GET.getlist('type'),
        'status_filter_list': request.GET.getlist('status'),
        'translation_filter_list': request.GET.getlist('translation_status'),

        # Oraliqlar
        'min_chapters': request.GET.get('min_chapters', ''),
        'max_chapters': request.GET.get('max_chapters', ''),
        'min_year': request.GET.get('min_year', ''),
        'max_year': request.GET.get('max_year', ''),

        # Choices
        'status_choices': status_choices,
        'age_rating_choices': age_rating_choices,
        'type_choices': type_choices,
        'translation_choices': translation_choices,
    }

    response = render(request, 'manga/browse.html', context)

    # 14) To‘liq response-cache (anonim) — 15 daqiqa
    if not request.user.is_authenticated:
        cache.set(cache_key, response, 60*15)

    return response

# ====== детали манги ========================================================
def manga_details(request, manga_slug):
    """
    Detail view (cache-siz).
    - Like holati/soni (Manga.likes M2M orqali)
    - Foydalanuvchining oxirgi ochgan joyi (faqat bitta 'current' bob)
    - 'O‘qilgan' belgisi faqat ChapterVisit bor bo‘lsa
    - Ko‘rinadigan ruxsat: can_read() orqali (free / muallif / xarid qilingan)
    """
    # --- Base ---------------------------------------------------------------
    order = request.GET.get("order", "desc").lower()
    manga = get_object_or_404(
        Manga.objects.select_related("created_by").prefetch_related("genres", "tags", "telegram_links"),
        slug=manga_slug
    )

    # --- User-specific ------------------------------------------------------
    reading_status = None
    is_liked = False
    likes_count = manga.likes.count()
    like_toggle_url = None

    reading_progress = None
    progress_current_chapter_id = None
    progress_current_page = None
    visited_chapter_ids = []

    if request.user.is_authenticated:
        # Profile mavjud bo'lsin (statuslar uchun)
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

        # Reading status (planned/reading/completed/favorite)
        reading_status = (
            ReadingStatus.objects
            .filter(user_profile=user_profile, manga=manga)
            .select_related("user_profile", "manga")
            .first()
        )

        # Likes (foydalanuvchi like bosganmi)
        is_liked = manga.likes.filter(pk=request.user.pk).exists()
        # Toggle URL bo'lmasa sahifa yiqilmasin:
        try:
            like_toggle_url = reverse("manga:manga_like_toggle", kwargs={"slug": manga.slug})
        except Exception:
            like_toggle_url = None

        # Progress (resume tugmasi uchun)
        reading_progress = (
            ReadingProgress.objects
            .filter(user=request.user, manga=manga)
            .select_related("last_read_chapter")
            .first()
        )
        if reading_progress and reading_progress.last_read_chapter_id:
            progress_current_chapter_id = reading_progress.last_read_chapter_id
            progress_current_page = reading_progress.last_read_page

        # Visited chapters (o‘qilganlar)
        visited_chapter_ids = list(
            ChapterVisit.objects
            .filter(user=request.user, chapter__manga=manga)
            .values_list("chapter_id", flat=True)
        )

    # --- Chapters (ordering) ------------------------------------------------
    if order == "asc":
        chapters_qs = manga.chapters.order_by("volume", "chapter_number")
    else:
        chapters_qs = manga.chapters.order_by("-volume", "-chapter_number")

    chapters = []
    for ch in chapters_qs:
        # Markaziy ruxsat siyosati
        ch.can_read = can_read(request.user, manga, ch)

        # Faqat bitta bobni current (resume)
        ch.is_current = (progress_current_chapter_id == ch.id)
        ch.current_page = progress_current_page if ch.is_current else None

        # O‘qilgan belgisi (Visit mavjud bo'lsa)
        ch.is_visited = (ch.id in visited_chapter_ids)

        chapters.append(ch)

    # Eng birinchi bob (boshlash uchun)
    first_chapter = (
        manga.chapters.order_by("volume", "chapter_number").first()
    )

    # Start/Continue tugma maqsadi va label’i (templatega tayyor)
    start_button_url = None
    start_button_label = "O'qishni boshlash"
    if progress_current_chapter_id:
        # resume bobini topamiz (chapters ichidan)
        resume = next((c for c in chapters if c.id == progress_current_chapter_id), None)
        if resume:
            start_button_url = reverse(
                "manga:chapter_read",
                kwargs={"manga_slug": manga.slug, "volume": resume.volume, "chapter_number": resume.chapter_number}
            )
            start_button_label = f"Davom ettirish (Bob {resume.chapter_number})"
    elif first_chapter:
        start_button_url = reverse(
            "manga:chapter_read",
            kwargs={"manga_slug": manga.slug, "volume": first_chapter.volume, "chapter_number": first_chapter.chapter_number}
        )
        start_button_label = "O'qishni boshlash"

    # --- Similar mangas (janr bo‘yicha) ------------------------------------
    user_genres = manga.genres.all()
    similar_mangas = (
        Manga.objects.exclude(pk=manga.pk)
        .annotate(shared_genres=Count("genres", filter=Q(genres__in=user_genres), distinct=True))
        .filter(shared_genres__gt=0)
        .order_by("-shared_genres", "title")[:10]
    )

    # --- Telegram & translator ---------------------------------------------
    telegram_links = list(manga.telegram_links.all())
    translator_profile = getattr(manga.created_by, "userprofile", None) if manga.created_by else None

    # --- Context ------------------------------------------------------------
    context = {
        "manga": manga,

        # reading list/status
        "reading_status": reading_status,
        "READING_STATUSES": READING_STATUSES,

        # chapters / order
        "chapters": chapters,
        "first_chapter": first_chapter,
        "current_order": order,

        # visited + progress (template “O‘qilgan/Kelgan” belgisi uchun)
        "visited_chapter_ids": visited_chapter_ids,
        "progress_current_chapter_id": progress_current_chapter_id,
        "progress_current_page": progress_current_page,

        # Start/Continue action (templateda to‘g‘ridan-to‘g‘ri ishlating)
        "start_button_url": start_button_url,
        "start_button_label": start_button_label,

        # likes
        "is_liked": is_liked,
        "likes_count": likes_count,
        "like_toggle_url": like_toggle_url,

        # extra blocks
        "similar_mangas": similar_mangas,
        "telegram_links": telegram_links,
        "translator_profile": translator_profile,
    }
    return render(request, "manga/manga_details.html", context)

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
    # 1) Manga va Chapter
    manga = get_object_or_404(Manga, slug=manga_slug)
    chapter = get_object_or_404(
        Chapter, manga=manga, volume=volume, chapter_number=chapter_number
    )

    # 2) Ruxsat: markaziy siyosat
    #    - Free (price_tanga == 0) — ruxsat
    #    - Muallif / superuser / staff / tarjimon — ruxsat
    #    - Xarid qilingan bo‘lsa — ruxsat
    #    - Aks holda: guest -> login, auth -> purchase
    def can_read(user, manga, ch) -> bool:
        if ch.price_tanga == 0:
            return True
        if not user.is_authenticated:
            return False
        if (
            user.is_superuser
            or user.is_staff
            or manga.created_by_id == getattr(user, "id", None)
            or _is_translator(user)
        ):
            return True
        return ChapterPurchase.objects.filter(user=user, chapter=ch).exists()

    if not can_read(request.user, manga, chapter):
        if not request.user.is_authenticated:
            messages.warning(request, "Bobni o‘qish uchun tizimga kiring!")
            return redirect("login")
        messages.warning(
            request,
            f"Ushbu bob {chapter.price_tanga} tanga turadi. Avval sotib oling."
        )
        return redirect(
            "manga:purchase_chapter",
            manga_slug=manga.slug,
            volume=chapter.volume,
            chapter_number=chapter.chapter_number,
        )

    # 3) Navigatsiya uchun barcha boblar (dropdown)
    all_chapters = list(
        Chapter.objects.filter(manga=manga).order_by('-volume', '-chapter_number')
    )

    # 4) Oldingi / Keyingi boblar
    previous_chapter = (
        Chapter.objects
               .filter(manga=manga)
               .filter(
                   Q(volume=chapter.volume, chapter_number__lt=chapter.chapter_number) |
                   Q(volume__lt=chapter.volume)
               )
               .order_by('-volume', '-chapter_number')
               .first()
    )

    next_chapter = (
        Chapter.objects
               .filter(manga=manga, volume=chapter.volume, chapter_number__gt=chapter.chapter_number)
               .order_by('chapter_number')
               .first()
        or
        Chapter.objects
               .filter(manga=manga, volume__gt=chapter.volume)
               .order_by('volume', 'chapter_number')
               .first()
    )

    # 5) Keyingi bob pullik bo‘lsa — modalda ko‘rsatish uchun narx (faqat auth)
    next_chapter_price = None
    if request.user.is_authenticated and next_chapter:
        if next_chapter.price_tanga > 0 and not can_read(request.user, manga, next_chapter):
            next_chapter_price = next_chapter.price_tanga

    # 6) Visit + Progress (faqat oldinga)
    if request.user.is_authenticated:
        ChapterVisit.objects.get_or_create(user=request.user, chapter=chapter)

        progress, created = ReadingProgress.objects.get_or_create(
            user=request.user, manga=manga,
            defaults={'last_read_chapter': chapter, 'last_read_page': 1}
        )
        if not created:
            prev = progress.last_read_chapter
            if prev is None or (chapter.volume, chapter.chapter_number) > (prev.volume, prev.chapter_number):
                progress.last_read_chapter = chapter
                progress.last_read_page = 1
            progress.save(update_fields=['last_read_chapter', 'last_read_page'])
    else:
        progress = None

    # 7) Foydalanuvchi ochgan (visited) boblar ID’lari
    user_read_chapters = []
    if request.user.is_authenticated:
        user_read_chapters = list(
            ChapterVisit.objects
                        .filter(user=request.user, chapter__manga=manga)
                        .values_list('chapter_id', flat=True)
        )

    # 8) Sahifalar
    pages = list(chapter.pages.all().order_by('page_number'))

    # 9) Sotib olingan boblar (faqat auth)
    purchased_chapters = []
    if request.user.is_authenticated:
        purchased_chapters = list(
            ChapterPurchase.objects
                           .filter(user=request.user, chapter__manga=manga)
                           .values_list('chapter_id', flat=True)
        )

    # 9.1) O‘qiy oladigan boblar (LOCK chiqmasligi kerak bo‘lganlar)
    if request.user.is_authenticated:
        is_privileged = (
            request.user.is_superuser
            or request.user.is_staff
            or manga.created_by_id == request.user.id
            or _is_translator(request.user)
        )
    else:
        is_privileged = False

    if is_privileged:
        readable_chapter_ids = [c.id for c in all_chapters]
    else:
        free_ids = [c.id for c in all_chapters if c.price_tanga == 0]
        # auth bo‘lsa purchased_chapters bilan birga, guest bo‘lsa faqat free
        readable_chapter_ids = list(set(free_ids) | set(purchased_chapters))

    # 10) Oxirgi bobmi?
    is_last_chapter = (next_chapter is None)

    # 11) Render
    context = {
        'manga': manga,
        'chapter': chapter,
        'all_chapters': all_chapters,
        'previous_chapter': previous_chapter,
        'next_chapter': next_chapter,
        'next_chapter_price': next_chapter_price,
        'reading_progress': progress,
        'user_read_chapters': user_read_chapters,   # visited IDs
        'pages': pages,
        'purchased_chapters': purchased_chapters,
        'readable_chapter_ids': readable_chapter_ids,  # 👈 LOCK tekshiruvi uchun
        'is_last_chapter': is_last_chapter,
    }
    return render(request, 'manga/chapter_read.html', context)
