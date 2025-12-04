import random
from collections import defaultdict
from datetime import datetime, date, time, timedelta
from uuid import uuid4
from django.db import transaction
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.db.models import Q, Count, F, Max, Subquery, OuterRef, Prefetch, Avg
from django.http import HttpResponse, JsonResponse, FileResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST, require_GET
from manga.service import _is_translator, can_read
from .models import (
    ChapterAnonVisit, ChapterPurchase, ChapterVisit, Manga, Chapter, Genre, Page, ReadingProgress, Tag,
    make_search_key
)
from accounts.models import ReadingStatus, TranslatorRating, UserProfile, READING_STATUSES
from django.utils.http import url_has_allowed_host_and_scheme

signer = TimestampSigner(salt="page-image-v2")


def _is_ajax(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@require_POST
@login_required
@transaction.atomic
def rate_translator(request, manga_slug, translator_id):
    manga = get_object_or_404(Manga, slug=manga_slug)
    translator = get_object_or_404(UserProfile, pk=translator_id, is_translator=True)

    # ✅ translator shu mangaga biriktirilganmi?
    # (Agar Manga.translators UserProfile emas, User bo‘lsa -> translator.user bilan tekshiring)
    if not manga.translators.filter(pk=translator.pk).exists():
        msg = "Bu tarjimon ushbu manga uchun biriktirilmagan."
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("manga:manga_details", manga_slug=manga.slug)

    # ✅ rating parse + validate
    raw = (request.POST.get("rating") or "").strip()
    try:
        rating = int(raw)
    except (TypeError, ValueError):
        rating = 0

    if rating not in (1, 2, 3, 4, 5):
        msg = "Ovoz 1 dan 5 gacha bo‘lishi kerak."
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("manga:manga_details", manga_slug=manga.slug)

    # ✅ save (1 user = 1 rating per manga+translator)
    TranslatorRating.objects.update_or_create(
        manga=manga,
        translator=translator,
        user=request.user,
        defaults={"rating": rating},
    )

    # ✅ avg/count (shu manga+translator uchun)
    agg = TranslatorRating.objects.filter(manga=manga, translator=translator).aggregate(
        avg=Avg("rating"),
        count=Count("id"),
    )
    avg = float(agg["avg"] or 0)
    count = int(agg["count"] or 0)

    if _is_ajax(request):
        return JsonResponse({
            "ok": True,
            "liked": True,              # ixtiyoriy, kerak bo‘lmasa olib tashlang
            "my_rating": rating,
            "avg": round(avg, 1),
            "count": count,
        })

    messages.success(request, "Ovozingiz saqlandi.")

    # ✅ qaytish: oldingi sahifa/tab saqlansin (xavfsiz)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("manga:manga_details", kwargs={"manga_slug": manga.slug})
    return redirect(next_url)

# =========================== Helpers ===========================

def _subject_for(request) -> str:
    if request.user.is_authenticated:
        return str(request.user.id)
    if not request.session.session_key:
        request.session.save()
    return str(request.session.session_key)


def make_page_token(request, page_id: int) -> str:
    return signer.sign(f"{_subject_for(request)}:{page_id}")


def get_cached_or_query(cache_key, queryset_func, timeout):
    data = cache.get(cache_key)
    if data is None:
        data = queryset_func()
        cache.set(cache_key, data, timeout)
    return data


# =========================== Protected page image ===========================

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

    # 2) Token owner
    if subject != _subject_for(request):
        return HttpResponseForbidden("Forbidden")

    # 3) Permission
    page = get_object_or_404(Page, id=page_id)
    ch = page.chapter
    if not can_read(request.user, ch.manga, ch):
        return HttpResponseForbidden("No access")

    # 4) Dev/Prod delivery
    if getattr(settings, "USE_X_ACCEL_REDIRECT", False):
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
        f = page.image.open("rb")
        resp = FileResponse(f, content_type="image/webp")
        resp["Cache-Control"] = "no-store"
        resp["X-Frame-Options"] = "DENY"
        resp["Referrer-Policy"] = "no-referrer"
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Cross-Origin-Resource-Policy"] = "same-origin"
        return resp


# =========================== Discover feed utils ===========================

def _chapter_last_dt(ch):
    dt = getattr(ch, "published_at", None)
    if dt:
        return timezone.localtime(dt) if timezone.is_aware(dt) else timezone.make_aware(dt)

    rel = getattr(ch, "release_date", None)
    if rel:
        return timezone.make_aware(datetime.combine(rel, time(0, 0)))

    return timezone.now()


def _ago_uz(dt):
    if not dt:
        return ""

    now_local = timezone.localtime(timezone.now())

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
            translators = []
            try:
                translators = list(ch.translators.all()[:3])
            except Exception:
                pass
            box["chapters"].append({"obj": ch, "translators": translators})

    items = list(feed_map.values())
    for it in items:
        it["shown"] = len(it["chapters"])
        it["more"] = max(0, it["total"] - it["shown"])
        it["ago"] = _ago_uz(it["last"])

    items.sort(key=lambda x: x["last"], reverse=True)
    return items[:limit_titles]


# =========================== Likes ===========================

@login_required
@require_POST
def toggle_manga_like(request, slug):
    manga = get_object_or_404(Manga, slug=slug)

    if hasattr(manga, "likes"):
        if manga.likes.filter(pk=request.user.pk).exists():
            manga.likes.remove(request.user)
            liked = False
        else:
            manga.likes.add(request.user)
            liked = True
        likes_count = manga.likes.count()
        return JsonResponse({"success": True, "liked": liked, "likes_count": likes_count})

    try:
        from .models import MangaLike
    except Exception:
        return JsonResponse({"success": False, "error": "Like modeli topilmadi"}, status=400)

    obj, created = MangaLike.objects.get_or_create(manga=manga, user=request.user)
    liked = bool(created)
    if not created:
        obj.delete()
    likes_count = MangaLike.objects.filter(manga=manga).count()
    return JsonResponse({"success": True, "liked": liked, "likes_count": likes_count})


# =========================== Random manga ===========================

def random_manga(request):
    """Tasodifiy mangaga redirect qiladi."""
    count = Manga.objects.count()
    if count == 0:
        return redirect("manga:discover")

    idx = random.randint(0, count - 1)
    random_manga_obj = Manga.objects.order_by('id')[idx]
    return redirect("manga:manga_details", manga_slug=random_manga_obj.slug)


# =========================== Discover ===========================

TOP_TRANSLATORS_KEY = "discover_top_translators_v1"
RECENT_FEED_KEY     = "discover_recent_feed_v1"
TOP_TRANSLATORS_TTL = 60 * 60 * 12   # 12 soat
RECENT_FEED_TTL     = 60 * 30        # 30 daqiqa

# ---- HERO: random N (>= min_chapters) + genres/tags prefetche bilan ----
def _hero_random_posters(request, limit=10, min_chapters=3):
    # 1) Mos IDlar
    ids = list(
        Manga.objects
        .annotate(chap_count=Count("chapters"))           # distinct shart emas
        .filter(chap_count__gte=min_chapters)             # >=
        .values_list("id", flat=True)
    )

    # 2) Mos topilmasa: fallback (eng ko‘p bobli)
    if not ids:
        return list(
            Manga.objects
            .annotate(chap_count=Count("chapters"))
            .order_by("-chap_count", "-id")[:limit]
            .prefetch_related("genres", "tags")
        )

    # 3) Foydalanuvchi + bugungi kun bo‘yicha seed (kuniga bir xil)
    seed = f"{_subject_for(request)}:{timezone.now().date().isoformat()}"
    rnd = random.Random(seed)
    rnd.shuffle(ids)
    chosen = ids[:limit]

    # 4) Tanlanganlarni tartibni saqlagan holda qaytarish
    posters_qs = (
        Manga.objects
        .filter(id__in=chosen)
        .annotate(chap_count=Count("chapters"))
        .prefetch_related("genres", "tags")
    )
    m_map = {m.id: m for m in posters_qs}
    return [m_map[i] for i in chosen if i in m_map]

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
        return list(
            Manga.objects
            .annotate(readers=Count("readingprogress__user", distinct=True))
            .order_by("-readers", "-id")[:25]
        )

    trending_mangas = get_cached_or_query(
        "discover_trending_mangas_v2", _get_trending_mangas, 60 * 60
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

    recent_feed = cache.get(RECENT_FEED_KEY)
    if recent_feed is None:
        recent_feed = _build_recent_feed(limit_titles=10, per_title=3, window_hours=72)
        cache.set(RECENT_FEED_KEY, recent_feed, RECENT_FEED_TTL)
        
    hero_posters = _hero_random_posters(request, limit=5, min_chapters=3)

    context = {
        "top_translators": top_translators,
        "trending_mangas": trending_mangas,
        "latest_mangas":   latest_mangas,
        "active_progress": active_progress,
        "latest_updates":  latest_updates,
        "recent_feed":     recent_feed,
        "hero_posters":    hero_posters,
    }
    return render(request, "manga/discover.html", context)


# =========================== Browse (grid + filters) ===========================

def manga_browse(request):
    """
    Barcha taytlar (grid) + qidiruv, filtrlar, sort va paginate.
    """
    # 1) Full response cache (anon)
    cache_key = (
        "manga_browse:"
        f"{bool(request.user.is_authenticated)}:"
        f"{urlencode(request.GET, doseq=True)}"
    )
    if not request.user.is_authenticated:
        cached = cache.get(cache_key)
        if cached:
            return cached

    # 2) UserProfile (reading status uchun)
    user_profile = None
    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # 3) Base queryset
    qs = Manga.objects.all().prefetch_related("genres", "tags")

    # 4) Search
    search_query = (request.GET.get("search") or "").strip()
    if search_query:
        norm = make_search_key(search_query)
        qs = qs.filter(
            Q(title__icontains=search_query) |
            Q(title_search_key__contains=norm) |
            Q(titles__name__icontains=search_query) |
            Q(titles__search_key__contains=norm)
        ).distinct()

    # 5) Checkbox filters
    filter_mappings = {
        "genre":               ("genres__name",         True),
        "age_rating":          ("age_rating",           False),
        "type":                ("type",                 False),
        "tag":                 ("tags__name",           True),
        "status":              ("status",               False),
        "translation_status":  ("translation_status",   False),
    }
    for param, (field, need_distinct) in filter_mappings.items():
        vals = [v for v in request.GET.getlist(param) if v]
        if vals:
            qs = qs.filter(**{f"{field}__in": vals})
            if need_distinct:
                qs = qs.distinct()

    # 6) Chapter count range
    min_chap = request.GET.get("min_chapters")
    max_chap = request.GET.get("max_chapters")
    if min_chap or max_chap:
        qs = qs.annotate(chap_count=Count("chapters", distinct=True))
        if min_chap:
            try:
                v = int(min_chap)
                if v > 0:
                    qs = qs.filter(chap_count__gte=v)
            except (TypeError, ValueError):
                pass
        if max_chap:
            try:
                v = int(max_chap)
                if v > 0:
                    qs = qs.filter(chap_count__lte=v)
            except (TypeError, ValueError):
                pass

    # 7) Publication year range
    min_year = request.GET.get("min_year")
    max_year = request.GET.get("max_year")
    if min_year:
        try:
            iy = int(min_year)
            if iy >= 1:
                qs = qs.filter(publication_date__year__gte=iy)
        except (TypeError, ValueError):
            pass
    if max_year:
        try:
            iy = int(max_year)
            if iy >= 1:
                qs = qs.filter(publication_date__year__lte=iy)
        except (TypeError, ValueError):
            pass

    # 8) Sorting
    sort = request.GET.get("sort", "chapters")
    if sort == "chapters":
        qs = qs.annotate(chap_count=Count("chapters", distinct=True)).order_by("-chap_count", "title")
    elif sort == "title_asc":
        qs = qs.order_by("title")
    elif sort == "title_desc":
        qs = qs.order_by("-title")
    else:
        qs = qs.order_by("title")

    # 9) Prefetch reading status for current user
    if user_profile:
        qs = qs.prefetch_related(
            Prefetch(
                "readingstatus_set",
                queryset=ReadingStatus.objects.filter(user_profile=user_profile),
                to_attr="user_status",
            )
        )

    # 10) Pagination
    paginator = Paginator(qs, 16)
    page_obj = paginator.get_page(request.GET.get("page"))
    elided_page_range = list(
        paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)
    )

    # 11) Choices (24h cache)
    def _choices(field): return Manga._meta.get_field(field).choices
    status_choices      = get_cached_or_query("choices_status",      lambda: _choices("status"),              60*60*24)
    age_rating_choices  = get_cached_or_query("choices_age_rating",  lambda: _choices("age_rating"),          60*60*24)
    type_choices        = get_cached_or_query("choices_type",        lambda: _choices("type"),                60*60*24)
    translation_choices = get_cached_or_query("choices_translation", lambda: _choices("translation_status"),  60*60*24)

    # 12) Genres & Tags (24h cache)
    genres = get_cached_or_query("all_genres", lambda: list(Genre.objects.all()), 60*60*24)
    tags   = get_cached_or_query("all_tags",   lambda: list(Tag.objects.all()),   60*60*24)

    # 13) Preserve GET in pagination links
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

        "genre_filter_list": request.GET.getlist("genre"),
        "tag_filter_list": request.GET.getlist("tag"),
        "age_rating_filter_list": request.GET.getlist("age_rating"),
        "type_filter_list": request.GET.getlist("type"),
        "status_filter_list": request.GET.getlist("status"),
        "translation_filter_list": request.GET.getlist("translation_status"),

        "min_chapters": request.GET.get("min_chapters", ""),
        "max_chapters": request.GET.get("max_chapters", ""),
        "min_year":     request.GET.get("min_year", ""),
        "max_year":     request.GET.get("max_year", ""),

        "status_choices": status_choices,
        "age_rating_choices": age_rating_choices,
        "type_choices": type_choices,
        "translation_choices": translation_choices,
    }

    response = render(request, "manga/browse.html", context)

    # 14) Cache anon response (15 min)
    if not request.user.is_authenticated:
        cache.set(cache_key, response, 60 * 15)

    return response


