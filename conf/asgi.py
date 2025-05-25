# """
# ASGI entry-point for Django + Channels.
# """
# import os
# from django.core.asgi import get_asgi_application
# from channels.routing import ProtocolTypeRouter, URLRouter
# from channels.auth import AuthMiddlewareStack
# import forum.routing            # <-- импортируем паттерны форума

# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "conf.settings")

# django_app = get_asgi_application()

# application = ProtocolTypeRouter(
#     {
#         "http": django_app,
#         "websocket": AuthMiddlewareStack(URLRouter(forum.routing.websocket_urlpatterns)),
#     }
# )
