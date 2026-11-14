"""UI 远程桌面的独立路由，不改变现有执行进度推送。"""
from django.urls import path
from .consumers import UiDesktopConsumer

websocket_urlpatterns = [
    path('ws/ui-automation/remote-sessions/<uuid:session_id>/desktop/', UiDesktopConsumer.as_asgi()),
]
