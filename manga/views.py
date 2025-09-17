# apps/manga/views.py
import io
import random
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count, F
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.db.models import Prefetch
import os
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.conf import settings
from django.http import FileResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET
from PIL import Image, ImageDraw, ImageFont
from manga.utils import cf_should_hide_manga, cf_filter_list
from manga.service import _is_translator, can_read
from .models import ChapterPurchase, ChapterVisit, Manga, Chapter, Genre, Page, ReadingProgress, Tag, make_search_key
from accounts.models import ReadingStatus, UserProfile, READING_STATUSES
from django.db.models import Count, Sum
from django.utils.timezone import now, timedelta

signer = TimestampSigner(salt="page-image-v2")

def _subject_for(request) -> str:
    if request.user.is_authenticated:
        return str(request.user.id)
    if not request.session.session_key:
        request.session.save()
    return str(request.session.session_key)

def make_page_token(request, page_id: int) -> str:
    return signer.sign(f"{_subject_for(request)}:{page_id}")

def can_read_chapter(user, manga, ch) -> bool:
    if ch.price_tanga == 0:
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    prof = getattr(user, "userprofile", None)
    is_translator = bool(prof and getattr(prof, "is_translator", False))
    if user.is_superuser or user.is_staff or manga.created_by_id == getattr(user, "id", None) or is_translator:
        return True
    return ChapterPurchase.objects.filter(user=user, chapter=ch).exists()

@require_GET
def page_image(request, page_id: int, token: str):
    # 1) Token
    try:
        payload = signer.unsign(token, max_age=600)  # 10 min
        subject, pid = payload.split(":")
        if str(page_id) != pid:
            raise BadSignature
    except (SignatureExpired, BadSignature):
        return HttpResponseForbidden("Invalid or expired")

    # 2) Token egasi
    if subject != _subject_for(request):
        return HttpResponseForbidden("Forbidden")

    # 3) Ruxsat
    page = get_object_or_404(Page, id=page_id)
    ch = page.chapter
    if not can_read_chapter(request.user, ch.manga, ch):
        return HttpResponseForbidden("No access")

    # 4) Dev/Prod bo‘yicha berish
    if getattr(settings, "USE_X_ACCEL_REDIRECT", False):
        # PROD: Nginx orqali
        prefix = getattr(settings, "X_ACCEL_REDIRECT_PREFIX", "/_protected/").rstrip("/")
        internal_path = f"{prefix}/{page.image.name}"
        resp = HttpResponse()
        resp["X-Accel-Redirect"] = internal_path
        resp["Content-Type"] = "image/webp"
        resp["Cache-Control"] = "private, max-age=120, stale-while-revalidate=30"
        resp["X-Frame-Options"] = "DENY"
        resp["Referrer-Policy"] = "no-referrer"
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Cross-Origin-Resource-Policy"] = "same-origin"
        return resp
    else:
        # DEV/LOCALHOST: to‘g‘ridan-to‘g‘ri oqim
        f = page.image.open("rb")
        resp = FileResponse(f, content_type="image/webp")
        resp["Cache-Control"] = "no-store"
        resp["X-Frame-Options"] = "DENY"
        resp["Referrer-Policy"] = "no-referrer"
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Cross-Origin-Resource-Policy"] = "same-origin"
        return resp

# ====== Discover sahifasi uchun yordamchi funksiya ===========================
def _build_recent_feed(limit_titles=10, per_title=3, window_hours=72):
    since = timezone.now() - timedelta(hours=window_hours)
    qs = (
        Chapter.objects.select_related("manga")
        .filter(Q(published_at__gte=since) | Q(release_date__gte=since.date()))
        .order_by(F("published_at").desc(nulls_last=True), "-id")[:800]
    )

    feed_map = {}
    for ch in qs:
        m = ch.manga
        if not m:
            continue

        ch_dt = _chapter_last_dt(ch)
        box = feed_map.setdefault(m.id, {"manga": m, "chapters": [], "last": ch_dt, "total": 0})
        box["total"] += 1
        if ch_dt and ch_dt > box["last"]:
            box["last"] = ch_dt

        if len(box["chapters"]) < per_title:
            try:
                translators = list(ch.translators.all()[:3])
            except Exception:
                translators = []
            box["chapters"].append({"obj": ch, "translators": translators})

    items = list(feed_map.values())
    for it in items:
        it["shown"] = len(it["chapters"])
        it["more"]  = max(0, it["total"] - it["shown"])
        it["ago"]   = _ago_uz(it["last"])

    items.sort(key=lambda x: x["last"], reverse=True)
    return items[:limit_titles]


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
    data = cache.get(cache_key)
    if data is None:                     # <<— shuni ishlat
        data = queryset_func()
        cache.set(cache_key, data, timeout)
    return data


