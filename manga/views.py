# apps/manga/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.urls import reverse
from django.db.models import Prefetch
import os
from django.conf import settings
from .models import ChapterContributor, Manga, Chapter, Genre, ReadingProgress, Tag
from accounts.models import ReadingStatus, UserProfile, READING_STATUSES

# ====== списки ==============================================================

def manga_list(request):
    # 1) Получаем профиль пользователя
    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    else:
        user_profile = None

    # 2) Базовый queryset
    qs = Manga.objects.all()

    # 0) Поиск по ключевому слову
    search_query = request.GET.get('search', '').strip()
    if search_query:
        qs = qs.filter(
            Q(title__icontains=search_query) |
            Q(author__icontains=search_query) |
            Q(description__icontains=search_query)
        ).distinct()

    # 1) Фильтры‐чекбоксы
    genre_filter_list      = request.GET.getlist('genre')
    age_rating_filter_list = request.GET.getlist('age_rating')
    type_filter_list       = request.GET.getlist('type')
    tag_filter_list        = request.GET.getlist('tag')
    status_filter_list     = request.GET.getlist('status')
    translation_filter_list = request.GET.getlist('translation_status')

    if genre_filter_list:
        qs = qs.filter(genres__name__in=genre_filter_list).distinct()
    if age_rating_filter_list:
        qs = qs.filter(age_rating__in=age_rating_filter_list)
    if type_filter_list:
        qs = qs.filter(type__in=type_filter_list)
    if tag_filter_list:
        qs = qs.filter(tags__name__in=tag_filter_list).distinct()
    if status_filter_list:
        qs = qs.filter(status__in=status_filter_list)
    if translation_filter_list:
        qs = qs.filter(translation_status__in=translation_filter_list)

    # 2) Диапазон по количеству глав
    min_chap = request.GET.get('min_chapters')
    max_chap = request.GET.get('max_chapters')
    if min_chap or max_chap:
        qs = qs.annotate(chap_count=Count('chapters'))
        # проверяем, что min_chap не пустая строка и int(min_chap) > 0
        if min_chap:
            try:
                iv = int(min_chap)
                if iv > 0:
                    qs = qs.filter(chap_count__gte=iv)
            except ValueError:
                pass
        # аналогично для max_chap
        if max_chap:
            try:
                iv = int(max_chap)
                if iv > 0:
                    qs = qs.filter(chap_count__lte=iv)
            except ValueError:
                pass

    # 3) Диапазон по году публикации
    min_year = request.GET.get('min_year')
    max_year = request.GET.get('max_year')
    if min_year:
        try:
            iy = int(min_year)
            # фильтруем ТОЛЬКО если год >= 1
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

    # 4) Сортировка
    sort = request.GET.get('sort', 'chapters')
    if sort == 'chapters':
        # Если не аннотировали выше, аннотируем и сортируем
        qs = qs.annotate(chap_count=Count('chapters')).order_by('-chap_count', 'title')
    elif sort == 'title_asc':
        qs = qs.order_by('title')
    elif sort == 'title_desc':
        qs = qs.order_by('-title')
    else:
        qs = qs.order_by('title')

    # 5) Подгрузка ReadingStatus текущего пользователя
    qs = qs.prefetch_related(
        Prefetch(
            'readingstatus_set',
            queryset=ReadingStatus.objects.filter(user_profile=user_profile),
            to_attr='user_status'
        )
    )

    # 6) Пагинация
    paginator = Paginator(qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    # 7) Подготовка справочников для шаблона
    status_choices      = Manga._meta.get_field('status').choices
    age_rating_choices  = Manga._meta.get_field('age_rating').choices
    type_choices        = Manga._meta.get_field('type').choices
    translation_choices = Manga._meta.get_field('translation_status').choices

    return render(request, 'manga/manga_list.html', {
        'genres': Genre.objects.all(),
        'tags': Tag.objects.all(),
        'page_obj': page_obj,
        'search': search_query,
        'sort': sort,

        # выбранные фильтры
        'genre_filter_list': genre_filter_list,
        'tag_filter_list': tag_filter_list,
        'age_rating_filter_list': age_rating_filter_list,
        'type_filter_list': type_filter_list,
        'status_filter_list': status_filter_list,
        'translation_filter_list': translation_filter_list,

        # диапазоны
        'min_chapters': request.GET.get('min_chapters', ''),
        'max_chapters': request.GET.get('max_chapters', ''),
        'min_year':     request.GET.get('min_year', ''),
        'max_year':     request.GET.get('max_year', ''),

        # выборки для чекбоксов
        'status_choices':      status_choices,
        'age_rating_choices':  age_rating_choices,
        'type_choices':        type_choices,
        'translation_choices': translation_choices,
    })

# ====== детали манги ========================================================
def manga_details(request, manga_slug):
    """
    Показывает страницу деталей конкретной манги.
    Подтягивает статус чтения пользователя, первый эпизод,
    рекомендуемые манги и поддерживает сортировку списка глав.
    """
    manga = get_object_or_404(Manga, slug=manga_slug)

    # 1) Статус чтения пользователя
    reading_status = None
    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
        reading_status = ReadingStatus.objects.filter(
            user_profile=user_profile,
            manga=manga
        ).first()

    # 2) Сортировка глав по GET-параметру ?order=asc|desc (учитывая номер тома и главы)
    order = request.GET.get('order', 'desc')
    if order == 'asc':
        # Сначала по возрастанию томов, внутри тома — по возрастанию номера главы
        chapters = manga.chapters.order_by('volume', 'chapter_number')
    else:
        # Сначала по возрастанию томов, внутри тома — по убыванию номера главы
        chapters = manga.chapters.order_by('-volume', '-chapter_number')

    # 3) Первый доступный эпизод (том 1, глава 1)
    first_chapter = manga.chapters.order_by('volume', 'chapter_number').first()

    # 4) Рекомендации: манги с пересечением жанров
    user_genres = manga.genres.all()
    similar_mangas = (
        Manga.objects
             .exclude(pk=manga.pk)
             .annotate(
                 shared_genres=Count(
                     'genres',
                     filter=Q(genres__in=user_genres),
                     distinct=True
                 )
             )
             .filter(shared_genres__gt=0)
             .order_by('-shared_genres', 'title')[:10]
    )

    return render(request, 'manga/manga_details.html', {
        'manga': manga,
        'reading_status': reading_status,
        'first_chapter': first_chapter,
        'chapters': chapters,
        'current_order': order,
        'similar_mangas': similar_mangas,
        'READING_STATUSES': READING_STATUSES,
    })

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

def chapter_read(request, manga_slug, volume, chapter_number):
    """
    Отображает страницу чтения указанной главы.
    Если PDF был сконвертирован в WebP-страницы — отдаём их как <img loading="lazy">.
    """
    # 1. Получаем мангу и главу
    manga = get_object_or_404(Manga, slug=manga_slug)
    chapter = get_object_or_404(
        Chapter,
        manga=manga,
        volume=volume,
        chapter_number=chapter_number
    )

    # 2. Список всех глав (для выпадающего меню, навигации)
    all_chapters = Chapter.objects.filter(manga=manga) \
                                  .order_by('volume', 'chapter_number')

    # 3. Вычисляем previous/next в рамках тома и между томами
    previous_chapter = (
        Chapter.objects
        .filter(manga=manga, volume=volume, chapter_number__lt=chapter_number)
        .order_by('-chapter_number')
        .first()
    )
    if not previous_chapter and volume > 1:
        previous_chapter = (
            Chapter.objects
            .filter(manga=manga, volume=volume-1)
            .order_by('-chapter_number')
            .first()
        )

    next_chapter = (
        Chapter.objects
        .filter(manga=manga, volume=volume, chapter_number__gt=chapter_number)
        .order_by('chapter_number')
        .first()
    )
    if not next_chapter:
        next_chapter = (
            Chapter.objects
            .filter(manga=manga, volume=volume+1)
            .order_by('chapter_number')
            .first()
        )

    # 4. Обновляем прогресс чтения
    progress = None
    if request.user.is_authenticated:
        progress, created = ReadingProgress.objects.get_or_create(
            user=request.user,
            manga=manga,
            defaults={'last_read_chapter': chapter, 'last_read_page': 1}
        )
        if not created:
            last = progress.last_read_chapter or chapter
            if (volume > getattr(last, 'volume', 0)
                or (volume == getattr(last, 'volume', 0)
                    and chapter_number > getattr(last, 'chapter_number', 0))
            ):
                progress.last_read_chapter = chapter
                progress.save()

    # 5. Собираем URL всех WebP-страниц
    page_images = []
    pages_dir = os.path.join(
        settings.MEDIA_ROOT,
        'chapters', 'pages', str(chapter.id)
    )
    if os.path.isdir(pages_dir):
        for fname in sorted(os.listdir(pages_dir)):
            if fname.lower().endswith('.webp'):
                page_images.append(
                    settings.MEDIA_URL + f'chapters/pages/{chapter.id}/{fname}'
                )

    # 6. Списки по ролям
    translators = ChapterContributor.objects.filter(
        chapter=chapter, role='translator'
    ).select_related('contributor')
    cleaners = ChapterContributor.objects.filter(
        chapter=chapter, role='cleaner'
    ).select_related('contributor')
    typers = ChapterContributor.objects.filter(
        chapter=chapter, role='typer'
    ).select_related('contributor')

    # 7. Рендерим шаблон
    return render(request, 'manga/chapter_read.html', {
        'manga': manga,
        'chapter': chapter,
        'all_chapters': all_chapters,
        'previous_chapter': previous_chapter,
        'next_chapter': next_chapter,
        'reading_progress': progress,
        'page_images': page_images,
        'translators': translators,
        'cleaners': cleaners,
        'typers': typers,
    })
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