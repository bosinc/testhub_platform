"""远程有头模式的开关、并发隔离、权限和原执行路径回归。"""
import asyncio
from datetime import timedelta
import os
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from ...remote import sessions as remote
from ...remote.consumers import UiDesktopConsumer
from ...remote.views import TICKET_SALT
from ...models import TestCase as UiCase, TestCaseExecution, TestScript, TestSuite, TestSuiteTestCase, UiRemoteSession, UiProject
from ...test_executor import _launch_playwright_browser


class ModeTests(SimpleTestCase):
    def test_switch_and_headless_are_the_only_conditions(self):
        for switch, headless, expected in [(False, False, False), (False, True, False),
                                           (True, True, False), (True, False, True)]:
            with self.subTest(switch=switch, headless=headless), override_settings(UI_REMOTE_ENABLED=switch):
                self.assertEqual(remote.enabled(headless), expected)

    def test_launch_env_is_private_and_optional(self):
        before = os.environ.get('DISPLAY')
        playwright = MagicMock()
        env = {**os.environ, 'DISPLAY': ':112'}
        _launch_playwright_browser(playwright, 'chrome', False, env=env)
        self.assertEqual(playwright.chromium.launch.call_args.kwargs['env']['DISPLAY'], ':112')
        self.assertEqual(os.environ.get('DISPLAY'), before)
        playwright.reset_mock()
        _launch_playwright_browser(playwright, 'chrome', True)
        self.assertNotIn('env', playwright.chromium.launch.call_args.kwargs)


