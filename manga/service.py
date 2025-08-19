from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

from accounts.models import UserProfile
from manga.models import Manga, Chapter, ChapterPurchase


def _is_translator(user) -> bool:
    try:
        return getattr(user.userprofile, "is_translator", False)
    except UserProfile.DoesNotExist:
        return False


@login_required
def purchase_chapter(request, manga_slug, volume, chapter_number):
    manga = get_object_or_404(Manga, slug=manga_slug)
    chapter = get_object_or_404(Chapter, manga=manga, volume=volume, chapter_number=chapter_number)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # — agar allaqachon sotib olingan bo'lsa
    if ChapterPurchase.objects.filter(user=request.user, chapter=chapter).exists():
        return JsonResponse({
            "success": False,
            "message": "Siz bu bobni allaqachon sotib olgansiz."
        })

    # --- Tarjimonlar o'zaro bepul (owner ham tarjimon bo'lsa),
    # --- superuser esa har doim bepul,
    # --- va o'z boblari har doim bepul.
    owner_user = manga.created_by
    owner_is_translator = _is_translator(owner_user) if owner_user else False
    user_is_translator = _is_translator(request.user)

    if (
        request.user.is_superuser
        or (owner_user and owner_user == request.user)
        or (user_is_translator and owner_is_translator)
    ):
        ChapterPurchase.objects.create(user=request.user, chapter=chapter)
        # Eslatma: bepul holatda balanslar o'zgarmaydi
        return JsonResponse({
            "success": True,
            "message": "Tarjimonlar/superuser uchun bepul ochildi!"
        })

    # — balans yetarli emas
    if profile.tanga_balance < chapter.price_tanga:
        return JsonResponse({
            "success": False,
            "message": "Tangangiz yetarli emas!"
        })

    # — pullik xarid (daromad manga egasiga yoziladi)
    with transaction.atomic():
        # Agar bir vaqtda ikki marta bosilsa ham, unique_together tufayli dublikat bo‘lmaydi
        ChapterPurchase.objects.create(user=request.user, chapter=chapter)

        profile.tanga_balance -= chapter.price_tanga
        profile.save(update_fields=["tanga_balance"])

        # daromadni manga egasiga o'tkazish
        if owner_user and owner_user != request.user:
            owner_profile, _ = UserProfile.objects.get_or_create(user=owner_user)
            owner_profile.tanga_balance += chapter.price_tanga
            owner_profile.save(update_fields=["tanga_balance"])

    return JsonResponse({
        "success": True,
        "message": f"{chapter.price_tanga} tanga evaziga bob ochildi!"
    })


def can_read(user, manga, chapter) -> bool:
    # 1) bepul bob
    if chapter.price_tanga == 0:
        return True
    # 2) login bo'lmagan foydalanuvchi pullik bobni o'qiy olmaydi
    if not user.is_authenticated:
        return False
    # 3) muallif o'z kontentini o'qiy oladi
    if manga.created_by_id == getattr(user, "id", None) or _is_translator(user) or user.is_superuser or user.is_staff:
        return True
    # 4) aks holda — faqat xarid qilingan bo'lsa
    return ChapterPurchase.objects.filter(user=user, chapter=chapter).exists()