# =========================== Details ===========================

def manga_details(request, manga_slug):
    order = (request.GET.get("order") or "desc").lower()

    manga = get_object_or_404(
        Manga.objects
        .select_related("created_by")
        .prefetch_related(
            "genres",
            "tags",
            "telegram_links",
            "translators__user",
            "chapters",
        ),
        slug=manga_slug,
    )

    # -------------------------
    # Privileged (statsni ko‘rish)
    # -------------------------
    can_see_detailed_stats = (
        request.user.is_authenticated and (
            request.user.is_superuser or _is_translator(request.user)
        )
    )

    # -------------------------
    # Auth bo‘lsa: status/like/progress/visited
    # -------------------------
    reading_status = None
    is_liked = False
    likes_count = manga.likes.count()
    like_toggle_url = None

    progress_current_chapter_id = None
    progress_current_page = None
    visited_chapter_ids = []

    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

        reading_status = (
            ReadingStatus.objects
            .filter(user_profile=user_profile, manga=manga)
            .select_related("user_profile", "manga")
            .first()
        )

        is_liked = manga.likes.filter(pk=request.user.pk).exists()
        try:
            like_toggle_url = reverse("manga:manga_like_toggle", kwargs={"slug": manga.slug})
        except Exception:
            like_toggle_url = None

        reading_progress = (
            ReadingProgress.objects
            .filter(user=request.user, manga=manga)
            .select_related("last_read_chapter")
            .first()
        )
        if reading_progress and reading_progress.last_read_chapter_id:
            progress_current_chapter_id = reading_progress.last_read_chapter_id
            progress_current_page = reading_progress.last_read_page

        visited_chapter_ids = list(
            ChapterVisit.objects
            .filter(user=request.user, chapter__manga=manga)
            .values_list("chapter_id", flat=True)
        )

    # -------------------------
    # Boblar tartibi (QuerySet)
    # -------------------------
    if order == "asc":
        chapters_qs = manga.chapters.order_by("volume", "chapter_number")
    else:
        chapters_qs = manga.chapters.order_by("-volume", "-chapter_number")

    # Template’da ishlatish uchun atributlar qo‘shib chiqamiz
    chapters = []
    for ch in chapters_qs:
        ch.can_read = can_read(request.user, manga, ch)
        ch.is_current = (progress_current_chapter_id == ch.id)
        ch.current_page = progress_current_page if ch.is_current else None
        ch.is_visited = (ch.id in visited_chapter_ids)
        chapters.append(ch)

    first_chapter = manga.chapters.order_by("volume", "chapter_number").first()

    # -------------------------
    # Start / Resume tugmasi
    # -------------------------
    start_button_url = None
    start_button_label = "O'qishni boshlash"

    if progress_current_chapter_id:
        resume = next((c for c in chapters if c.id == progress_current_chapter_id), None)
        if resume:
            start_button_url = reverse(
                "manga:chapter_read",
                kwargs={"manga_slug": manga.slug, "volume": resume.volume, "chapter_number": resume.chapter_number},
            )
            start_button_label = f"Davom ettirish (Bob {resume.chapter_number})"
    elif first_chapter:
        start_button_url = reverse(
            "manga:chapter_read",
            kwargs={"manga_slug": manga.slug, "volume": first_chapter.volume, "chapter_number": first_chapter.chapter_number},
        )

    # -------------------------
    # O‘xshash mangalar (janr)
    # -------------------------
    user_genres = manga.genres.all()
    similar_mangas = (
        Manga.objects.exclude(pk=manga.pk)
        .annotate(
            shared_genres=Count(
                "genres",
                filter=Q(genres__in=user_genres),
                distinct=True,
            )
        )
        .filter(shared_genres__gt=0)
        .order_by("-shared_genres", "title")[:10]
    )

    telegram_links = list(manga.telegram_links.all())

    # -------------------------
    # Tarjimonlar (M2M + fallback)
    # -------------------------
    translator_profiles = list(
        manga.translators
        .filter(is_translator=True)
        .select_related("user")
        .order_by("user__username")
    )

    if not translator_profiles and manga.created_by:
        fallback_profile = getattr(manga.created_by, "userprofile", None)
        if fallback_profile and fallback_profile.is_translator:
            translator_profiles = [fallback_profile]

    # -------------------------
    # ✅ Translator ratings (avg/count/my) — shu joy sizda yetishmayapti
    # -------------------------
    if translator_profiles:
        # avg/count per translator (shu manga ichida)
        rating_rows = (
            TranslatorRating.objects
            .filter(manga=manga, translator_id__in=[t.id for t in translator_profiles])
            .values("translator_id")
            .annotate(
                rating_avg=Avg("rating"),
                rating_count=Count("id"),
            )
        )
        stat_map = {r["translator_id"]: r for r in rating_rows}

        # my_rating faqat user authenticated bo‘lsa
        my_map = {}
        if request.user.is_authenticated:
            my_map = dict(
                TranslatorRating.objects
                .filter(manga=manga, user=request.user, translator_id__in=[t.id for t in translator_profiles])
                .values_list("translator_id", "rating")
            )

        # template ishlatadigan attribute’larni attach qilamiz
        for t in translator_profiles:
            s = stat_map.get(t.id) or {}
            t.rating_avg = float(s.get("rating_avg") or 0)
            t.rating_count = int(s.get("rating_count") or 0)
            t.my_rating = int(my_map.get(t.id, 0))

    # -------------------------
    # Statistikalar (cache bilan)
    # -------------------------
    ttl = getattr(settings, "MANGA_STATS_TTL", 60 * 10)
    cache_key = f"manga:{manga.id}:stats:v2"

    stats = cache.get(cache_key)
    if stats is None:
        since_30 = timezone.now() - timedelta(days=30)

        agg_logged_all = manga.chapters.aggregate(
            readers_logged=Count("visits__user", distinct=True),
            reads_logged=Count("visits"),
        )
        agg_logged_30d = manga.chapters.aggregate(
            readers_logged_30d=Count(
                "visits__user",
                filter=Q(visits__visited_at__gte=since_30),
                distinct=True,
            ),
            reads_logged_30d=Count(
                "visits",
                filter=Q(visits__visited_at__gte=since_30),
            ),
        )

        agg_anon_all = manga.chapters.aggregate(
            readers_anon=Count("anon_visits__visitor_id", distinct=True),
            reads_anon=Count("anon_visits"),
        )
        agg_anon_30d = manga.chapters.aggregate(
            readers_anon_30d=Count(
                "anon_visits__visitor_id",
                filter=Q(anon_visits__visited_at__gte=since_30),
                distinct=True,
            ),
            reads_anon_30d=Count(
                "anon_visits",
                filter=Q(anon_visits__visited_at__gte=since_30),
            ),
        )

        readers_all = (agg_logged_all["readers_logged"] or 0) + (agg_anon_all["readers_anon"] or 0)
        reads_all   = (agg_logged_all["reads_logged"] or 0)   + (agg_anon_all["reads_anon"] or 0)

        readers_30d = (agg_logged_30d["readers_logged_30d"] or 0) + (agg_anon_30d["readers_anon_30d"] or 0)
        reads_30d   = (agg_logged_30d["reads_logged_30d"] or 0)   + (agg_anon_30d["reads_anon_30d"] or 0)

        stats = {
            "readers_all": readers_all,
            "reads_all": reads_all,
            "readers_30d": readers_30d,
            "reads_30d": reads_30d,

            "readers_logged": agg_logged_all["readers_logged"] or 0,
            "reads_logged": agg_logged_all["reads_logged"] or 0,
            "readers_logged_30d": agg_logged_30d["readers_logged_30d"] or 0,
            "reads_logged_30d": agg_logged_30d["reads_logged_30d"] or 0,
        }
        cache.set(cache_key, stats, ttl)

    # -------------------------
    # Context
    # -------------------------
    context = {
        "manga": manga,
        "reading_status": reading_status,
        "READING_STATUSES": READING_STATUSES,

        "chapters": chapters,
        "first_chapter": first_chapter,
        "current_order": order,

        "visited_chapter_ids": visited_chapter_ids,
        "progress_current_chapter_id": progress_current_chapter_id,
        "progress_current_page": progress_current_page,

        "start_button_url": start_button_url,
        "start_button_label": start_button_label,

        "is_liked": is_liked,
        "likes_count": likes_count,
        "like_toggle_url": like_toggle_url,

        "similar_mangas": similar_mangas,
        "telegram_links": telegram_links,
        "translator_profiles": translator_profiles,

        "can_see_detailed_stats": can_see_detailed_stats,

        "readers_all": stats["readers_all"],
        "reads_all": stats["reads_all"],
        "readers_30d": stats["readers_30d"],
        "reads_30d": stats["reads_30d"],
    }

    if can_see_detailed_stats:
        context.update({
            "readers_logged": stats.get("readers_logged", 0),
            "reads_logged": stats.get("reads_logged", 0),
            "readers_logged_30d": stats.get("readers_logged_30d", 0),
            "reads_logged_30d": stats.get("reads_logged_30d", 0),
        })

    return render(request, "manga/manga_details.html", context)