def random_manga(request):
    """Tasodifiy mangaga redirect qiladi."""
    count = Manga.objects.count()
    if count == 0:
        return redirect("manga:discover")

    # Tez va barqaror usul: ofset bilan olish
    idx = random.randint(0, count - 1)
    random_manga_obj = Manga.objects.order_by('id')[idx]
    return redirect("manga:manga_details", manga_slug=random_manga_obj.slug)

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
from datetime import datetime, date, time

TOP_TRANSLATORS_KEY = "discover_top_translators_v1"
RECENT_FEED_KEY     = "discover_recent_feed_v1"

TOP_TRANSLATORS_TTL = 60 * 60 * 12   # 12 soat
RECENT_FEED_TTL     = 60 * 30        # 30 daqiqa

def _chapter_last_dt(ch):
    dt = getattr(ch, "published_at", None)
    if dt:
        return timezone.localtime(dt) if timezone.is_aware(dt) else timezone.make_aware(dt)

    rel = getattr(ch, "release_date", None)
    if rel:
        # fallback: kun boshi (yoki xohlasangiz time(12,0))
        return timezone.make_aware(datetime.combine(rel, time(0, 0)))

    return timezone.now()

def _ago_uz(dt):
    """
    'N daqiqa/soat/kun oldin' — date ham, datetime ham qabul qiladi.
    Chapter-logikani BU yerga kiritmang.
    """
    if not dt:
        return ""

    now_local = timezone.localtime(timezone.now())

    # Agar faqat sana bo'lsa — kunlar bo'yicha hisoblaymiz
    if isinstance(dt, date) and not isinstance(dt, datetime):
        d = (now_local.date() - dt).days
        if d <= 0:
            return "bugun"
        if d == 1:
            return "kecha"
        if d < 30:
            return f"{d} kun oldin"
        mo = d // 30
        if mo < 12:
            return f"{mo} oy oldin"
        return f"{mo // 12} yil oldin"

    # Datetime bo'lsa — aware/localtime ga o'tkazamiz
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    dt = timezone.localtime(dt)

    s = int((now_local - dt).total_seconds())
    if s <= 0:
        return "hozir"

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
    if request.user.is_authenticated:
        UserProfile.objects.get_or_create(user=request.user)

    def _get_top_translators():
        return list(
            UserProfile.objects.filter(is_translator=True)
            .select_related("user")
            .annotate(
                manga_count=Count("user__mangas_created", distinct=True),
                follower_count=Count("followers", distinct=True),
                likes_count=Count("user__mangas_created__likes", distinct=True),
            )
            .order_by("-likes_count", "-follower_count")[:4]
        )

    top_translators = get_cached_or_query(
        TOP_TRANSLATORS_KEY, _get_top_translators, TOP_TRANSLATORS_TTL
    )

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

    def _get_latest_updates_unique():
        raw = (
            Chapter.objects
            .select_related("manga")
            .order_by("-release_date", "-volume", "-chapter_number", "-id")[:300]
        )
        seen, items = set(), []
        for ch in raw:
            if ch.manga_id in seen:
                continue
            seen.add(ch.manga_id)
            items.append({"manga": ch.manga, "chapter": ch})
            if len(items) >= 15:
                break
        return items

    latest_updates = get_cached_or_query(
        "discover_latest_updates_unique_v1", _get_latest_updates_unique, 60 * 30
    )

    # ⚠️ Mana bu qismi funksiya tashqarisida bo‘lishi kerak
    recent_feed = cache.get(RECENT_FEED_KEY)
    if recent_feed is None:
        recent_feed = _build_recent_feed(limit_titles=10, per_title=3, window_hours=72)
        cache.set(RECENT_FEED_KEY, recent_feed, RECENT_FEED_TTL)
        
    # Sessiyadagi kontent filtrini keshdan kelgan ro‘yxatlarga ham qo‘llaymiz
    trending_mangas = cf_filter_list(trending_mangas, request)
    latest_mangas   = cf_filter_list(latest_mangas, request)
    active_progress = cf_filter_list(active_progress, request, key="manga")
    latest_updates  = cf_filter_list(latest_updates,  request, key="manga")
    recent_feed     = cf_filter_list(recent_feed,     request, key="manga")

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

    # --- 0) Sessiondagi global kontent filtri (EXCLUDE) ---------------------
    cf = request.session.get("content_filter") or {}
    hide_types  = cf.get("types")  or []
    hide_genres = cf.get("genres") or []
    hide_tags   = cf.get("tags")   or []

    # --- 1) Response-cache (faqat anonim, GET+CF kalitda) -------------------
    cf_qs = urlencode(
        [("t",  v) for v in hide_types]  +
        [("g",  v) for v in hide_genres] +
        [("tg", v) for v in hide_tags],
        doseq=True
    )
    cache_key = (
        "manga_browse:"
        f"{request.user.is_authenticated}:"
        f"{urlencode(request.GET, doseq=True)}:"
        f"cf:{cf_qs}"
    )
    if not request.user.is_authenticated:
        cached = cache.get(cache_key)
        if cached:
            return cached

    # --- 2) UserProfile (foydalanuvchi statuslari uchun) --------------------
    user_profile = None
    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # --- 3) Bazaviy queryset -------------------------------------------------
    qs = (
        Manga.objects
        .all()
        .prefetch_related("genres", "tags")
    )

    # --- 4) Qidiruv ----------------------------------------------------------
    search_query = (request.GET.get("search") or "").strip()
    if search_query:
        norm = make_search_key(search_query)
        qs = qs.filter(
            Q(title__icontains=search_query) |
            Q(title_search_key__contains=norm) |
            Q(titles__name__icontains=search_query) |
            Q(titles__search_key__contains=norm)
        ).distinct()

    # --- 5) Checkbox filtrlar (INCLUDE) -------------------------------------
    filter_mappings = {
        "genre":               ("genres__name",         True),
        "age_rating":          ("age_rating",           False),
        "type":                ("type",                 False),
        "tag":                 ("tags__name",           True),
        "status":              ("status",               False),
        "translation_status":  ("translation_status",   False),
    }
    for param, (field, need_distinct) in filter_mappings.items():
        vals = request.GET.getlist(param)
        if vals:
            qs = qs.filter(**{f"{field}__in": vals})
            if need_distinct:
                qs = qs.distinct()

    # --- 5.1) GLOBAL CONTENT FILTER (EXCLUDE) --------------------------------
    #     Modalda saqlangan yashirish ro‘yxatlari DB darajasida qo‘llanadi
    if hide_types:
        qs = qs.exclude(type__in=hide_types)
    if hide_genres:
        qs = qs.exclude(genres__name__in=hide_genres)
    if hide_tags:
        qs = qs.exclude(tags__name__in=hide_tags)
    if hide_genres or hide_tags:
        qs = qs.distinct()

    # --- 6) Boblar soni oralig‘i --------------------------------------------
    min_chap = request.GET.get("min_chapters")
    max_chap = request.GET.get("max_chapters")
    if min_chap or max_chap:
        qs = qs.annotate(chap_count=Count("chapters", distinct=True))
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

    # --- 7) Noshirlik yili oralig‘i -----------------------------------------
    min_year = request.GET.get("min_year")
    max_year = request.GET.get("max_year")
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

    # --- 8) Sortlash ---------------------------------------------------------
    sort = request.GET.get("sort", "chapters")
    if sort == "chapters":
        qs = qs.annotate(chap_count=Count("chapters", distinct=True)).order_by("-chap_count", "title")
    elif sort == "title_asc":
        qs = qs.order_by("title")
    elif sort == "title_desc":
        qs = qs.order_by("-title")
    else:
        qs = qs.order_by("title")

    # --- 9) Prefetch: foydalanuvchi o‘qish statusi ---------------------------
    if user_profile:
        qs = qs.prefetch_related(
            Prefetch(
                "readingstatus_set",
                queryset=ReadingStatus.objects.filter(user_profile=user_profile),
                to_attr="user_status",
            )
        )

    # --- 10) Paginatsiya -----------------------------------------------------
    paginator = Paginator(qs, 16)
    page_obj = paginator.get_page(request.GET.get("page"))

    elided_page_range = list(
        paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)
    )

    # --- 11) Choices (24 soat cache) ----------------------------------------
    def _choices(field): return Manga._meta.get_field(field).choices
    status_choices      = get_cached_or_query("choices_status",      lambda: _choices("status"),              60*60*24)
    age_rating_choices  = get_cached_or_query("choices_age_rating",  lambda: _choices("age_rating"),          60*60*24)
    type_choices        = get_cached_or_query("choices_type",        lambda: _choices("type"),                60*60*24)
    translation_choices = get_cached_or_query("choices_translation", lambda: _choices("translation_status"),  60*60*24)

    # --- 12) Janr va teglar (24 soat cache) ---------------------------------
    genres = get_cached_or_query("all_genres", lambda: list(Genre.objects.all()), 60*60*24)
    tags   = get_cached_or_query("all_tags",   lambda: list(Tag.objects.all()),   60*60*24)

    # --- 13) Paginatsiya linklarida GET’larni saqlash -----------------------
    qs_preserve = request.GET.copy()
    qs_preserve.pop("page", None)
    preserve_qs = qs_preserve.urlencode()

    context = {
        "genres": genres,
        "tags": tags,
        "page_obj": page_obj,
        "elided_page_range": elided_page_range,
        "preserve_qs": preserve_qs,
        "search": search_query,
        "sort": sort,

        # Tanlangan filtrlar (checkboxlar uchun)
        "genre_filter_list": request.GET.getlist("genre"),
        "tag_filter_list": request.GET.getlist("tag"),
        "age_rating_filter_list": request.GET.getlist("age_rating"),
        "type_filter_list": request.GET.getlist("type"),
        "status_filter_list": request.GET.getlist("status"),
        "translation_filter_list": request.GET.getlist("translation_status"),

        # Oraliqlar
        "min_chapters": request.GET.get("min_chapters", ""),
        "max_chapters": request.GET.get("max_chapters", ""),
        "min_year":     request.GET.get("min_year", ""),
        "max_year":     request.GET.get("max_year", ""),

        # Choices
        "status_choices": status_choices,
        "age_rating_choices": age_rating_choices,
        "type_choices": type_choices,
        "translation_choices": translation_choices,
    }

    response = render(request, "manga/browse.html", context)

    # --- 14) To‘liq response-cache (anonim) — 15 daqiqa ----------------------
    if not request.user.is_authenticated:
        cache.set(cache_key, response, 60 * 15)

    return response


