"""远程清理器的开关、持续运行及异常时数据库连接回收。"""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from ...remote.cleanup import run_cleanup


class CleanupTests(SimpleTestCase):
    @override_settings(UI_REMOTE_ENABLED=False)
    @patch('apps.ui_automation.remote.cleanup.time.sleep')
    @patch('apps.ui_automation.remote.sessions.reap_expired_sessions')
    def test_disabled_watch_exits_without_cleanup_or_wait(self, reap, sleep):
        run_cleanup(watch=True)
        reap.assert_not_called()
        sleep.assert_not_called()

    @override_settings(UI_REMOTE_ENABLED=True)
    @patch('django.db.close_old_connections')
    @patch('apps.ui_automation.remote.cleanup.time.sleep')
    @patch('apps.ui_automation.remote.sessions.reap_expired_sessions')
    def test_single_run_releases_connections_without_wait(self, reap, sleep, close):
        run_cleanup()
        reap.assert_called_once_with()
        close.assert_called_once_with()
        sleep.assert_not_called()

    @override_settings(UI_REMOTE_ENABLED=True)
    @patch('django.db.close_old_connections')
    @patch('apps.ui_automation.remote.cleanup.time.sleep')
    @patch('apps.ui_automation.remote.sessions.reap_expired_sessions',
           side_effect=[None, RuntimeError('cleanup failed')])
    def test_watch_repeats_and_releases_connections_on_failure(self, reap, sleep, close):
        # 异常继续交给 Supervisor 重启，失败的轮次也必须释放数据库连接。
        with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
            run_cleanup(watch=True)
        self.assertEqual(reap.call_count, 2)
        self.assertEqual(close.call_count, 2)
        sleep.assert_called_once_with(60)