# =========================== Reading list/status ===========================

@login_required
def add_to_reading_list(request, manga_slug):
    manga = get_object_or_404(Manga, slug=manga_slug)
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

    status = request.POST.get('status', 'planned')

    if status == 'remove':
        ReadingStatus.objects.filter(user_profile=user_profile, manga=manga).delete()
    else:
        ReadingStatus.objects.update_or_create(
            user_profile=user_profile,
            manga=manga,
            defaults={'status': status or 'planned'}
        )

    return redirect('manga:manga_details', manga_slug=manga.slug)


# =========================== Thanks to chapter ===========================

@login_required
def thank_chapter(request, chapter_id):
    chapter = get_object_or_404(Chapter, id=chapter_id)
    user = request.user

    if user in chapter.thanks.all():
        chapter.thanks.remove(user)
        thanked = False
    else:
        chapter.thanks.add(user)
        thanked = True

    count = chapter.thanks.count()

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'thanked': thanked, 'count': count})

    return redirect('manga:chapter_read', chapter_id=chapter.id)


# =========================== Read chapter ===========================

VISITOR_COOKIE = getattr(settings, "MANGALAB_VISITOR_COOKIE", "ml_vid")
VISITOR_COOKIE_MAX_AGE = getattr(settings, "MANGALAB_VISITOR_COOKIE_MAX_AGE", 60 * 60 * 24 * 365)  # 1 yil

