from django.conf import settings
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.utils.text import slugify

User = get_user_model()

READING_STATUSES = (
    ("reading", "O'qilyapti"),
    ("planned", "O'qiyman"),
    ("completed", "O'qilgan"),
    ("favorite", "Yoqtirganim"),
)

class EmailVerificationCode(models.Model):
    user    = models.OneToOneField(User, on_delete=models.CASCADE)
    code    = models.CharField(max_length=6)
    created = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        return timezone.now() > self.created + timedelta(minutes=15)

    def __str__(self):
        return f"{self.user.email} → {self.code}"

    
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True, verbose_name="Rasm")
    link = models.URLField(verbose_name="Telegram havolasi")
    tanga_balance = models.PositiveIntegerField(default=0, verbose_name="Tangalar balansi")
    is_translator = models.BooleanField(default=False, verbose_name="Tarjimonmi?")
    description = models.TextField(
        null=True,
        blank=True,
        verbose_name="Tarjimon tavsifi"
    )

    reading_list = models.ManyToManyField(
        'manga.Manga',
        through='ReadingStatus',
        blank=True
    )

    class Meta:
        verbose_name = "Profil"
        verbose_name_plural = "Profillar"

    def __str__(self):
        return self.user.username

    def add_to_reading_list(self, manga, status: str = "planned"):
        ReadingStatus.objects.update_or_create(
            user_profile=self,
            manga=manga,
            defaults={"status": status}
        )
    
    def follower_count(self, obj):
        return obj.followers.count()
    follower_count.short_description = "Followers"

    @property
    def team_names(self) -> str:
        return ", ".join(self.teams.values_list("name", flat=True))

    def is_in_team(self, team_slug: str) -> bool:
        return self.teams.filter(slug=team_slug).exists()

from django import forms
class TranslatorSelfEditForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ('avatar', 'description')


class TranslatorTeam(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="Jamoa nomi")
    description = models.TextField(blank=True, verbose_name="Ta'rif")
    profile_image = models.ImageField(upload_to="team_images/", verbose_name="Jamoa rasmi")
    created_at = models.DateTimeField(auto_now_add=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)

    # A'zolar: UserProfile orqali
    members = models.ManyToManyField(
        UserProfile,
        through='TranslatorTeamMembership',
        related_name='teams',
        verbose_name="A'zolar",
        limit_choices_to={'is_translator': True},  # 👈 faqat tarjimonlar ko‘rinsin
    )

    class Meta:
        verbose_name = "Tarjimonlar jamoasi"
        verbose_name_plural = "Tarjimonlar jamoalari"
        ordering = ("name",)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug = base
            i = 1
            while TranslatorTeam.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                i += 1
                slug = f"{base}-{i}"
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def member_count(self):
        return self.members.count()


ROLE_CHOICES = (
    ("lead", "Jamoa yetakchisi"),
    ("translator", "Tarjimon"),
)

class TranslatorTeamMembership(models.Model):
    team = models.ForeignKey(
        TranslatorTeam,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='team_memberships',
        limit_choices_to={'is_translator': True},  # 👈 faqat tarjimonlar ko‘rinsin
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="translator")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("team", "profile")
        verbose_name = "Jamoa a'zoligi"
        verbose_name_plural = "Jamoa a'zoliklari"

    def __str__(self):
        return f"{self.team.name} → {self.profile.user.username} ({self.role})"

    def clean(self):
        # Faqat tarjimon profilga a'zo bo'lishi mumkin
        if not self.profile.is_translator:
            raise ValidationError("Faqat 'is_translator=True' bo‘lgan UserProfile jamoaga qo‘shilishi mumkin.")

class TranslatorFollower(models.Model):
    translator = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='followers')
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='following')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('translator', 'user')

    def __str__(self):
        return f"{self.translator.user.username} → {self.user.user.username}"

class ReadingStatus(models.Model):
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='reading_statuses'
    )
    manga = models.ForeignKey(
        'manga.Manga',
        on_delete=models.CASCADE
    )
    status = models.CharField(
        max_length=15,
        choices=READING_STATUSES,
        default="planned"
    )

    class Meta:
        unique_together = ("user_profile", "manga")
        verbose_name = "O'qish holati"
        verbose_name_plural = "O'qish holatlari"

    def __str__(self):
        return f"{self.user_profile.user.username} — {self.manga.title} ({self.status})"


class PendingSignup(models.Model):
    username      = models.CharField(max_length=150, unique=True)
    email         = models.EmailField(unique=True)
    password_hash = models.CharField(max_length=128)
    code          = models.CharField(max_length=6)
    created       = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        return timezone.now() > self.created + timedelta(minutes=15)

    def save_password(self, raw_password):
        self.password_hash = make_password(raw_password)
