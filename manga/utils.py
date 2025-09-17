# ---- Kontent filtrini qo‘llash (Manga queryset'iga) ------------------------
from django.db.models import Q

from manga.models import Genre, Manga, Tag

def apply_content_filter(qs, request):
    cf = request.session.get("content_filter") or {}
    types  = cf.get("types")  or []
    genres = cf.get("genres") or []
    tags   = cf.get("tags")   or []

    if types:
        qs = qs.exclude(type__in=types)
    if genres:
        qs = qs.exclude(genres__name__in=genres).distinct()
    if tags:
        qs = qs.exclude(tags__name__in=tags).distinct()
    return qs


# ==== Content Filter helpers (listlarga qo‘llash) ====

def cf_should_hide_manga(m, request) -> bool:
    """Sessiondagi content_filter bo‘yicha ushbu manga yashirilishi kerakmi?"""
    if not m:
        return False
    cf = request.session.get("content_filter") or {}
    types  = set(cf.get("types")  or [])
    genres = set(cf.get("genres") or [])
    tags   = set(cf.get("tags")   or [])

    # type
    if types and getattr(m, "type", None) in types:
        return True
    # genres / tags (tezkor .exists())
    if genres and m.genres.filter(name__in=genres).exists():
        return True
    if tags and m.tags.filter(name__in=tags).exists():
        return True
    return False


def cf_filter_list(items, request, key=None):
    """
    items: list (Manga yoki dict)
    key:   dict bo‘lsa — mangaga boradigan kalit nomi (masalan, 'manga')
    """
    out = []
    for it in items:
        m = it if key is None else it.get(key)
        if m is None or not cf_should_hide_manga(m, request):
            out.append(it)
    return out
