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
