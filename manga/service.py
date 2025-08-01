from django.db import transaction
from django.core.exceptions import ValidationError
from accounts.models import TranslatorFollower, User, UserProfile
from manga.models import ChapterPurchase
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.contrib.auth.decorators import login_required

from accounts.models import UserProfile
from manga.models import Chapter, Manga, ChapterPurchase
from django.http import JsonResponse

from django.db import transaction, IntegrityError

@login_required
def purchase_chapter(request, manga_slug, volume, chapter_number):
    manga = get_object_or_404(Manga, slug=manga_slug)
    chapter = get_object_or_404(Chapter, manga=manga, volume=volume, chapter_number=chapter_number)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # --- Agar allaqachon sotib olingan bo'lsa
    if ChapterPurchase.objects.filter(user=request.user, chapter=chapter).exists():
        return JsonResponse({
            "success": False,
            "message": "Siz bu bobni allaqachon sotib olgansiz."
        })

    # --- Agar foydalanuvchi bobni yaratgan bo'lsa → avtomatik bepul ochish (daromadsiz)
    if chapter.created_by == request.user:
        ChapterPurchase.objects.create(user=request.user, chapter=chapter)
        return JsonResponse({
            "success": True,
            "message": "Bu sizning bobingiz, bepul ochildi!"
        })

    # --- Balans yetarli emas
    if profile.tanga_balance < chapter.price_tanga:
        return JsonResponse({
            "success": False,
            "message": "Tangangiz yetarli emas!"
        })

    # --- Xarid qilish (daromad boshqa foydalanuvchiga yoziladi)
    with transaction.atomic():
        ChapterPurchase.objects.create(user=request.user, chapter=chapter)
        profile.tanga_balance -= chapter.price_tanga
        profile.save()

        # === Daromad yozish faqat boshqa foydalanuvchining bobiga ===
        if chapter.created_by and chapter.created_by != request.user:
            owner_profile, _ = UserProfile.objects.get_or_create(user=chapter.created_by)
            owner_profile.tanga_balance += chapter.price_tanga
            owner_profile.save()

    return JsonResponse({
        "success": True,
        "message": f"{chapter.price_tanga} tanga evaziga bob ochildi!"
    })