def chapter_read(request, manga_slug, volume, chapter_number):
    manga = get_object_or_404(Manga, slug=manga_slug)
    chapter = get_object_or_404(Chapter, manga=manga, volume=volume, chapter_number=chapter_number)

    # --- O‘qishga ruxsat tekshiruvi
    if not can_read(request.user, manga, chapter):
        if not request.user.is_authenticated:
            messages.warning(request, "Bobni o‘qish uchun tizimga kiring!")
            return redirect("accounts:login")
        messages.warning(request, f"Ushbu bob {chapter.price_tanga} tanga turadi. Avval sotib oling.")
        return redirect(
            "manga:purchase_chapter",
            manga_slug=manga.slug,
            volume=chapter.volume,
            chapter_number=chapter.chapter_number,
        )

    # --- boblar ro‘yxati
    all_chapters = list(
        Chapter.objects
        .filter(manga=manga)
        .only("id", "volume", "chapter_number", "price_tanga")
        .order_by("-volume", "-chapter_number")
    )

    previous_chapter = (
        Chapter.objects
        .filter(manga=manga)
        .filter(
            Q(volume=chapter.volume, chapter_number__lt=chapter.chapter_number) |
            Q(volume__lt=chapter.volume)
        )
        .order_by("-volume", "-chapter_number")
        .first()
    )

    next_chapter = (
        Chapter.objects
        .filter(manga=manga)
        .filter(
            Q(volume=chapter.volume, chapter_number__gt=chapter.chapter_number) |
            Q(volume__gt=chapter.volume)
        )
        .order_by("volume", "chapter_number")
        .first()
    )

    next_chapter_price = None
    if request.user.is_authenticated and next_chapter:
        if next_chapter.price_tanga > 0 and not can_read(request.user, manga, next_chapter):
            next_chapter_price = next_chapter.price_tanga

    # =========================================================
    # ✅ KO‘RISHNI YOZIB BORISH (ALL)
    # - login bo‘lsa: ChapterVisit (user+chapter)
    # - anon bo‘lsa: ChapterAnonVisit (visitor_id+chapter) + cookie
    # =========================================================
    new_vid = None
    if request.user.is_authenticated:
        ChapterVisit.objects.get_or_create(user=request.user, chapter=chapter)
    else:
        vid = (request.COOKIES.get(VISITOR_COOKIE) or "").strip()
        if not vid or len(vid) > 36:  # juda uzun/iflos cookie bo‘lsa yangilaymiz
            vid = str(uuid4())
            new_vid = vid
        ChapterAnonVisit.objects.get_or_create(visitor_id=vid, chapter=chapter)

    # --- progress faqat login uchun
    progress = None
    if request.user.is_authenticated:
        progress, created = ReadingProgress.objects.get_or_create(
            user=request.user,
            manga=manga,
            defaults={"last_read_chapter": chapter, "last_read_page": 1},
        )
        if not created:
            prev = progress.last_read_chapter
            if prev is None or (chapter.volume, chapter.chapter_number) > (prev.volume, prev.chapter_number):
                progress.last_read_chapter = chapter
                progress.last_read_page = 1
                progress.save(update_fields=["last_read_chapter", "last_read_page"])

    user_read_chapters = []
    if request.user.is_authenticated:
        user_read_chapters = list(
            ChapterVisit.objects
            .filter(user=request.user, chapter__manga=manga)
            .values_list("chapter_id", flat=True)
        )

    pages = list(chapter.pages.all().order_by("page_number"))
    pages_payload = []
    for p in pages:
        tok = make_page_token(request, p.id)
        secure_url = request.build_absolute_uri(reverse("manga:page_image", args=[p.id, tok]))
        pages_payload.append({"url": secure_url, "alt": f"Sahifa {p.page_number}"})

    purchased_chapters = []
    if request.user.is_authenticated:
        purchased_chapters = list(
            ChapterPurchase.objects
            .filter(user=request.user, chapter__manga=manga)
            .values_list("chapter_id", flat=True)
        )

    is_privileged = (
        request.user.is_authenticated and (
            request.user.is_superuser
            or request.user.is_staff
            or manga.created_by_id == request.user.id
            or _is_translator(request.user)
        )
    )
    if is_privileged:
        readable_chapter_ids = [c.id for c in all_chapters]
    else:
        free_ids = [c.id for c in all_chapters if c.price_tanga == 0]
        readable_chapter_ids = sorted(set(free_ids) | set(purchased_chapters))

    is_last_chapter = (next_chapter is None)

    context = {
        "manga": manga,
        "chapter": chapter,
        "all_chapters": all_chapters,
        "previous_chapter": previous_chapter,
        "next_chapter": next_chapter,
        "next_chapter_price": next_chapter_price,
        "reading_progress": progress,
        "user_read_chapters": user_read_chapters,
        "pages": pages,
        "pages_payload": pages_payload,
        "purchased_chapters": purchased_chapters,
        "readable_chapter_ids": readable_chapter_ids,
        "is_last_chapter": is_last_chapter,
    }

    response = render(request, "manga/chapter_read.html", context)

    # anon bo‘lsa cookie yozamiz
    if new_vid:
        response.set_cookie(
            VISITOR_COOKIE,
            new_vid,
            max_age=VISITOR_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=getattr(settings, "SESSION_COOKIE_SECURE", False),
        )

    return response

