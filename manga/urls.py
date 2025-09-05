# apps/manga/urls.py
from django.urls import path
from . import views
from manga.service import purchase_chapter

app_name = "manga"

urlpatterns = [
    # path("", views.manga_list, name="manga_list"),
    path("", views.manga_discover, name="discover"),
    path("browse/", views.manga_browse, name="browse"), # Barcha taytlar (filtr + paginate)
    path("random/", views.random_manga, name="random_manga"),
    
    # YANGI: janr/tag index sahifalari
    path("genres/", views.genre_index, name="genre_index"),
    path("tags/", views.tag_index, name="tag_index"),

    path("<slug:manga_slug>/", views.manga_details, name="manga_details"),
    path("<slug:manga_slug>/jild/<int:volume>/bob/<int:chapter_number>/", views.chapter_read, name="chapter_read"),
    path('chapter/<int:chapter_id>/thank/',views.thank_chapter, name='thank_chapter'),
    path('<slug:manga_slug>/add/', views.add_to_reading_list, name='add_to_reading_list'),
    path('<slug:manga_slug>/volume/<int:volume>/chapter/<int:chapter_number>/purchase/', purchase_chapter, name="purchase_chapter"),

    path('<slug:manga_slug>/volume/<int:volume>/chapter/<int:chapter_number>/', views.chapter_read, name='chapter_read'),
    path("manga/<slug:slug>/like/", views.toggle_manga_like, name="manga_like_toggle"),
]
