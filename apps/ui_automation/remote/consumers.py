"""使用现有 Channels 将已鉴权 WebSocket 字节流转发到本机私有 VNC。"""
import asyncio
import contextlib
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.core import signing
from django.utils import timezone

from .views import TICKET_SALT
from ..models import UiRemoteSession


class UiDesktopConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.writer = None
        self.pump = None
        try:
            if not settings.UI_REMOTE_ENABLED:
                raise ValueError('远程桌面未启用')
            ticket = parse_qs(self.scope.get('query_string', b'').decode()).get('ticket', [''])[0]
            claims = signing.loads(ticket, salt=TICKET_SALT, max_age=60)
            session_id = str(self.scope['url_route']['kwargs']['session_id'])
            if claims['session'] != session_id:
                raise ValueError('会话不匹配')
            self.session_id = session_id
            self.owner_id = claims['owner']
            port = await self._authorized_port(mark_connected=False)
            if port is None:
                raise ValueError('无权连接或会话已结束')
            self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), 5)
            protocols = self.scope.get('subprotocols', [])
            if await self._authorized_port(mark_connected=True) != port:
                raise ValueError('连接期间会话已结束')
            await self.accept(subprotocol='binary' if 'binary' in protocols else None)
            self.pump = asyncio.create_task(self._forward())
        except Exception:
            if self.writer:
                self.writer.close()
            await self.close(code=4403)

    @database_sync_to_async
    def _authorized_port(self, mark_connected=False):
        # 每秒重新检查会话，停止/到期/权限撤销后已有连接也必须断开。
        session = UiRemoteSession.objects.filter(
            pk=self.session_id, owner_id=self.owner_id, owner__is_active=True,
            node=settings.UI_REMOTE_NODE, status__in=['ready', 'running'],
            stop_requested=False, expires_at__gt=timezone.now(),
        ).first()
        if session and mark_connected:
            UiRemoteSession.objects.filter(pk=session.pk, connected_at__isnull=True).update(connected_at=timezone.now())
        return session.port if session else None

    async def _forward(self):
        try:
            next_check = 0
            while True:
                now = asyncio.get_running_loop().time()
                if now >= next_check:
                    if not await self._authorized_port():
                        break
                    next_check = now + 1
                try:
                    data = await asyncio.wait_for(self.reader.read(65536), 1)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    break
                await self.send(bytes_data=data)
        except (OSError, ConnectionError):
            pass
        finally:
            await self.close()

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data and self.writer:
            self.writer.write(bytes_data)
            await self.writer.drain()

    async def disconnect(self, code):
        if self.pump:
            self.pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.pump
        if self.writer:
            self.writer.close()
            with contextlib.suppress(OSError):
                await self.writer.wait_closed()