# ====== детали манги ========================================================
def manga_details(request, manga_slug):
    """
    Detail view (cache-siz asosiy qism).
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
        # Profile (statuslar uchun)
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
    first_chapter = manga.chapters.order_by("volume", "chapter_number").first()

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

    # --- Analytics (KESHLANGAN) --------------------------------------------
    # TTL ni settings’dan sozlash mumkin: MANGA_STATS_TTL (sekundlarda). Default: 10 daqiqa.
    ttl = getattr(settings, "MANGA_STATS_TTL", 60 * 10)
    cache_key = f"manga:{manga.id}:stats:v1"  # versiya suffix — strukturani o‘zgartirsangiz yangilang

    stats = cache.get(cache_key)
    if stats is None:
        # jami
        agg_all = manga.chapters.aggregate(
            readers_all=Count("visits__user", distinct=True),
            reads_all=Count("visits"),
        )
        # Oxirgi 30 kun
        since_30 = now() - timedelta(days=30)
        agg_30d = manga.chapters.aggregate(
            readers_30d=Count("visits__user", filter=Q(visits__visited_at__gte=since_30), distinct=True),
            reads_30d=Count("visits", filter=Q(visits__visited_at__gte=since_30)),
        )

        stats = {
            "readers_all": agg_all["readers_all"] or 0,
            "reads_all":   agg_all["reads_all"] or 0,
            "readers_30d": agg_30d["readers_30d"] or 0,
            "reads_30d":   agg_30d["reads_30d"] or 0,
        }
        cache.set(cache_key, stats, ttl)

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

        # Start/Continue action
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

        # analytics (keshlangan)
        "readers_all": stats["readers_all"],
        "reads_all":   stats["reads_all"],
        "readers_30d": stats["readers_30d"],
        "reads_30d":   stats["reads_30d"],
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

    # 8) Sahifalar (Page -> tokenlangan URL’lar)
    pages = list(chapter.pages.all().order_by('page_number'))

    # --- YANGI: tokenlangan, qisqa muddatli URL’lar ---
    # make_page_token(request, page_id) va page_image endpoint avvaldan mavjud bo‘lishi shart.
    pages_payload = []
    for p in pages:
        tok = make_page_token(request, p.id)  # helper
        secure_url = request.build_absolute_uri(
            reverse("manga:page_image", args=[p.id, tok])  # yopiq endpoint
        )
        pages_payload.append({"url": secure_url, "alt": f"Sahifa {p.page_number}"})
    # --- /YANGI ---

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
        'pages': pages,                              # konteynerlar soni uchun
        'pages_payload': pages_payload,              # 👈 front-end JSON uchun (img src manzillari)
        'purchased_chapters': purchased_chapters,
        'readable_chapter_ids': readable_chapter_ids,  # 👈 LOCK tekshiruvi uchun
        'is_last_chapter': is_last_chapter,
    }
    return render(request, 'manga/chapter_read.html', context)



def _make_alpha_groups(qs, name_field="name"):
    """
    Alfavit bo‘yicha guruhlash (A, B, ... #).
    qs ichida .name va .num (manga soni) bo‘lishi kerak.
    """
    groups = defaultdict(list)
    for obj in qs:
        name = getattr(obj, name_field) or ""
        first = name.strip()[:1].upper() if name else "#"
        # agar harf/raqam bo‘lmasa -> '#'
        if not first or not first.isalnum():
            first = "#"
        groups[first].append(obj)

    # Har bir guruhni nom bo‘yicha sortlab chiqamiz
    letters = sorted(groups.keys(), key=lambda x: x)
    out = [{"letter": L, "items": sorted(groups[L], key=lambda o: getattr(o, name_field).lower())}
           for L in letters]
    return out

def _taxonomy_context(model_cls, title, qparam, request):
    """
    model_cls: Genre yoki Tag
    title: sahifa sarlavhasi
    qparam: 'genre' yoki 'tag' (browse uchun GET nomi)
    """
    sort = request.GET.get("sort", "alpha")  # 'alpha' | 'popular'

    # annotatsiya: shu janr/tegga bog‘langan taytlar soni
    qs = model_cls.objects.annotate(num=Count("mangas", distinct=True))

    total_count = qs.count()

    if sort == "popular":
        items = list(qs.order_by("-num", "name"))
        groups = None
    else:
        # default: alpha
        items = list(qs.order_by("name"))
        groups = _make_alpha_groups(items)

    return {
        "title": title,
        "qparam": qparam,
        "active_tab": sort,
        "total_count": total_count,
        "groups": groups,       # alpha bo‘lsa to‘ladi
        "items": items,         # popular bo‘lsa ishlatiladi
    }

def genre_index(request):
    ctx = _taxonomy_context(Genre, "Janrlar", "genre", request)
    return render(request, "manga/taxonomy_list.html", ctx)

def tag_index(request):
    ctx = _taxonomy_context(Tag, "Teglar", "tag", request)
    return render(request, "manga/taxonomy_list.html", ctx)


def reading_now(request):
    """
    /manga/reading/?tab=latest|trending|popular
    - latest: so‘nggi yangilangan (oxirgi 7 kun ichida), har title'dan bir nechta bob
    - trending: oxirgi 7 kunda eng ko‘p davom ettirilgan (ReadingProgress) taytlar
    - popular: oxirgi 30 kunda eng ko‘p o‘qilgan (ChapterVisit) taytlar
    """
    tab = (request.GET.get("tab") or "latest").lower()
    limit = 30  # sahifada nechta title ko‘rsatamiz

    ctx = {"active_tab": tab, "items": [], "feed": []}

    if tab == "trending":
        cache_key = f"reading_trending_{limit}_v1"
        data = cache.get(cache_key)
        if data is None:
            since = now() - timedelta(days=7)
            agg = (
                ReadingProgress.objects
                .filter(updated_at__gte=since)
                .values("manga")
                .annotate(
                    readers=Count("user", distinct=True),
                    last=Max("updated_at"),
                )
                .order_by("-readers", "-last")[:limit]
            )
            ids = [r["manga"] for r in agg]
            m_map = {m.id: m for m in Manga.objects.filter(id__in=ids)}
            data = [
                {
                    "manga": m_map.get(r["manga"]),
                    "readers": r["readers"],
                    "last": r["last"],
                    "ago": _ago_uz(r["last"]),
                }
                for r in agg if m_map.get(r["manga"])
            ]
            cache.set(cache_key, data, 60 * 15)

        # ✅ Sessiya kontent filtri
        data = [d for d in data if d.get("manga") and not cf_should_hide_manga(d["manga"], request)]
        ctx["items"] = data

    elif tab == "popular":
        cache_key = f"reading_popular_{limit}_v1"
        data = cache.get(cache_key)
        if data is None:
            since = now() - timedelta(days=30)
            agg = (
                ChapterVisit.objects
                .filter(visited_at__gte=since)
                .values("chapter__manga")
                .annotate(
                    reads=Count("id"),
                    readers=Count("user", distinct=True),
                    last=Max("visited_at"),
                )
                .order_by("-reads", "-last")[:limit]
            )
            ids = [r["chapter__manga"] for r in agg]
            m_map = {m.id: m for m in Manga.objects.filter(id__in=ids)}
            data = [
                {
                    "manga": m_map.get(r["chapter__manga"]),
                    "reads": r["reads"],
                    "readers": r["readers"],
                    "last": r["last"],
                    "ago": _ago_uz(r["last"]),
                }
                for r in agg if m_map.get(r["chapter__manga"])
            ]
            cache.set(cache_key, data, 60 * 15)

        # ✅ Sessiya kontent filtri
        data = [d for d in data if d.get("manga") and not cf_should_hide_manga(d["manga"], request)]
        ctx["items"] = data

    else:  # latest (default)
        cache_key = "reading_latest_v1"
        feed = cache.get(cache_key)
        if feed is None:
            # Oxirgi 7 kun: har title'dan 3 tagacha so‘nggi bob
            feed = _build_recent_feed(limit_titles=30, per_title=3, window_hours=24*7)
            cache.set(cache_key, feed, 60 * 10)

        # ✅ Har bir element dict: {"manga": ..., "chapter": ...}
        feed = cf_filter_list(feed, request, key="manga")
        ctx["feed"] = feed
        ctx["active_tab"] = "latest"

    return render(request, "manga/reading_now.html", ctx)


from django.db.models import Max, Subquery, OuterRef

@login_required
def reading_history(request):
    """
    /history/?tab=titles|translators|authors&order=new|old&q=...&page=...
    """
    user  = request.user
    q     = (request.GET.get("q") or "").strip()
    order = (request.GET.get("order") or "new").lower()
    tab   = (request.GET.get("tab") or "titles").lower()

    # ---- visits
    visits_base = ChapterVisit.objects.filter(user=user)
    if q and tab == "titles":
        visits_base = visits_base.filter(
            Q(chapter__manga__title__icontains=q) |
            Q(chapter__manga__titles__name__icontains=q)
        ).distinct()

    last_visit_qs = (
        visits_base.values("chapter__manga")
        .annotate(
            last_visit=Max("visited_at"),
            last_visit_chapter_id=Subquery(
                visits_base.filter(chapter__manga=OuterRef("chapter__manga"))
                .order_by("-visited_at")
                .values("chapter__id")[:1]
            ),
        )
    )
    visit_map = {
        r["chapter__manga"]: {"last_time": r["last_visit"], "chapter_id": r["last_visit_chapter_id"]}
        for r in last_visit_qs
    }

    # ---- progress
    progress_qs = ReadingProgress.objects.filter(user=user).select_related("last_read_chapter", "manga")
    if q and tab == "titles":
        progress_qs = progress_qs.filter(
            Q(manga__title__icontains=q) |
            Q(manga__titles__name__icontains=q)
        ).distinct()

    prog_map = {}
    for p in progress_qs:
        prog_map[p.manga_id] = {
            "last_time": p.updated_at,
            "chapter_id": getattr(p.last_read_chapter, "id", None),
            "page": p.last_read_page or 1,
        }

    # ---- titles list (raw, before pagination)
    manga_ids = set(visit_map.keys()) | set(prog_map.keys())
    items_titles = []
    if manga_ids:
        mangas = (
            Manga.objects.filter(id__in=manga_ids)
            .annotate(chap_total=Count("chapters", distinct=True))
            .select_related("created_by")
            .prefetch_related("titles")
        )
        last_ch_ids = {d["chapter_id"] for d in visit_map.values() if d["chapter_id"]}
        last_ch_ids |= {d["chapter_id"] for d in prog_map.values() if d["chapter_id"]}
        ch_map = {c.id: c for c in Chapter.objects.filter(id__in=last_ch_ids)}

        for m in mangas:
            v = visit_map.get(m.id)
            p = prog_map.get(m.id)
            cand = []
            if v and v["last_time"]:
                cand.append(("visit", v["last_time"], v.get("chapter_id"), 1))
            if p and p["last_time"]:
                cand.append(("progress", p["last_time"], p.get("chapter_id"), p.get("page") or 1))
            if not cand:
                continue
            cand.sort(key=lambda x: x[1], reverse=True)
            _src, last_time, last_ch_id, page = cand[0]
            last_ch = ch_map.get(last_ch_id)

            alt_name = None
            try:
                t0 = m.titles.all().first()
                if t0 and t0.name and t0.name.strip().lower() != (m.title or "").strip().lower():
                    alt_name = t0.name
            except Exception:
                pass

            resume_url = None
            if last_ch:
                try:
                    resume_url = reverse(
                        "manga:chapter_read",
                        kwargs={"manga_slug": m.slug, "volume": last_ch.volume, "chapter_number": last_ch.chapter_number},
                    )
                except Exception:
                    pass

            items_titles.append({
                "manga": m,
                "last_time": last_time,
                "ago": _ago_uz(last_time),
                "last_chapter": last_ch,
                "page": page or 1,
                "resume_url": resume_url,
                "alt_name": alt_name,
                "chap_total": getattr(m, "chap_total", 0),
            })

    items_titles.sort(key=lambda x: x["last_time"], reverse=(order != "old"))

    # ---- translators & authors (built from titles)
    translator_map = {}
    for it in items_titles:
        m = it["manga"]
        up = getattr(getattr(m, "created_by", None), "userprofile", None)
        if not (up and getattr(up, "is_translator", False)):
            continue
        T = translator_map.setdefault(up.id, {
            "profile": up, "last_time": it["last_time"], "ago": it["ago"],
            "cover": getattr(m, "cover_image", None), "num_titles": 0,
        })
        T["num_titles"] += 1
        if it["last_time"] > T["last_time"]:
            T["last_time"] = it["last_time"]; T["ago"] = it["ago"]; T["cover"] = getattr(m, "cover_image", None)
    items_translators = list(translator_map.values())
    if q and tab == "translators":
        items_translators = [t for t in items_translators if q.lower() in (getattr(t["profile"].user, "username", "")).lower()]
    items_translators.sort(key=lambda x: x["last_time"], reverse=(order != "old"))

    author_map = {}
    for it in items_titles:
        m = it["manga"]; author_name = getattr(m, "author", None)
        if not author_name: continue
        A = author_map.setdefault(author_name, {
            "name": author_name, "last_time": it["last_time"], "ago": it["ago"],
            "cover": getattr(m, "cover_image", None), "num_titles": 0,
        })
        A["num_titles"] += 1
        if it["last_time"] > A["last_time"]:
            A["last_time"] = it["last_time"]; A["ago"] = it["ago"]; A["cover"] = getattr(m, "cover_image", None)
    items_authors = list(author_map.values())
    if q and tab == "authors":
        items_authors = [a for a in items_authors if q.lower() in (a["name"] or "").lower()]
    items_authors.sort(key=lambda x: x["last_time"], reverse=(order != "old"))

    # ---- counts for left sidebar
    counts = {
        "titles":      len(items_titles),
        "translators": len(items_translators),
        "authors":     len(items_authors),
        "publishers":  0,
        "collections": 0,
    }

    items_by_tab = {
        "titles":      items_titles,
        "translators": items_translators,
        "authors":     items_authors,
        "publishers":  [],
        "collections": [],
    }
    items = items_by_tab.get(tab, items_titles)

    # ---- pagination
    per_page = 10
    paginator = Paginator(items, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))

    # to keep q/order/tab in page links
    qs_preserve = request.GET.copy()
    qs_preserve.pop("page", None)
    preserve_qs = qs_preserve.urlencode()

    ctx = {
        "tab": tab,
        "order": order,
        "q": q,
        "counts": counts,
        "items": page_obj.object_list,
        "page_obj": page_obj,
        "elided_page_range": list(paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)),
        "preserve_qs": preserve_qs,
    }
    return render(request, "manga/history.html", ctx)


@login_required
@require_POST
def history_clear(request):
    """
    Tarixni tozalash.
    - Agar 'all' kelgan bo‘lsa: foydalanuvchining barcha visits va progresslari o‘chiriladi.
    - Aks holda: joriy 'tab' bo‘yicha (tab=titles) — titles agregatiga ta'sir qiluvchi hammasi (visits+progress).
      (translators/authors ham titles dan tuzilgani uchun titles’ni tozalash yetarli).
    """
    tab = (request.POST.get("tab") or "titles").lower()
    clear_all = bool(request.POST.get("all"))
    nxt = request.POST.get("next") or reverse("manga:history")

    if clear_all or tab in ("titles", "translators", "authors", "publishers", "collections"):
        ChapterVisit.objects.filter(user=request.user).delete()
        ReadingProgress.objects.filter(user=request.user).delete()
        messages.success(request, "Tarix muvaffaqiyatli tozalandi.")
    else:
        messages.info(request, "Noto‘g‘ri bo‘lim.")

    return redirect(nxt)


@login_required
@require_POST
def history_remove(request, manga_id):
    """Foydalanuvchining bitta manga bo‘yicha tarixini tozalash."""
    ChapterVisit.objects.filter(user=request.user, chapter__manga_id=manga_id).delete()
    ReadingProgress.objects.filter(user=request.user, manga_id=manga_id).delete()
    messages.success(request, "Tanlangan tayt tarixi o‘chirildi.")
    next_url = request.POST.get("next") or reverse("manga:history")
    return redirect(next_url)



@require_POST
def content_filter_save(request):
    """
    Modal formadan kelgan tanlovlarni sessiyaga yozadi.
    'types', 'genres', 'tags' – barchasi YASHIRISH (exclude) uchun.
    """
    types  = request.POST.getlist("types")
    genres = request.POST.getlist("genres")
    tags   = request.POST.getlist("tags")

    request.session["content_filter"] = {
        "types": types,
        "genres": genres,
        "tags": tags,
    }
    request.session.modified = True

    messages.success(request, "Kontent filtri saqlandi.")
    nxt = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("manga:browse")
    return redirect(nxt)


@login_required
@require_POST
def content_filter_clear(request):
    request.session.pop("content_filter", None)
    messages.info(request, "Filtrlar tozalandi.")
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("manga:discover"))
