"""私有桌面 HTTP 接口；项目内其他成员也不能连接他人的桌面。"""
from django.conf import settings
from django.core import signing
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, viewsets

from ..models import UiRemoteSession
from .sessions import ACTIVE, execution_payload

TICKET_SALT = 'ui-remote-desktop'


class RemoteSessionViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    lookup_value_regex = '[0-9a-f-]{36}'

    def get_queryset(self):
        return UiRemoteSession.objects.filter(owner=self.request.user).select_related('case_execution', 'execution')

    def retrieve(self, request, pk=None):
        session = self.get_object()
        return Response({
            'id': str(session.pk), 'status': session.status, 'error': session.error,
            'expires_at': session.expires_at, 'result': execution_payload(session),
            'execution_status': (session.case_execution.status if session.case_execution_id
                                 else session.execution.status),
        })

    @action(detail=True, methods=['post'])
    def connect(self, request, pk=None):
        session = self.get_object()
        if not settings.UI_REMOTE_ENABLED or session.node != settings.UI_REMOTE_NODE:
            return Response({'error': '当前节点未启用此远程桌面'}, status=status.HTTP_409_CONFLICT)
        if session.status not in ('ready', 'running') or session.stop_requested or session.expires_at <= timezone.now():
            return Response({'error': '桌面尚未就绪或已关闭'}, status=status.HTTP_409_CONFLICT)
        # 短时凭据仅通过已鉴权 API 签发，绑定单个用户和会话。
        ticket = signing.dumps({'session': str(session.pk), 'owner': session.owner_id}, salt=TICKET_SALT)
        path = f'ws/ui-automation/remote-sessions/{session.pk}/desktop/?ticket={ticket}'
        from urllib.parse import urlencode
        response = Response({'url': '/novnc/vnc.html?' + urlencode({
            'autoconnect': 'true', 'resize': 'scale', 'path': path, 'reconnect': 'false',
        })})
        response['Cache-Control'] = 'no-store'
        return response

    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        session = self.get_object()
        if session.status in ACTIVE:
            UiRemoteSession.objects.filter(pk=session.pk).update(stop_requested=True)
        return Response({'message': '已请求停止本次调试'})
