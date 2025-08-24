# views/purchases.py (yoki tegishli faylingiz)

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

from accounts.models import UserProfile
from manga.models import Manga, Chapter, ChapterPurchase


def _is_translator(user) -> bool:
    """Foydalanuvchi tarjimonmi? (UserProfile.is_translator)"""
    try:
        return getattr(user.userprofile, "is_translator", False)
    except UserProfile.DoesNotExist:
        return False


@login_required
def purchase_chapter(request, manga_slug, volume, chapter_number):
    manga = get_object_or_404(Manga, slug=manga_slug)
    chapter = get_object_or_404(
        Chapter, manga=manga, volume=volume, chapter_number=chapter_number
    )
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    owner_user = manga.created_by

    # --- IMTIYOZLI GURUHLAR: superuser/staff/muallif/tarjimon yoki bob bepul
    if (
        chapter.price_tanga == 0
        or request.user.is_superuser
        or request.user.is_staff
        or (owner_user and owner_user == request.user)
        or _is_translator(request.user)
    ):
        # Hech qanday tanga harakati YO'Q, xarid yozuvi ham YO'Q
        return JsonResponse({"success": True, "message": "Siz uchun bepul o‘qish mumkin."})

    # --- ALLAQACHON SOTIB OLGAN: Hech narsa yozmaymiz
    if ChapterPurchase.objects.filter(user=request.user, chapter=chapter).exists():
        return JsonResponse({"success": True, "message": "Bu bob allaqachon ochilgan."})

    price = int(chapter.price_tanga or 0)
    if price <= 0:
        return JsonResponse({"success": True, "message": "Siz uchun bepul o‘qish mumkin."})

    with transaction.atomic():
        # Xaridor profilini qulflab olamiz (double-click’lardan himoya)
        buyer_profile = UserProfile.objects.select_for_update().get(pk=profile.pk)

        # QULF ostida qayta tekshirish (poygada ikkinchi so‘rov bo‘lsa)
        if ChapterPurchase.objects.filter(user=request.user, chapter=chapter).exists():
            return JsonResponse({"success": True, "message": "Bu bob allaqachon ochilgan."})

        if buyer_profile.tanga_balance < price:
            return JsonResponse({"success": False, "message": "Tangangiz yetarli emas!"})

        # 1) Xaridordan yechamiz
        buyer_profile.tanga_balance -= price
        buyer_profile.save(update_fields=["tanga_balance"])

        # 2) Egaga yozamiz (o‘z-o‘ziga emas)
        if owner_user and owner_user != request.user:
            owner_profile, _ = UserProfile.objects.select_for_update().get_or_create(user=owner_user)
            owner_profile.tanga_balance += price
            owner_profile.save(update_fields=["tanga_balance"])

        # 3) Endi xarid yozuvini yaratamiz (xarid haqiqatan to‘landi)
        ChapterPurchase.objects.create(user=request.user, chapter=chapter)

    return JsonResponse({"success": True, "message": f"{price} tanga evaziga bob ochildi!"})

def can_read(user, manga, chapter) -> bool:
    """
    O‘qish siyosati:
      - Bob bepul bo‘lsa -> True
      - Guest -> False (pullik bob)
      - Superuser/staff, muallif, istalgan tarjimon -> True
      - Aks holda — xarid qilingan bo‘lsa True
    """
    if chapter.price_tanga == 0:
        return True
    if not user.is_authenticated:
        return False
    if (
        user.is_superuser
        or user.is_staff
        or manga.created_by_id == getattr(user, "id", None)
        or _is_translator(user)
    ):
        return True
    return ChapterPurchase.objects.filter(user=user, chapter=chapter).exists()