# =========================== Taxonomy pages ===========================

def _make_alpha_groups(qs, name_field="name"):
    groups = defaultdict(list)
    for obj in qs:
        name = getattr(obj, name_field) or ""
        first = name.strip()[:1].upper() if name else "#"
        if not first or not first.isalnum():
            first = "#"
        groups[first].append(obj)

    letters = sorted(groups.keys(), key=lambda x: x)
    out = [{"letter": L, "items": sorted(groups[L], key=lambda o: getattr(o, name_field).lower())}
           for L in letters]
    return out


def _taxonomy_context(model_cls, title, qparam, request):
    sort = request.GET.get("sort", "alpha")
    qs = model_cls.objects.annotate(num=Count("mangas", distinct=True))
    total_count = qs.count()

    if sort == "popular":
        items = list(qs.order_by("-num", "name"))
        groups = None
    else:
        items = list(qs.order_by("name"))
        groups = _make_alpha_groups(items)

    return {
        "title": title,
        "qparam": qparam,
        "active_tab": sort,
        "total_count": total_count,
        "groups": groups,
        "items": items,
    }


def genre_index(request):
    ctx = _taxonomy_context(Genre, "Janrlar", "genre", request)
    return render(request, "manga/taxonomy_list.html", ctx)


def tag_index(request):
    ctx = _taxonomy_context(Tag, "Teglar", "tag", request)
    return render(request, "manga/taxonomy_list.html", ctx)


