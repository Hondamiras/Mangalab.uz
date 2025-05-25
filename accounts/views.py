from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm

from manga.models import ReadingProgress

from .forms import SignupForm
from .models import UserProfile, ReadingStatus

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
        # 1) Сохраняем пользователя неактивным
        user = form.save(commit=False)
        user.is_active = False
        user.save()

        # 2) Генерируем код (6 цифр)
        code = f"{secrets.randbelow(10**6):06d}"

        # 3) Сохраняем/перезаписываем код
        EmailVerificationCode.objects.update_or_create(
            user=user,
            defaults={"code": code, "created": timezone.now()}
        )

        # 4) Отправляем письмо
        subject = "Ваш код подтверждения MyManga"
        message = (
            f"Привет, {user.username}!\n\n"
            f"Ваш код для активации аккаунта: {code}\n\n"
            "Он действителен 15 минут."
        )
        send_mail(subject, message,
                  settings.DEFAULT_FROM_EMAIL,
                  [user.email],
                  fail_silently=False)

        # 5) Редиректим на ввод кода
        return redirect(reverse("accounts:verify_code") + f"?uid={user.pk}")
    return render(request, "accounts/signup.html", {"form": form})

def verify_code_view(request):
    error = None
    uid = request.GET.get("uid")
    if request.method == "POST":
        uid = request.POST.get("uid")
        code = request.POST.get("code", "").strip()
        try:
            user = User.objects.get(pk=uid)
            ev = EmailVerificationCode.objects.get(user=user)
        except (User.DoesNotExist, EmailVerificationCode.DoesNotExist):
            user = ev = None

        if not user or ev.is_expired() or ev.code != code:
            error = "Неверный или просроченный код."
        else:
            user.is_active = True
            user.save()
            ev.delete()                  # удаляем уже использованный код
            login(request, user)
            messages.success(request, "Аккаунт активирован, вы вошли в систему.")
            return redirect("manga:manga_list")

    return render(request, "accounts/verify_code.html", {
        "uid": uid,
        "error": error
    })

def login_view(request):
    form = AuthenticationForm(data=request.POST or None)
    if form.is_valid():
        login(request, form.get_user())
        return redirect("manga:manga_list")
    return render(request, "accounts/login.html", {"form": form})


@login_required
def logout_view(request):
    logout(request)
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
