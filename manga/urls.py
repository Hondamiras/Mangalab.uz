# apps/manga/urls.py
from django.urls import path
from . import views
from manga.service import purchase_chapter

app_name = "manga"

urlpatterns = [
    path("", views.manga_discover, name="discover"),
    path("browse/", views.manga_browse, name="browse"),
    path("random/", views.random_manga, name="random_manga"),

    path("history/", views.reading_history, name="history"),
    path("history/remove/<int:manga_id>/", views.history_remove, name="history_remove"),
    path("history/clear/", views.history_clear, name="history_clear"),

    path("page/<int:page_id>/<str:token>/", views.page_image, name="page_image"),
    path("reading/", views.reading_now, name="reading_now"),

    path("genres/", views.genre_index, name="genre_index"),
    path("tags/", views.tag_index, name="tag_index"),

    # LIKE (sizda slug param nomi 'slug')
    path("manga/<slug:slug>/like/", views.toggle_manga_like, name="manga_like_toggle"),

    # ✅ Rating (fallback + ajax)
    path("m/<slug:manga_slug>/rate/<int:translator_id>/", views.rate_translator, name="rate_translator"),
    
    path("<slug:manga_slug>/", views.manga_details, name="manga_details"),

    # (sizda chapter_read dublikat bo‘lib ketgan — bittasini qoldiring)
    path("<slug:manga_slug>/jild/<int:volume>/bob/<int:chapter_number>/", views.chapter_read, name="chapter_read"),
    path("<slug:manga_slug>/volume/<int:volume>/chapter/<int:chapter_number>/purchase/", purchase_chapter, name="purchase_chapter"),

    path("chapter/<int:chapter_id>/thank/", views.thank_chapter, name="thank_chapter"),
    path("<slug:manga_slug>/add/", views.add_to_reading_list, name="add_to_reading_list"),
]
