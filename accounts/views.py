from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm

from manga.models import Chapter, ChapterPurchase, Manga, ReadingProgress

from .forms import SignupForm
from .models import PendingSignup, TranslatorFollower, UserProfile, ReadingStatus

User = get_user_model()

# ------------------------------------------------------------------#
#                      АВТОРИЗАЦИЯ / РЕГИСТРАЦИЯ                     #
# ------------------------------------------------------------------#
import secrets
from django.shortcuts import render, redirect
from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.core.mail import send_mail
from django.utils import timezone

from .forms import SignupForm
from .models import EmailVerificationCode
def signup_view(request):
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        username    = form.cleaned_data["username"]
        email       = form.cleaned_data["email"]
        raw_password = form.cleaned_data["password1"]

        # Удаляем старые PendingSignup с тем же username/email
        PendingSignup.objects.filter(username=username).delete()
        PendingSignup.objects.filter(email=email).delete()

        # Создаём новую PendingSignup и хэшируем пароль
        pending = PendingSignup(
            username=username,
            email=email,
            code=f"{secrets.randbelow(10**6):06d}"
        )
        pending.save_password(raw_password)
        pending.save()

        # Отправляем письмо с кодом
        subject = "Akkountingizni faollashtirish kodi - MangaLab"
        message = (
            f"Salom, {username}!\n\n"
            f"Akkountingizni faollashtirish uchun kodingiz: {pending.code}\n\n"
            "15 daqiqa davomida amal qiladi."
        )
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False
        )

        return redirect(reverse("accounts:verify_code") + f"?pid={pending.pk}")

    return render(request, "accounts/signup.html", {"form": form})

def verify_code_view(request):
    error = None
    pid = request.GET.get("pid")

    if request.method == "POST":
        pid = request.POST.get("pid")
        code_entered = request.POST.get("code", "").strip()

        try:
            pending = PendingSignup.objects.get(pk=pid)
        except PendingSignup.DoesNotExist:
            pending = None

        if not pending or pending.is_expired() or pending.code != code_entered:
            error = "Kod noto'g'ri yoki amal qilish muddati tugagan."
        else:
            # Создаём реального пользователя с готовым хэшем пароля
            user = User.objects.create(
                username=pending.username,
                email=pending.email,
                password=pending.password_hash,
                is_active=True
            )
            pending.delete()
            login(request, user)
            messages.success(request, "Hisobingiz faollashtirildi, tizimga kirdingiz.")
            return redirect("manga:discover")

    return render(request, "accounts/verify_code.html", {
        "pid": pid,
        "error": error
    })

def login_view(request):
    form = AuthenticationForm(data=request.POST or None)
    if form.is_valid():
        login(request, form.get_user())
        messages.success(request, "Tizimga muvaffaqiyatli kirdingiz")  # «Вы успешно вошли»
        return redirect("manga:discover")
    return render(request, "accounts/login.html", {"form": form})


@login_required
def logout_view(request):
    logout(request)
    messages.info(request, "Siz tizimdan chiqdingiz")
    return redirect("manga:discover")

# ------------------------------------------------------------------#
#                           ПРОФИЛИ                                 #
# ------------------------------------------------------------------#

from django.db.models import Count, Sum

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Count, Sum

from manga.models import Manga, ChapterPurchase

@login_required
def profile_view(request):
    """Foydalanuvchi profilini ko‘rsatadi (oddiy yoki tarjimon)."""
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # --- Agar foydalanuvchi tarjimon bo‘lsa ---
    if user_profile.is_translator:
        # Tarjimon yaratgan taytllar (boblar soni bilan)
        mangas = Manga.objects.filter(created_by=request.user).annotate(
            chapter_count=Count('chapters', distinct=True)
        )

        # Har bir manga uchun layklar sonini alohida hisoblash
        likes_per_manga = (
            Chapter.objects.filter(manga__in=mangas)
            .values('manga_id')
            .annotate(total_likes=Count('thanks'))
        )
        likes_dict = {x['manga_id']: x['total_likes'] for x in likes_per_manga}
        for manga in mangas:
            manga.total_likes = likes_dict.get(manga.id, 0)

        # Followerlar soni
        follower_count = TranslatorFollower.objects.filter(translator=user_profile).count()

        # Tarjimonning jami daromadi
        total_income = (
            ChapterPurchase.objects.filter(chapter__manga__created_by=request.user)
            .aggregate(total_earned=Sum('chapter__price_tanga'))
            .get('total_earned') or 0
        )

        # Boblar bo‘yicha sotib olish statistikasi
        chapter_earnings = (
            ChapterPurchase.objects.filter(chapter__manga__created_by=request.user)
            .values('chapter__manga__title', 'chapter__volume', 'chapter__chapter_number')
            .annotate(
                total_earned=Sum('chapter__price_tanga'),
                buyers=Count('id')
            )
            .order_by('chapter__manga__title', 'chapter__volume', 'chapter__chapter_number')
        )

        return render(request, "accounts/translators/translator_profile_owner.html", {
            'profile_user': request.user,
            'user_profile': user_profile,
            'mangas': mangas,
            'follower_count': follower_count,
            'total_income': total_income,
            'chapter_earnings': chapter_earnings,
            'is_self': True,
        })

    # --- Oddiy foydalanuvchi uchun ---
    reading_statuses = (
        ReadingStatus.objects
        .filter(user_profile=user_profile)
        .select_related('manga')
        .order_by('status')
    )
    reading_progress = (
        ReadingProgress.objects
        .filter(user=request.user)
        .select_related('manga', 'last_read_chapter')
        .order_by('-updated_at')
    )

    return render(request, "accounts/profile.html", {
        'profile_user': request.user,
        'user_profile': user_profile,
        'reading_statuses': reading_statuses,
        'reading_progress': reading_progress,
        'is_self': True,
    })


