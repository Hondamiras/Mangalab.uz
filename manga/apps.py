from django.apps import AppConfig


class MangaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'manga'
    verbose_name = "Manga Boshqaruvi"

    def ready(self):
        pass