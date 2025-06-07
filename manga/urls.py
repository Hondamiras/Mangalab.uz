# apps/manga/urls.py
from django.urls import path
from . import views

app_name = "manga"

urlpatterns = [
    path("", views.manga_list, name="manga_list"),
    path("<slug:manga_slug>/", views.manga_details, name="manga_details"),
    path("<slug:manga_slug>/jild/<int:volume>/bob/<int:chapter_number>/",views.chapter_read, name="chapter_read"),
    path('chapter/<int:chapter_id>/thank/',views.thank_chapter, name='thank_chapter'),
    path('<slug:manga_slug>/add/', views.add_to_reading_list, name='add_to_reading_list'),
]
