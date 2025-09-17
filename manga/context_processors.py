# apps/manga/context_processors.py
from django.core.cache import cache
from django.conf import settings
from .models import Genre, Tag, Manga

# --- Katalog turlari (mavjudlar, label bilan) ------------------------------
CATALOG_TYPES_CACHE_KEY = "catalog_types_v1"
CATALOG_TYPES_TTL = 60 * 10  # 10 min

def catalog_context(request):
    data = cache.get(CATALOG_TYPES_CACHE_KEY)
    if data is None:
        present = set(Manga.objects.values_list("type", flat=True).distinct())
        data = [
            {"key": key, "label": label}
            for key, label in Manga._meta.get_field("type").choices
            if key in present
        ]
        cache.set(CATALOG_TYPES_CACHE_KEY, data, CATALOG_TYPES_TTL)
    return {"CATALOG_TYPES": data}


# --- Content filter modal uchun kontekst -----------------------------------
CF_CHOICES_CACHE_KEY = "content_filter_choices_v1"
CF_CHOICES_TTL = getattr(settings, "CONTENT_FILTER_TTL", 60 * 60 * 24)  # default 24 soat

def content_filter_context(request):
    # Sessiyadagi tanlanganlar
    cf = request.session.get("content_filter") or {"types": [], "genres": [], "tags": []}

    # Modal variantlari (kesh bilan)
    choices = cache.get(CF_CHOICES_CACHE_KEY)
    if choices is None:
        choices = {
            "TYPE_CHOICES": [k for k, _ in Manga._meta.get_field("type").choices],
            "ALL_GENRES": list(Genre.objects.order_by("name").values_list("name", flat=True)),
            "ALL_TAGS":   list(Tag.objects.order_by("name").values_list("name", flat=True)),
        }
        cache.set(CF_CHOICES_CACHE_KEY, choices, CF_CHOICES_TTL)

    # UI’da ko‘rsatish uchun kesh kechikishlari
    cf_ttls = {
        "browse_anon_min":      15,
        "reading_latest_min":   10,
        "reading_trending_min": 15,
        "discover_recent_min":  30,
        "choices_hours":        max(1, CF_CHOICES_TTL // 3600),
    }

    # Agar ayrim templatelar (value,label) juftligini xohlaydigan bo‘lsa,
    # shu yerda bir marta beramiz — qo‘shimcha processor shart emas.
    manga_type_choices = list(Manga._meta.get_field("type").choices)

    return {
        "content_filter": cf,
        **choices,                 # TYPE_CHOICES, ALL_GENRES, ALL_TAGS
        "CF_TTLS": cf_ttls,
        "MANGA_TYPE_CHOICES": manga_type_choices,
    }
