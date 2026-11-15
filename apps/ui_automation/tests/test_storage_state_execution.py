"""登录态动作在 Playwright 同步 API 的事件循环中仍须遵守 ORM 线程边界。"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from playwright.sync_api import sync_playwright

from ..models import StorageState, UiProject
from ..test_executor import TestExecutor, _run_case_playwright


class StorageStateExecutionTests(TransactionTestCase):
    def setUp(self):
        # 清除旧套件入口可能遗留的全局开关，让测试真正触发 Django 异步安全检查。
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user(username='storage-execution')
        self.project = UiProject.objects.create(name='登录态项目', owner=self.user)
        self.state = StorageState.objects.create(project=self.project, name='已有登录态')
        self.state.write_content(b'{"cookies": [], "origins": []}')
        self.executor = TestExecutor(project=self.project, executed_by=self.user)
        self.executor._active_project_id = self.project.pk
        self.executor.current_page = MagicMock(url='https://example.test/account')
        self.executor.context = MagicMock()
        self.executor.browser_instance = MagicMock()

    def test_switch_queries_state_with_playwright_event_loop_running(self):
        old_context = self.executor.context
        with sync_playwright():
            self.executor._execute_storage_state_action({
                'operation': 'switch', 'storage_state_id': self.state.pk,
            })

        old_context.close.assert_called_once()
        options = self.executor.browser_instance.new_context.call_args.kwargs
        self.assertEqual(options['storage_state'], self.state.file.path)
        self.executor.current_page.goto.assert_called_once_with(
            'https://example.test/account', wait_until='domcontentloaded', timeout=30000)
        self.assertNotIn('DJANGO_ALLOW_ASYNC_UNSAFE', os.environ)

    def test_save_and_overwrite_with_playwright_event_loop_running(self):
        raw = b'{"cookies": [], "origins": [{"origin": "https://example.test", "localStorage": []}]}'
        self.executor.context.storage_state.side_effect = lambda path: Path(path).write_bytes(raw)
        original_path = self.state.file.name
        for params in (
            {'operation': 'save', 'mode': 'save_as', 'name': '新登录态'},
            {'operation': 'save', 'mode': 'overwrite', 'storage_state_id': self.state.pk},
        ):
            with self.subTest(mode=params['mode']):
                with sync_playwright():
                    saved = self.executor._execute_storage_state_action(params)
                saved.refresh_from_db()
                self.assertEqual(Path(saved.file.path).read_bytes(), raw)
                self.assertEqual(saved.source_url, 'https://example.test/account')
                self.assertEqual(saved.file_size, len(raw))
                if params['mode'] == 'overwrite':
                    self.assertEqual(saved.file.name, original_path)
                else:
                    self.assertEqual(saved.created_by_id, self.user.pk)
        self.assertEqual(StorageState.objects.filter(project=self.project).count(), 2)

    def test_switch_rejects_state_from_another_project_before_closing_context(self):
        other_project = UiProject.objects.create(name='其他项目', owner=self.user)
        self.executor._active_project_id = other_project.pk
        with sync_playwright(), self.assertRaisesRegex(ValueError, '目标登录态不存在或不属于当前项目'):
            self.executor._execute_storage_state_action({
                'operation': 'switch', 'storage_state_id': self.state.pk,
            })
        self.executor.context.close.assert_not_called()

    def test_initial_state_is_loaded_with_playwright_event_loop_running(self):
        self.executor.storage_state_id = self.state.pk
        self.executor.execute_test_case_playwright_no_db = MagicMock(return_value={'status': 'passed'})
        browser = MagicMock()
        with patch('apps.ui_automation.test_executor._launch_playwright_browser', return_value=browser):
            result = _run_case_playwright(self.executor, {'project_id': self.project.pk})
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(browser.new_context.call_args.kwargs['storage_state'], self.state.file.path)
        browser.close.assert_called_once()
