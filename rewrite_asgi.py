import os

from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

from tickets import routing as tickets_routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chamados.settings')

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    'http': ASGIStaticFilesHandler(django_asgi_app),
    'websocket': AuthMiddlewareStack(
        URLRouter(tickets_routing.websocket_urlpatterns)
    ),
})
