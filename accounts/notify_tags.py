# manga/templatetags/notify_tags.py
from django import template
from django.utils import timezone
from manga.models import NewChapterNotification

register = template.Library()

@register.simple_tag
def has_new_chapters(user) -> bool:
    """
    Faqat mavjudmi/yo‘qmi — .exists() bilan yengil tekshiruv.
    """
    if not (user and user.is_authenticated):
        return False
    now = timezone.now()
    return NewChapterNotification.objects.filter(
        user=user, expires_at__gt=now, is_seen=False
    ).exists()
