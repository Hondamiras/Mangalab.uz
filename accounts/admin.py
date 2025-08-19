from django.contrib import admin
from django.contrib.admin import AdminSite
from .models import ReadingStatus, TranslatorFollower, TranslatorTeam, TranslatorTeamMembership, UserProfile
from django.contrib import admin, messages
from django.db.models import Count


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

class TranslatorTeamMembershipInline(admin.TabularInline):
    model = TranslatorTeamMembership
    extra = 1
    autocomplete_fields = ["profile"]
    fields = ("profile", "role", "joined_at")
    readonly_fields = ("joined_at",)
    show_change_link = True

    def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
        if db_field.name == "profile":
            kwargs["queryset"] = UserProfile.objects.filter(
                is_translator=True
            ).select_related("user")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# --- Team admin ---
@admin.register(TranslatorTeam)
class TranslatorTeamAdmin(admin.ModelAdmin):
    list_display = ("name", "admin_member_count", "created_at")
    search_fields = (
        "name",
        "slug",
        "memberships__profile__user__username",
        "memberships__profile__user__email",
    )
    list_filter = ("created_at",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = [TranslatorTeamMembershipInline]
    ordering = ("name",)
    list_per_page = 25

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Tez hisoblash uchun annotate
        return qs.annotate(_member_count=Count("memberships", distinct=True))

    def admin_member_count(self, obj):
        return getattr(obj, "_member_count", obj.members.count())
    admin_member_count.short_description = "A'zolar soni"
    admin_member_count.admin_order_field = "_member_count"


# --- Membership admin (qulay boshqaruv + actions) ---
@admin.register(TranslatorTeamMembership)
class TranslatorTeamMembershipAdmin(admin.ModelAdmin):
    list_display = ("team", "profile_display", "role", "joined_at")
    list_filter = ("team", "role", "joined_at")
    search_fields = (
        "team__name",
        "profile__user__username",
        "profile__user__email",
    )
    autocomplete_fields = ["team", "profile"]
    list_select_related = ("team", "profile", "profile__user")
    ordering = ("team__name", "profile__user__username")
    readonly_fields = ("joined_at",)
    actions = ["make_lead", "make_translator"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("team", "profile", "profile__user")

    # Faqat tarjimonlar chiqishi uchun
    def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
        if db_field.name == "profile":
            kwargs["queryset"] = UserProfile.objects.filter(
                is_translator=True
            ).select_related("user")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def profile_display(self, obj):
        u = obj.profile.user
        return f"{u.username} ({u.email})"
    profile_display.short_description = "Tarjimon"

    # --- Actions ---
    @admin.action(description="Tanlanganlarni yetakchi (lead) qilish")
    def make_lead(self, request, queryset):
        updated = queryset.update(role="lead")
        self.message_user(
            request, f"{updated} ta a'zolik 'lead' ga o'zgartirildi.", level=messages.SUCCESS
        )

    @admin.action(description="Tanlanganlarni oddiy tarjimon (translator) qilish")
    def make_translator(self, request, queryset):
        updated = queryset.update(role="translator")
        self.message_user(
            request, f"{updated} ta a'zolik 'translator' ga o'zgartirildi.", level=messages.SUCCESS
        )
