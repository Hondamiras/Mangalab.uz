from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

from accounts.models import UserProfile
from manga.models import Manga, Chapter, ChapterPurchase

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

    # — agar foydalanuvchi manga egasi bo'lsa → bepul ochilsin
    if manga.created_by == request.user:
        ChapterPurchase.objects.create(user=request.user, chapter=chapter)
        return JsonResponse({
            "success": True,
            "message": "Bu sizning bobingiz, bepul ochildi!"
        })

    # — balans yetarli emas
    if profile.tanga_balance < chapter.price_tanga:
        return JsonResponse({
            "success": False,
            "message": "Tangangiz yetarli emas!"
        })

    # — xarid qilish (daromad manga egasiga yoziladi)
    with transaction.atomic():
        ChapterPurchase.objects.create(user=request.user, chapter=chapter)
        profile.tanga_balance -= chapter.price_tanga
        profile.save()

        # daromadni manga egasiga o'tkazish
        if manga.created_by and manga.created_by != request.user:
            owner_profile, _ = UserProfile.objects.get_or_create(user=manga.created_by)
            owner_profile.tanga_balance += chapter.price_tanga
            owner_profile.save()

    return JsonResponse({
        "success": True,
        "message": f"{chapter.price_tanga} tanga evaziga bob ochildi!"
    })
