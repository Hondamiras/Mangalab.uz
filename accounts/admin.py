from django.contrib import admin
from django.contrib.admin import AdminSite
from .models import ReadingStatus, TranslatorFollower, UserProfile

class MangaLabAdminSite(AdminSite):
    site_header = "MangaLab Translator Admin"
    site_title = "MangaLab Translator Portal"
    index_title = "Translator Dashboard"


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'tanga_balance', 'is_translator', 'follower_count')
    list_editable = ('tanga_balance', 'is_translator')
    search_fields = ('user__username',)
    list_filter = ('is_translator',)

    def save_model(self, request, obj, form, change):
        # Agar foydalanuvchi tarjimon bo‘lsa
        if obj.is_translator:
            obj.user.is_staff = True
            obj.user.is_active = True  # <-- MUHIM!
            obj.user.save()
        super().save_model(request, obj, form, change)
    
    def follower_count(self, obj):
        return obj.followers.count()
    follower_count.short_description = "Followers"
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(user=request.user)
    
@admin.register(TranslatorFollower)
class TranslatorFollowerAdmin(admin.ModelAdmin):
    list_display = ("translator", "user", "created_at")
    list_filter = ("translator",)
    search_fields = ("user__user__username", "translator__user__username")
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Translators can only see their own followers
        return qs.filter(translator__user=request.user)

@admin.register(ReadingStatus)
class ReadingStatusAdmin(admin.ModelAdmin):
    list_display = ("user_profile", "manga", "status")
    list_filter = ("status",)
    search_fields = ("user_profile__user__username", "manga__title")
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Translators can see reading status of their followers
        return qs.filter(
            user_profile__in=UserProfile.objects.filter(
                followers__translator__user=request.user
            )
        )