# =========================== Reading now ===========================

def reading_now(request):
    """
    /manga/reading/?tab=latest|trending|popular
    """
    tab = (request.GET.get("tab") or "latest").lower()
    limit = 30

    ctx = {"active_tab": tab, "items": [], "feed": []}

    if tab == "trending":
        cache_key = f"reading_trending_{limit}_v1"
        data = cache.get(cache_key)
        if data is None:
            since = timezone.now() - timedelta(days=7)
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

        ctx["items"] = data

    elif tab == "popular":
        cache_key = f"reading_popular_{limit}_v1"
        data = cache.get(cache_key)
        if data is None:
            since = timezone.now() - timedelta(days=30)
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

        ctx["items"] = data

    else:  # latest
        cache_key = "reading_latest_v1"
        feed = cache.get(cache_key)
        if feed is None:
            feed = _build_recent_feed(limit_titles=30, per_title=3, window_hours=24*7)
            cache.set(cache_key, feed, 60 * 10)

        ctx["feed"] = feed
        ctx["active_tab"] = "latest"

    return render(request, "manga/reading_now.html", ctx)


# =========================== Reading history ===========================

@login_required
def reading_history(request):
    """
    /history/?tab=titles|translators|authors&order=new|old&q=...&page=...
    """
    user  = request.user
    q     = (request.GET.get("q") or "").strip()
    order = (request.GET.get("order") or "new").lower()
    tab   = (request.GET.get("tab") or "titles").lower()

    # visits
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

    # progress
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

    # titles list (raw)
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

    # translators & authors
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
        if not author_name:
            continue
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

    per_page = 10
    paginator = Paginator(items, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))

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


# =========================== History clear/remove ===========================

@login_required
@require_POST
def history_clear(request):
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
    ChapterVisit.objects.filter(user=request.user, chapter__manga_id=manga_id).delete()
    ReadingProgress.objects.filter(user=request.user, manga_id=manga_id).delete()
    messages.success(request, "Tanlangan tayt tarixi o‘chirildi.")
    next_url = request.POST.get("next") or reverse("manga:history")
    return redirect(next_url)