from django.db.models import Sum, Count, F

@login_required
def translator_profile_view(request, username):
    profile_user = get_object_or_404(User, username=username)
    user_profile = get_object_or_404(UserProfile, user=profile_user, is_translator=True)

    mangas = Manga.objects.filter(created_by=profile_user).annotate(
        chapter_count=Count('chapters', distinct=True)
    )
    likes_per_manga = (
        Chapter.objects.filter(manga__in=mangas)
        .values('manga_id')
        .annotate(total_likes=Count('thanks'))
    )
    likes_dict = {x['manga_id']: x['total_likes'] for x in likes_per_manga}
    for manga in mangas:
        manga.total_likes = likes_dict.get(manga.id, 0)

    follower_count = TranslatorFollower.objects.filter(translator=user_profile).count()
    is_following = (
        request.user.is_authenticated and request.user != profile_user and
        TranslatorFollower.objects.filter(translator=user_profile, user=request.user.userprofile).exists()
    )

    return render(request, "accounts/translators/translator_profile_public.html", {
        "profile_user": profile_user,
        "user_profile": user_profile,
        "mangas": mangas,
        "follower_count": follower_count,
        "is_following": is_following,
    })


@login_required
def translator_profile_owner_view(request):
    profile_user = request.user
    user_profile = get_object_or_404(UserProfile, user=profile_user, is_translator=True)

    # Mangalar (boblar soni + layklar soni)
    mangas = (
        Manga.objects.filter(created_by=profile_user)
        .annotate(
            chapter_count=Count('chapters', distinct=True),
            total_likes=Count('chapters__thanks', distinct=True)  # xuddi publicdagi kabi
        )
    )

    # Jami daromad
    total_income = (
        ChapterPurchase.objects.filter(chapter__manga__created_by=profile_user)
        .aggregate(total_earned=Sum('chapter__price_tanga'))
        .get('total_earned') or 0
    )

    # Boblar bo‘yicha sotib olish statistikasi
    chapter_earnings = (
        ChapterPurchase.objects.filter(chapter__manga__created_by=profile_user)
        .values('chapter__manga__title', 'chapter__volume', 'chapter__chapter_number')
        .annotate(
            total_earned=Sum('chapter__price_tanga'),
            buyers=Count('id')
        )
        .order_by('chapter__manga__title', 'chapter__volume', 'chapter__chapter_number')
    )

    # Followerlar soni
    follower_count = TranslatorFollower.objects.filter(translator=user_profile).count()

    return render(request, "accounts/translators/translator_profile_owner.html", {
        "profile_user": profile_user,
        "user_profile": user_profile,
        "mangas": mangas,
        "follower_count": follower_count,
        "total_income": total_income,
        "chapter_earnings": chapter_earnings,
    })


@login_required
def follow_translator(request, username):
    translator_profile = get_object_or_404(UserProfile, user__username=username, is_translator=True)
    user_profile = request.user.userprofile

    # Agar allaqachon obuna bo'lsa → bekor qilish
    if TranslatorFollower.objects.filter(translator=translator_profile, user=user_profile).exists():
        TranslatorFollower.objects.filter(translator=translator_profile, user=user_profile).delete()
        messages.info(request, f"Siz {translator_profile.user.username} tarjimoniga obunani bekor qildingiz.")
    else:
        TranslatorFollower.objects.create(translator=translator_profile, user=user_profile)
        messages.success(request, f"Siz {translator_profile.user.username} tarjimoniga obuna bo‘ldingiz.")

    return redirect("accounts:translator_profile", username=username)


@login_required
def top_translators(request):
    translators = (
        UserProfile.objects.filter(is_translator=True)
        .annotate(
            manga_count=Count("user__mangas_created", distinct=True),
            follower_count=Count("followers", distinct=True),
            likes_count=Count("user__mangas_created__chapters__thanks"),
        )
        .order_by("-likes_count", "-follower_count")
    )

    for t in translators:
        t.likes_count = t.likes_count

    return render(
        request,
        "accounts/translators/top_translators.html",
        {"translators": translators},
    )

