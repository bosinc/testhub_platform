from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..test_executor import TestExecutor
from ..variable_context import UiVariableContext


class TestSuiteExecutorFixes(SimpleTestCase):
    def test_navigation_falls_back_after_dom_content_loaded(self):
        page = Mock()
        page.wait_for_load_state.side_effect = PlaywrightTimeoutError('network stays active')

        idle = TestExecutor._goto_with_load_fallback(page, 'https://example.test/', timeout=30000)

        self.assertFalse(idle)
        page.goto.assert_called_once_with(
            'https://example.test/', wait_until='domcontentloaded', timeout=30000
        )
        page.wait_for_load_state.assert_called_once_with('networkidle', timeout=5000)


    def test_url_assertion_waits_for_redirect(self):
        executor = object.__new__(TestExecutor)
        page = Mock()
        page.url = 'https://example.test/login'

        def wait_for_timeout(_):
            page.url = 'https://example.test/autotestshop'

        page.wait_for_timeout.side_effect = wait_for_timeout
        executor.current_page = page
        executor.variables = UiVariableContext()
        step = {
            'step_number': 1,
            'action_type': 'assert',
            'description': '等待登录跳转',
            'assert_type': 'urlContains',
            'assert_value': 'autotestshop',
            'wait_time': 1000,
            'input_value': '',
            'element': None,
            'action_params': {},
        }

        result = executor._execute_step_playwright(step)

        self.assertTrue(result['success'], result.get('error'))
        page.wait_for_timeout.assert_called_once_with(100)

    def test_click_skips_a_disabled_save_button_when_requested(self):
        executor = object.__new__(TestExecutor)
        executor.variables = UiVariableContext()
        executor.current_page = Mock()
        locator = Mock()
        locator.count.return_value = 1
        locator.is_enabled.return_value = False
        step = {
            'step_number': 1,
            'action_type': 'click',
            'description': 'Save restored setting',
            'element': {
                'name': 'save_button',
                'locator_strategy': 'role',
                'locator_value': 'button',
                'locator_options': {'name': 'Save'},
            },
            'action_params': {'skip_if_disabled': True},
            'wait_time': 1000,
            'input_value': '',
            'assert_value': '',
            'assert_type': '',
        }

        with patch('apps.ui_automation.test_executor.build_playwright_locator', return_value=locator):
            result = executor._execute_step_playwright(step)

        self.assertTrue(result['success'])
        self.assertTrue(result['skipped'])
        locator.click.assert_not_called()

    def test_click_forces_svg_hit_target_when_requested(self):
        executor = object.__new__(TestExecutor)
        executor.variables = UiVariableContext()
        executor.current_page = Mock()
        locator = Mock()
        locator.count.return_value = 1
        locator.is_visible.return_value = True
        locator.is_enabled.return_value = True
        step = {
            'step_number': 1,
            'action_type': 'click',
            'description': 'Select SVG seat through transparent hit target',
            'element': {
                'name': 'seat',
                'locator_strategy': 'css',
                'locator_value': 'circle[data-id]',
                'locator_options': {'first': True},
            },
            'action_params': {'force': True},
            'wait_time': 1000,
            'input_value': '',
            'assert_value': '',
            'assert_type': '',
        }

        with patch('apps.ui_automation.test_executor.build_playwright_locator', return_value=locator):
            result = executor._execute_step_playwright(step)

        self.assertTrue(result['success'])
        locator.click.assert_called_once_with(timeout=1000, force=True)

    def test_optional_click_skips_when_target_is_missing(self):
        executor = object.__new__(TestExecutor)
        executor.variables = UiVariableContext()
        executor.current_page = Mock()
        locator = Mock()
        locator.count.return_value = 0
        step = {
            'step_number': 1,
            'action_type': 'click',
            'description': 'Dismiss optional notice',
            'element': {
                'name': 'optional_notice',
                'locator_strategy': 'role',
                'locator_value': 'button',
                'locator_options': {'name': 'Got it'},
            },
            'action_params': {'optional': True},
            'wait_time': 1000,
            'input_value': '',
            'assert_value': '',
            'assert_type': '',
        }

        with patch('apps.ui_automation.test_executor.build_playwright_locator', return_value=locator):
            result = executor._execute_step_playwright(step)

        self.assertTrue(result['success'])
        self.assertTrue(result['skipped'])
        locator.click.assert_not_called()

    def test_optional_wait_skips_after_timeout(self):
        executor = object.__new__(TestExecutor)
        executor.variables = UiVariableContext()
        executor.current_page = Mock()
        locator = Mock()
        locator.wait_for.side_effect = PlaywrightTimeoutError('not present')
        step = {
            'step_number': 1,
            'action_type': 'waitFor',
            'description': 'Wait for optional confirmation',
            'element': {
                'name': 'optional_confirmation',
                'locator_strategy': 'text',
                'locator_value': 'Post settings updated',
                'locator_options': {},
            },
            'action_params': {'optional': True},
            'wait_time': 1000,
            'input_value': '',
            'assert_value': '',
            'assert_type': '',
        }

        with patch('apps.ui_automation.test_executor.build_playwright_locator', return_value=locator):
            result = executor._execute_step_playwright(step)

        self.assertTrue(result['success'])
        self.assertTrue(result['skipped'])


    # ---- clearCart 清空购物车动作 ----

    def _make_clear_cart_executor(self):
        executor = object.__new__(TestExecutor)
        executor.variables = UiVariableContext()
        executor.context = Mock()
        return executor

    def _clear_cart_step(self, params):
        return {
            'step_number': 1,
            'action_type': 'clearCart',
            'description': '清空购物车',
            'element': None,
            'action_params': params,
            'wait_time': 1000,
            'input_value': '',
            'assert_value': '',
            'assert_type': '',
        }

    def test_clear_cart_bearer_mode_refreshes_token_then_deletes(self):
        executor = self._make_clear_cart_executor()
        api = executor.context.request

        refresh_resp = Mock()
        refresh_resp.status = 200
        refresh_resp.json.return_value = {'code': 200, 'data': 'jwt-token-xyz'}
        delete_resp = Mock()
        delete_resp.status = 200

        def fetch(url, **kwargs):
            self.assertTrue(url.endswith('/cart'))
            self.assertEqual(kwargs['method'], 'DELETE')
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer jwt-token-xyz')
            return delete_resp

        api.get.return_value = refresh_resp
        api.fetch.side_effect = fetch

        step = self._clear_cart_step({
            'api_base': 'https://api.example.test',
            'auth_mode': 'bearer',
        })
        result = executor._execute_step_playwright(step)

        self.assertTrue(result['success'], result.get('error'))
        api.get.assert_called_once()
        self.assertIn('/auth/refreshToken', api.get.call_args.args[0])
        api.fetch.assert_called_once()

    def test_clear_cart_none_mode_skips_token_refresh(self):
        executor = self._make_clear_cart_executor()
        api = executor.context.request
        delete_resp = Mock()
        delete_resp.status = 204
        api.fetch.return_value = delete_resp

        step = self._clear_cart_step({
            'api_base': 'https://release.pear.us',
            'cart_path': '/api/cart',
            'auth_mode': 'none',
        })
        result = executor._execute_step_playwright(step)

        self.assertTrue(result['success'], result.get('error'))
        api.get.assert_not_called()
        args, kwargs = api.fetch.call_args
        self.assertEqual(args[0], 'https://release.pear.us/api/cart')
        self.assertEqual(kwargs['method'], 'DELETE')

    def test_clear_cart_put_with_body(self):
        executor = self._make_clear_cart_executor()
        api = executor.context.request
        resp = Mock()
        resp.status = 200
        api.fetch.return_value = resp

        step = self._clear_cart_step({
            'api_base': 'https://api.example.test',
            'method': 'PUT',
            'auth_mode': 'none',
            'body': '{"items": []}',
        })
        result = executor._execute_step_playwright(step)

        self.assertTrue(result['success'], result.get('error'))
        _, kwargs = api.fetch.call_args
        self.assertEqual(kwargs['method'], 'PUT')
        self.assertEqual(kwargs['data'], '{"items": []}')

    def test_clear_cart_fails_on_refresh_error(self):
        executor = self._make_clear_cart_executor()
        refresh_resp = Mock()
        refresh_resp.status = 401
        executor.context.request.get.return_value = refresh_resp

        step = self._clear_cart_step({'api_base': 'https://api.example.test'})
        result = executor._execute_step_playwright(step)

        self.assertFalse(result['success'])
        self.assertIn('401', result['error'])
        executor.context.request.fetch.assert_not_called()

    def test_clear_cart_requires_api_base(self):
        executor = self._make_clear_cart_executor()
        step = self._clear_cart_step({})
        result = executor._execute_step_playwright(step)
        self.assertFalse(result['success'])
        self.assertIn('API', result['error'])
