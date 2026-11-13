"""断言在单用例和套件的执行路径中保持相同语义。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase
from playwright.async_api import TimeoutError as AsyncTimeout
from playwright.sync_api import TimeoutError as SyncTimeout
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait

from ..playwright_engine import PlaywrightTestEngine
from ..selenium_engine import SeleniumTestEngine
from ..test_executor import TestExecutor
from ..variable_context import UiVariableContext


def assertion_step(assert_type, assert_value=''):
    return {
        'step_number': 1, 'action_type': 'assert', 'description': '检查目标元素',
        'assert_type': assert_type, 'assert_value': assert_value, 'input_value': '',
        'wait_time': 1, 'action_params': {},
        'element': {'name': 'target', 'locator_strategy': 'css', 'locator_value': '.dropdown li'},
    }


class PlaywrightAssertionTests(SimpleTestCase):
    def setUp(self):
        self.engine = PlaywrightTestEngine()
        self.engine.page = MagicMock()
        self.engine.page.screenshot = AsyncMock(return_value=b'screenshot')
        self.locator = self.engine.page.locator.return_value
        self.locator.wait_for = AsyncMock()

    async def test_not_visible_waits_without_filtering_hidden_dropdown_nodes(self):
        step = assertion_step('isNotVisible')
        success, log, _ = await self.engine.execute_step(SimpleNamespace(**step), step['element'])

        self.assertTrue(success, log)
        self.engine.page.locator.assert_called_once_with('.dropdown li')
        self.locator.wait_for.assert_awaited_once_with(state='hidden', timeout=1)

    async def test_still_visible_fails_with_screenshot(self):
        self.locator.wait_for.side_effect = AsyncTimeout('still visible')
        step = assertion_step('isNotVisible')
        success, log, screenshot = await self.engine.execute_step(SimpleNamespace(**step), step['element'])

        self.assertFalse(success)
        self.assertIn('仍然可见', log)
        self.assertTrue(screenshot.startswith('data:image/png;base64,'))

    async def test_has_attribute_preserves_hidden_nodes_and_resolves_variable(self):
        self.engine.variables.set('expected', '/home?id=1')
        self.locator.get_attribute = AsyncMock(return_value='/home?id=1')
        step = assertion_step('hasAttribute', 'href={{expected}}')

        success, log, _ = await self.engine.execute_step(SimpleNamespace(**step), step['element'])

        self.assertTrue(success, log)
        self.engine.page.locator.assert_called_once_with('.dropdown li')
        self.locator.get_attribute.assert_awaited_once_with('href', timeout=1)


class SyncPlaywrightAssertionTests(SimpleTestCase):
    def setUp(self):
        self.executor = TestExecutor.__new__(TestExecutor)
        self.executor.variables = UiVariableContext()
        self.executor.current_page = MagicMock()
        self.locator = self.executor.current_page.locator.return_value

    def test_not_visible_waits_and_reports_visible_failure(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                self.locator.wait_for.side_effect = SyncTimeout('still visible') if timeout else None
                result = self.executor.execute_step_playwright(assertion_step('isNotVisible'))
                self.assertEqual(result['success'], not timeout, result.get('error'))
                if timeout:
                    self.assertIn('仍然可见', result['error'])
        self.locator.wait_for.assert_called_with(state='hidden', timeout=1)

    def test_has_attribute_resolves_expected_value_variable(self):
        self.executor.variables.set('expected', '/home?id=1')
        self.locator.get_attribute.return_value = '/home?id=1'

        result = self.executor.execute_step_playwright(assertion_step('hasAttribute', 'href={{expected}}'))

        self.assertTrue(result['success'], result.get('error'))
        self.locator.get_attribute.assert_called_once_with('href', timeout=1)


class SeleniumAssertionTests(SimpleTestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.driver.get_screenshot_as_png.return_value = b'screenshot'
        self.element = self.driver.find_element.return_value
        self.engine = SeleniumTestEngine()
        self.engine.driver = self.driver
        self.executor = TestExecutor.__new__(TestExecutor)
        self.executor.variables = UiVariableContext()

    def run_assertion(self, path, assert_type, assert_value=''):
        step = assertion_step(assert_type, assert_value)
        # 保留真实等待条件求值，仅取消重试延迟，让可见/隐藏/不存在均经过 Selenium 条件。
        with patch(f'apps.ui_automation.{path}.WebDriverWait',
                   side_effect=lambda driver, _: WebDriverWait(driver, 0)):
            if path == 'selenium_engine':
                success, log, _ = self.engine.execute_step(SimpleNamespace(**step), step['element'])
                return success, log
            result = self.executor.execute_step_selenium(self.driver, step)
            return result['success'], result.get('error') or ''

    def test_not_visible_handles_hidden_missing_and_visible_elements(self):
        for path in ('selenium_engine', 'test_executor'):
            for state in ('hidden', 'missing', 'visible'):
                with self.subTest(path=path, state=state):
                    self.driver.find_element.side_effect = NoSuchElementException() if state == 'missing' else None
                    self.element.is_displayed.return_value = state == 'visible'
                    success, log = self.run_assertion(path, 'isNotVisible')
                    self.assertEqual(success, state != 'visible', log)
                    if state == 'visible':
                        self.assertIn('仍然可见', log)

    def test_has_attribute_checks_dom_presence_empty_values_and_exact_values(self):
        cases = (
            ('data-empty', '', True), ('missing', None, False),
            ('href=/home?id=1', '/home?id=1', True),
            ('data-empty=', '', True), ('href=/wrong', '/home', False),
            ('href=', None, False), ('=value', 'value', False), ('', '', False),
        )
        for path in ('selenium_engine', 'test_executor'):
            for specification, actual, expected in cases:
                with self.subTest(path=path, specification=specification):
                    self.element.get_dom_attribute.reset_mock()
                    self.element.get_dom_attribute.return_value = actual
                    success, log = self.run_assertion(path, 'hasAttribute', specification)
                    self.assertEqual(success, expected, log)
                    name = specification.partition('=')[0]
                    if name:
                        self.element.get_dom_attribute.assert_called_once_with(name)
                    else:
                        self.element.get_dom_attribute.assert_not_called()

    def test_has_attribute_resolves_expected_value_variable(self):
        self.engine.variables.set('expected', '/home')
        self.executor.variables.set('expected', '/home')
        self.element.get_dom_attribute.return_value = '/home'
        for path in ('selenium_engine', 'test_executor'):
            with self.subTest(path=path):
                success, log = self.run_assertion(path, 'hasAttribute', 'href={{expected}}')
                self.assertTrue(success, log)
