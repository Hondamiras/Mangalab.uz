from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm

from manga.models import ReadingProgress

from .forms import SignupForm
from .models import PendingSignup, UserProfile, ReadingStatus

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
            messages.success(request, "Akkountingiz faollashtirildi, tizimga kirdingiz.")
            return redirect("manga:manga_list")

    return render(request, "accounts/verify_code.html", {
        "pid": pid,
        "error": error
    })

def login_view(request):
    form = AuthenticationForm(data=request.POST or None)
    if form.is_valid():
        login(request, form.get_user())
        messages.success(request, "Tizimga muvaffaqiyatli kirdingiz")  # «Вы успешно вошли»
        return redirect("manga:manga_list")
    return render(request, "accounts/login.html", {"form": form})


@login_required
def logout_view(request):
    logout(request)
    messages.info(request, "Siz tizimdan chiqdingiz")  # «Вы вышли из системы»
    return redirect("manga:manga_list")

# ------------------------------------------------------------------#
#                           ПРОФИЛИ                                 #
# ------------------------------------------------------------------#

@login_required
def profile_view(request):
    """Показывает профиль текущего пользователя."""
    # Получаем или создаём профиль
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Список чтения
    reading_statuses = (
        ReadingStatus.objects
        .filter(user_profile=user_profile)
        .select_related('manga')
        .order_by('status')         # <-- сортировка по полю status
    )

    # Прогресс чтения (последняя глава + страница)
    reading_progress = (
        ReadingProgress.objects
        .filter(user=request.user)
        .select_related('manga', 'last_read_chapter')
        .order_by('-updated_at')
    )

    return render(
        request,
        "accounts/profile.html",
        {
            'profile_user': request.user,
            'user_profile': user_profile,
            'reading_statuses': reading_statuses,
            'reading_progress': reading_progress,
            'is_self': True,
        },
    )