@override_settings(UI_REMOTE_ENABLED=True, UI_REMOTE_MAX_SESSIONS=2)
class SessionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='desktop-owner')
        self.other = User.objects.create_user(username='desktop-other')
        self.project = UiProject.objects.create(name='远程桌面', base_url='https://example.test', owner=self.user)
        self.project.members.add(self.other)
        self.case = UiCase.objects.create(name='登录', project=self.project, created_by=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.tools = patch('apps.ui_automation.remote.sessions.shutil.which', return_value='/usr/bin/tool')
        self.tools.start()
        self.addCleanup(self.tools.stop)

    def execution(self, user=None, headless=False):
        return TestCaseExecution.objects.create(test_case=self.case, project=self.project,
                                               created_by=user or self.user, headless=headless)

    def session(self):
        return remote.create_session(self.execution(), self.user)

    def test_users_receive_distinct_slots_and_limit_is_enforced(self):
        first = self.session()
        second = remote.create_session(self.execution(self.other), self.other)
        self.assertNotEqual(first.display, second.display)
        self.assertNotEqual(first.port, second.port)
        with self.assertRaises(ValidationError):
            self.session()
        UiRemoteSession.objects.filter(pk=first.pk).update(status='finished', slot=None)
        self.assertEqual(self.session().display, first.display)

    def test_no_desktop_lookup_or_components_when_disabled_or_headless(self):
        with patch('apps.ui_automation.remote.sessions.reap_expired_sessions') as reap:
            self.assertIsNone(remote.create_session(self.execution(headless=True), self.user))
            with override_settings(UI_REMOTE_ENABLED=False):
                self.assertIsNone(remote.create_session(self.execution(), self.user))
            reap.assert_not_called()
        self.assertEqual(UiRemoteSession.objects.count(), 0)

    def test_project_member_cannot_read_connect_or_stop_other_users_desktop(self):
        session = self.session()
        UiRemoteSession.objects.filter(pk=session.pk).update(status='ready')
        self.client.force_authenticate(self.other)
        root = f'/api/ui-automation/remote-sessions/{session.pk}/'
        self.assertEqual(self.client.get(root).status_code, 404)
        self.assertEqual(self.client.post(root + 'connect/').status_code, 404)
        self.assertEqual(self.client.post(root + 'stop/').status_code, 404)

    def test_ticket_is_bound_to_owner_and_session_and_hides_internal_address(self):
        session = self.session()
        UiRemoteSession.objects.filter(pk=session.pk).update(status='ready')
        root = f'/api/ui-automation/remote-sessions/{session.pk}/'
        response = self.client.post(root + 'connect/')
        self.assertEqual(response.status_code, 200)
        from urllib.parse import parse_qs, urlparse
        path = parse_qs(urlparse(response.data['url']).query)['path'][0]
        ticket = parse_qs(urlparse(path).query)['ticket'][0]
        self.assertEqual(signing.loads(ticket, salt=TICKET_SALT, max_age=60),
                         {'session': str(session.pk), 'owner': self.user.pk})
        self.assertNotIn('127.0.0.1', response.data['url'])
        self.assertNotIn('port', self.client.get(root).data)
        self.assertEqual(self.client.post(root + 'stop/').status_code, 200)
        session.refresh_from_db()
        self.assertTrue(session.stop_requested)
        self.assertEqual(self.client.post(root + 'connect/').status_code, 409)

    def test_expired_session_cannot_connect_and_is_reaped_with_failed_execution(self):
        session = self.session()
        UiRemoteSession.objects.filter(pk=session.pk).update(status='ready', expires_at=timezone.now() - timedelta(seconds=1))
        root = f'/api/ui-automation/remote-sessions/{session.pk}/'
        self.assertEqual(self.client.post(root + 'connect/').status_code, 409)
        remote.reap_expired_sessions()
        session.refresh_from_db()
        session.case_execution.refresh_from_db()
        self.assertEqual(session.status, 'failed')
        self.assertIsNone(session.slot)
        self.assertEqual(session.case_execution.status, 'failed')

    @patch('apps.ui_automation.test_executor.check_engine_environment', return_value=(True, ''))
    @patch('apps.ui_automation.remote.sessions.launch_case')
    def test_remote_case_returns_async_session_and_original_path_remains_sync(self, launch, check):
        response = self.client.post(f'/api/ui-automation/test-cases/{self.case.pk}/run/',
                                    {'headless': False}, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertIn('remote_session_id', response.data)
        launch.assert_called_once()
        for switch, headless in [(False, False), (True, True), (False, True)]:
            with self.subTest(switch=switch, headless=headless), override_settings(UI_REMOTE_ENABLED=switch):
                with patch('apps.ui_automation.test_executor.run_single_case', return_value={'steps': []}) as run:
                    response = self.client.post(f'/api/ui-automation/test-cases/{self.case.pk}/run/',
                                                {'headless': headless}, format='json')
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn('remote_session_id', response.data)
                    run.assert_called_once()
        self.assertEqual(UiRemoteSession.objects.count(), 1)

    @patch('apps.ui_automation.test_executor.check_engine_environment', return_value=(True, ''))
    def test_allocation_failure_marks_case_execution_failed(self, check):
        with patch('apps.ui_automation.remote.sessions.shutil.which', return_value=None):
            response = self.client.post(f'/api/ui-automation/test-cases/{self.case.pk}/run/', {'headless': False}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TestCaseExecution.objects.latest('pk').status, 'failed')

    @patch('apps.ui_automation.views.start_script_execution')
    def test_saved_and_unsaved_replays_both_bind_private_session(self, start):
        script = TestScript.objects.create(project=self.project, name='录制', content='print("ok")',
                                           language='python', framework='playwright')
        urls = [(f'/api/ui-automation/test-scripts/{script.pk}/run/', {}),
                ('/api/ui-automation/test-scripts/run-unsaved/', {'project': self.project.pk, 'content': 'print("ok")'})]
        for url, data in urls:
            response = self.client.post(url, {**data, 'headless': False}, format='json')
            self.assertEqual(response.status_code, 202, response.data)
            self.assertEqual(str(start.call_args.kwargs['remote_session'].pk), response.data['remote_session_id'])

    @patch('threading.Thread')
    @patch('apps.ui_automation.playwright_engine.PlaywrightTestEngine.check_execution_environment_sync', return_value=(True, ''))
    def test_suite_precreates_execution_and_session_without_starting_python_process(self, check, thread):
        suite = TestSuite.objects.create(project=self.project, name='套件')
        TestSuiteTestCase.objects.create(test_suite=suite, test_case=self.case)
        response = self.client.post(f'/api/ui-automation/test-suites/{suite.pk}/run_suite/',
                                    {'headless': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        session = UiRemoteSession.objects.get(pk=response.data['remote_session_id'])
        self.assertEqual(session.execution_id, response.data['execution_id'])
        thread.return_value.start.assert_called_once()

    @override_settings(UI_REMOTE_CONNECT_WAIT=0)
    def test_two_live_desktops_keep_environment_private_and_release_their_own_resources(self):
        first, second = self.session(), self.session()
        before = dict(os.environ)
        processes = []

        def spawn(args, **kwargs):
            process = MagicMock()
            process.pid = 10000 + len(processes)
            process.poll.return_value = None
            processes.append((process, args, kwargs))
            return process

        with patch('apps.ui_automation.remote.sessions.subprocess.Popen', side_effect=spawn), \
             patch('apps.ui_automation.remote.sessions.subprocess.run', return_value=MagicMock(returncode=0)), \
             patch('apps.ui_automation.remote.sessions.socket.create_connection', side_effect=[OSError(), MagicMock(), OSError(), MagicMock()]), \
             patch('apps.ui_automation.remote.sessions.threading.Thread'), \
             patch('apps.ui_automation.remote.sessions._terminate') as terminate:
            with remote.Desktop(first) as a, remote.Desktop(second) as b:
                self.assertNotEqual(a.env['DISPLAY'], b.env['DISPLAY'])
                self.assertNotEqual(a.env['XAUTHORITY'], b.env['XAUTHORITY'])
                self.assertNotEqual(a.env['HOME'], b.env['HOME'])
                self.assertEqual(dict(os.environ), before)
                self.assertEqual(UiRemoteSession.objects.filter(status='running').count(), 2)
                self.assertEqual(processes[0][2]['env']['DISPLAY'], first.display)
                self.assertEqual(processes[3][2]['env']['DISPLAY'], second.display)
            self.assertEqual(terminate.call_count, 6)
        self.assertFalse(UiRemoteSession.objects.filter(slot__isnull=False).exists())

    def test_stopped_session_never_starts_display_processes(self):
        session = self.session()
        UiRemoteSession.objects.filter(pk=session.pk).update(stop_requested=True)
        with patch('apps.ui_automation.remote.sessions.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, '已停止或超时'):
                with remote.Desktop(session):
                    self.fail('已停止的会话不能继续运行')
            spawn.assert_not_called()
        session.refresh_from_db()
        self.assertEqual(session.status, 'failed')
        self.assertIsNone(session.slot)

    def test_monitor_terminates_only_its_desktop_on_stop_or_expiry(self):
        for stopped, expired in [(True, False), (False, True)]:
            with self.subTest(stopped=stopped, expired=expired):
                session = self.session()
                UiRemoteSession.objects.filter(pk=session.pk).update(
                    stop_requested=stopped,
                    expires_at=timezone.now() + timedelta(seconds=-1 if expired else 90),
                )
                desktop = remote.Desktop(session)
                desktop.closed = MagicMock()
                desktop.closed.wait.return_value = False
                with patch('apps.ui_automation.remote.sessions.close_old_connections'), \
                     patch.object(desktop, '_stop_processes') as stop:
                    desktop._monitor()
                self.assertTrue(desktop.cancelled.is_set())
                stop.assert_called_once()
                UiRemoteSession.objects.filter(pk=session.pk).update(status='finished', slot=None)

    def test_desktop_startup_failure_releases_slot_and_marks_execution(self):
        session = self.session()
        with patch('apps.ui_automation.remote.sessions.subprocess.run', side_effect=RuntimeError('xauth failed')):
            with self.assertRaisesRegex(RuntimeError, 'xauth failed'):
                with remote.Desktop(session):
                    self.fail('不应进入执行阶段')
        session.refresh_from_db()
        self.assertIsNone(session.slot)
        self.assertEqual(session.status, 'failed')
        self.assertFalse(session.resources)


class ResourceCleanupTests(SimpleTestCase):
    def test_cleanup_signals_only_processes_with_exact_session_marker(self):
        owned, other = MagicMock(), MagicMock()
        owned.name, other.name = '9101', '9102'
        (owned / 'environ').read_bytes.return_value = b'PATH=/bin\0REMOTE_SESSION=private-id\0'
        (other / 'environ').read_bytes.return_value = b'PATH=/bin\0REMOTE_SESSION=other-id\0'
        owned.exists.return_value = False
        with patch('apps.ui_automation.remote.sessions.Path.glob', return_value=[owned, other]), \
             patch('apps.ui_automation.remote.sessions.os.kill') as kill:
            remote._stop_session_children('private-id')
        import signal
        kill.assert_called_once_with(9101, signal.SIGTERM)


class SocketAuthTests(SimpleTestCase):
    def test_bad_or_wrong_session_ticket_never_connects_to_vnc(self):
        async def scenario(ticket):
            consumer = UiDesktopConsumer()
            consumer.scope = {'query_string': f'ticket={ticket}'.encode(),
                              'url_route': {'kwargs': {'session_id': 'different-session'}}}
            consumer.close = AsyncMock()
            with patch('asyncio.open_connection', new_callable=AsyncMock) as connect:
                await consumer.connect()
                connect.assert_not_called()
            consumer.close.assert_awaited_once_with(code=4403)
        asyncio.run(scenario('invalid'))
        ticket = signing.dumps({'session': 'other-session', 'owner': 1}, salt=TICKET_SALT)
        with override_settings(UI_REMOTE_ENABLED=True):
            asyncio.run(scenario(ticket))

    @override_settings(UI_REMOTE_ENABLED=True)
    def test_authenticated_socket_forwards_binary_data_and_rechecks_before_accepting(self):

        async def scenario():
            consumer = UiDesktopConsumer()
            session_id = '88194ff9-3959-450b-a6c1-071874b013c2'
            ticket = signing.dumps({'session': session_id, 'owner': 1}, salt=TICKET_SALT)
            consumer.scope = {'query_string': f'ticket={ticket}'.encode(),
                              'url_route': {'kwargs': {'session_id': session_id}}, 'subprotocols': ['binary']}
            consumer._authorized_port = AsyncMock(return_value=5911)
            consumer.accept = AsyncMock()
            consumer.close = AsyncMock()
            consumer.send = AsyncMock()
            reader = AsyncMock()
            reader.read.side_effect = [b'RFB 003.008\n', b'']
            writer = MagicMock()
            writer.drain = AsyncMock()
            writer.wait_closed = AsyncMock()
            with patch('asyncio.open_connection', new_callable=AsyncMock, return_value=(reader, writer)):
                await consumer.connect()
                await consumer.receive(bytes_data=b'client input')
                await consumer.pump
            consumer.accept.assert_awaited_once_with(subprotocol='binary')
            consumer.send.assert_awaited_once_with(bytes_data=b'RFB 003.008\n')
            writer.write.assert_called_once_with(b'client input')
            await consumer.disconnect(1000)
            writer.close.assert_called_once()

            # 在建立 TCP 连接期间会话被结束时，不接受 WebSocket、也不发送 VNC 数据。
            consumer._authorized_port = AsyncMock(side_effect=[5911, None])
            consumer.accept.reset_mock()
            consumer.close.reset_mock()
            with patch('asyncio.open_connection', new_callable=AsyncMock, return_value=(reader, writer)):
                await consumer.connect()
            consumer.accept.assert_not_awaited()
            consumer.close.assert_awaited_once_with(code=4403)
        asyncio.run(scenario())
