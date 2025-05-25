# apps/manga/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.urls import reverse
from django.db.models import Prefetch

from .models import Manga, Chapter, Genre, ReadingProgress, Tag
from accounts.models import ReadingStatus, UserProfile, READING_STATUSES

# ====== списки ==============================================================
def manga_list(request):
    # Показывает список манги с фильтрами и сортировкой.
    # Если пользователь авторизован — подтягивает его статус чтения и закладки.
    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    else:
        user_profile = None
    
    qs = Manga.objects.all()

    # ———————————————————
    # 0) Поиск
    search_query = request.GET.get('search', '').strip()
    if search_query:
        qs = qs.filter(
            Q(title__icontains=search_query) |
            Q(author__icontains=search_query) |
            Q(description__icontains=search_query)
        ).distinct()
    # ———————————————————

    # 1) Multi‐checkbox filters
    genre_filter_list      = request.GET.getlist('genre')
    age_rating_filter_list = request.GET.getlist('age_rating')
    type_filter_list       = request.GET.getlist('type')
    tag_filter_list = request.GET.getlist('tag')
    status_filter_list = request.GET.getlist('status')
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


    # 2) Range filters
    min_chap = request.GET.get('min_chapters')
    max_chap = request.GET.get('max_chapters')
    if min_chap or max_chap:
        qs = qs.annotate(chap_count=Count('chapters'))
        if min_chap:
            qs = qs.filter(chap_count__gte=int(min_chap))
        if max_chap:
            qs = qs.filter(chap_count__lte=int(max_chap))

    min_year = request.GET.get('min_year')
    max_year = request.GET.get('max_year')
    if min_year:
        qs = qs.filter(publication_date__year__gte=int(min_year))
    if max_year:
        qs = qs.filter(publication_date__year__lte=int(max_year))

    # … add other ranges the same way …

    # 2) Сортировка
    sort = request.GET.get('sort', 'chapters')  # значение по умолчанию
    if sort == 'chapters':
        # сортируем по количеству глав (аннотируем, если ещё не делали)
        qs = qs.annotate(chap_count=Count('chapters')).order_by('-chap_count', 'title')
    elif sort == 'title_asc':
        qs = qs.order_by('title')
    elif sort == 'title_desc':
        qs = qs.order_by('-title')
    else:
        # на всякий случай — чтобы не сломать вьюху
        qs = qs.order_by('title')

    # подгружаем для каждого Manga его ReadingStatus (0 или 1 запись)
    qs = qs.prefetch_related(
        Prefetch(
            'readingstatus_set',
            queryset=ReadingStatus.objects.filter(user_profile=user_profile),
            to_attr='user_status'  # будет доступно как manga.user_status
        )
    )

    # Pagination
    paginator  = Paginator(qs, 10)
    page_obj   = paginator.get_page(request.GET.get('page'))

    # Prepare choices for template
    status_choices      = Manga._meta.get_field('status').choices
    age_choices         = Manga._meta.get_field('age_rating').choices
    type_choices        = Manga._meta.get_field('type').choices

    return render(request, 'manga/manga_list.html', {
    'genres': Genre.objects.all(),
    'tags': Tag.objects.all(),
    'page_obj': page_obj,
    'search': search_query,

    # сортировка
    'sort': sort,

    # список выбранных фильтров для чекбоксов
    'genre_filter_list': request.GET.getlist('genre'),
    'tag_filter_list':    request.GET.getlist('tag'),
    'age_rating_filter_list': request.GET.getlist('age_rating'),
    'type_filter_list': request.GET.getlist('type'),
    'format_filter_list': request.GET.getlist('format'),
    'status_filter_list': request.GET.getlist('status'),
    'translation_filter_list': request.GET.getlist('translation_status'),
    'other_filter_list': request.GET.getlist('other'),
    'mylist_filter_list': request.GET.getlist('mylist'),

    # диапазоны
    'min_chapters': request.GET.get('min_chapters',''),
    'max_chapters': request.GET.get('max_chapters',''),
    'min_year':     request.GET.get('min_year',''),
    'max_year':     request.GET.get('max_year',''),
    'min_score':    request.GET.get('min_score',''),
    'max_score':    request.GET.get('max_score',''),
    'min_votes':    request.GET.get('min_votes',''),
    'max_votes':    request.GET.get('max_votes',''),

    # сами списки выбора
    'age_rating_choices': Manga._meta.get_field('age_rating').choices,
    'type_choices':        Manga._meta.get_field('type').choices,
    # 'format_choices':      Manga._meta.get_field('format').choices,
    'status_choices':      Manga._meta.get_field('status').choices,
    'translation_choices': Manga._meta.get_field('translation_status').choices,
    'other_choices': [      # пример
        ('no_translation_3m','Нет перевода >3 мес'),
        ('licensed','Лицензирован'),
        ('for_sale','Можно приобрести'),
    ],
    'mylist_choices': [     # пример
        ('reading','Читаю'),
        ('planned','В планах'),
        ('dropped','Брошено'),
        ('completed','Прочитано'),
        ('favorite','Любимые'),
        ('ongoing','Продолжается'),
    ],
})

# ====== детали манги ========================================================
def manga_details(request, manga_slug):
    """
    Показывает страницу деталей конкретной манги.
    Если пользователь авторизован — подтягивает его статус чтения и закладки.
    """
    manga = get_object_or_404(Manga, slug=manga_slug)

    reading_status = None

    if request.user.is_authenticated:
        # Получаем или создаём профиль
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

        # Статус чтения (или None)
        reading_status = ReadingStatus.objects.filter(
            user_profile=user_profile,
            manga=manga
        ).first()

    first_chapter = manga.chapters.order_by('chapter_number').first()

    # Для рекомендованного списка: ищем манги с пересечением жанров
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
        'first_chapter': first_chapter,
        'reading_status': reading_status,
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
@login_required  # если страница должна быть только для залогиненных
def chapter_read(request, chapter_id):
    chapter = get_object_or_404(Chapter, id=chapter_id)
    pages = chapter.pages.order_by('page_number')

    all_chapters = Chapter.objects.filter(
        manga=chapter.manga
    ).order_by('volume', 'chapter_number')

    # найти «предыдущую» и «следующую» для кнопок навигации
    previous_chapter = (
        Chapter.objects
        .filter(manga=chapter.manga, chapter_number__lt=chapter.chapter_number)
        .order_by('-chapter_number')
        .first()
    )
    next_chapter = (
        Chapter.objects
        .filter(manga=chapter.manga, chapter_number__gt=chapter.chapter_number)
        .order_by('chapter_number')
        .first()
    )

    # --- Новое: обновляем или создаём прогресс ---
    progress, _ = ReadingProgress.objects.get_or_create(
        user=request.user,
        manga=chapter.manga,
        defaults={'last_read_chapter': chapter, 'last_read_page': 1}
    )
    # если прогресс уже был и это более новая глава — обновляем
    if (not progress.last_read_chapter) or (chapter.chapter_number > progress.last_read_chapter.chapter_number):
        progress.last_read_chapter = chapter
        progress.save()

    return render(request, 'manga/chapter_read.html', {
        'manga': chapter.manga,
        'chapter': chapter,
        'pages': pages,
        'previous_chapter': previous_chapter,
        'next_chapter': next_chapter,
        'all_chapters': all_chapters,
        'reading_progress': progress,
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