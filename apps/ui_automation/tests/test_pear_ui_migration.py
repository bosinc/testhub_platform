from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.ui_automation.playwright_engine import PlaywrightTestEngine, build_playwright_locator
from apps.ui_automation.models import Element, LocatorStrategy, TestCase as UiTestCase, UiProject
from apps.ui_automation.serializers import ElementSerializer, TestCaseStepSerializer
from apps.ui_automation.test_executor import TestExecutor
from apps.ui_automation.variable_context import UiVariableContext
from django.contrib.auth import get_user_model


class PearMigrationAsyncEngineTests(SimpleTestCase):
    def setUp(self):
        self.engine = PlaywrightTestEngine()
        self.engine.page = MagicMock()
        self.engine.page.screenshot = AsyncMock(return_value=b'screenshot')
        self.engine.variables = UiVariableContext()
        self.locator = MagicMock()
        self.engine.page.locator.return_value = self.locator
        self.locator.press = AsyncMock()
        self.locator.select_option = AsyncMock()
        self.locator.check = AsyncMock()
        self.locator.uncheck = AsyncMock()
        self.locator.get_attribute = AsyncMock()
        self.locator.count = AsyncMock(return_value=0)
        self.locator.is_visible = AsyncMock(return_value=False)
        self.locator.click = AsyncMock()
        self.locator.evaluate = AsyncMock()

    async def run_step(self, action_type, *, input_value='', assert_type='', assert_value='', element=None,
                       action_params=None):
        step = SimpleNamespace(
            action_type=action_type,
            input_value=input_value,
            assert_value=assert_value,
            assert_type=assert_type,
            action_params=action_params or {},
            wait_time=1000,
        )
        element = element or {
            'name': 'target', 'locator_strategy': 'css', 'locator_value': '#target',
            'locator_options': {},
        }
        return await self.engine.execute_step(step, element)

    async def test_press_select_check_and_uncheck_are_dispatched(self):
        operations = (
            ('press', 'Enter', self.locator.press, ('Enter',)),
            ('select', 'active', self.locator.select_option, ()),
            ('check', '', self.locator.check, ()),
            ('uncheck', '', self.locator.uncheck, ()),
        )

        for action, value, operation, positional_args in operations:
            with self.subTest(action=action):
                self.locator.reset_mock()
                for method in ('press', 'select_option', 'check', 'uncheck'):
                    getattr(self.locator, method).reset_mock()
                success, log, _ = await self.run_step(action, input_value=value)

                self.assertTrue(success, log)
                self.assertIn('成功', log)
                operation.assert_awaited_once()
                if positional_args:
                    operation.assert_awaited_once_with(*positional_args, timeout=1000)
                elif action == 'select':
                    operation.assert_awaited_once_with(value='active', timeout=1000)
                else:
                    operation.assert_awaited_once_with(timeout=1000, force=False)

    async def test_optional_click_skips_missing_target(self):
        success, log, _ = await self.run_step('click', action_params={'optional': True})

        self.assertTrue(success, log)
        self.assertIn('已跳过', log)
        self.locator.click.assert_not_awaited()

    async def test_optional_click_uses_target_when_visible(self):
        self.locator.count.return_value = 1
        self.locator.is_visible.return_value = True

        success, log, _ = await self.run_step('click', action_params={'optional': True})

        self.assertTrue(success, log)
        self.locator.click.assert_awaited_once_with(timeout=1000, force=False)

    async def test_dispatch_click_sends_mouse_event_sequence(self):
        success, log, _ = await self.run_step('click', action_params={'dispatch_click': True})

        self.assertTrue(success, log)
        self.assertIn('分发鼠标事件', log)
        self.locator.evaluate.assert_awaited_once()
        self.locator.click.assert_not_awaited()

    async def test_url_contains_waits_for_navigation(self):
        self.engine.page.url = 'https://example.test/checkout'

        async def complete_navigation(_delay):
            self.engine.page.url = 'https://example.test/order/123'

        with patch('apps.ui_automation.playwright_engine.asyncio.sleep',
                   new_callable=AsyncMock, side_effect=complete_navigation):
            success, log, _ = await self.run_step(
                'assert', assert_type='urlContains', assert_value='order'
            )

        self.assertTrue(success, log)
        self.assertIn('URL包含', log)

    async def test_has_attribute_checks_presence_exact_value_and_missing_attributes(self):
        cases = (
            ('href', 'https://example.com', True),
            ('disabled', '', True),
            ('href=https://example.com', 'https://example.com', True),
            ('href=https://wrong.example', 'https://example.com', False),
            ('missing', None, False),
        )

        for specification, actual_value, expected in cases:
            with self.subTest(specification=specification):
                self.locator.get_attribute.reset_mock()
                self.locator.get_attribute.return_value = actual_value
                success, log, _ = await self.run_step(
                    'assert', assert_type='hasAttribute', assert_value=specification
                )

                self.assertEqual(success, expected, log)
                self.locator.get_attribute.assert_awaited_once_with(
                    specification.partition('=')[0], timeout=1000
                )

    async def test_has_attribute_rejects_empty_attribute_name(self):
        success, log, _ = await self.run_step(
            'assert', assert_type='hasAttribute', assert_value='=value'
        )

        self.assertFalse(success)
        self.assertIn('需要指定属性名', log)
        self.locator.get_attribute.assert_not_awaited()


class PearMigrationSyncExecutorTests(SimpleTestCase):
    def setUp(self):
        self.executor = TestExecutor.__new__(TestExecutor)
        self.executor.variables = UiVariableContext()
        self.executor.current_page = MagicMock()
        self.locator = self.executor.current_page.locator.return_value

    def run_step(self, action_type, locator_value, *, assert_value='', assert_type=''):
        return self.executor.execute_step_playwright({
            'step_number': 1,
            'action_type': action_type,
            'description': '',
            'element': {
                'name': 'target', 'locator_strategy': 'css', 'locator_value': locator_value,
                'locator_options': {},
            },
            'input_value': '',
            'assert_type': assert_type,
            'assert_value': assert_value,
            'wait_time': 100,
            'action_params': {},
        })

    def test_wait_for_uses_visible_unless_locator_is_an_option(self):
        for selector, expected_state in (
            ('.publish-button', 'visible'),
            ('#fullList', 'visible'),
            ('ul > li', 'visible'),
            ('.el-select-dropdown li', 'attached'),
            ('[role="option"]', 'attached'),
        ):
            with self.subTest(selector=selector):
                self.locator.wait_for.reset_mock()
                result = self.run_step('waitFor', selector)
                self.assertTrue(result['success'], result.get('error'))
                self.locator.wait_for.assert_called_once_with(state=expected_state, timeout=100)

    def test_has_attribute_checks_presence_exact_value_and_missing_attributes(self):
        cases = (
            ('href', 'https://example.com', True),
            ('disabled', '', True),
            ('href=https://example.com', 'https://example.com', True),
            ('href=https://wrong.example', 'https://example.com', False),
            ('missing', None, False),
        )

        for specification, actual_value, expected in cases:
            with self.subTest(specification=specification):
                self.locator.get_attribute.return_value = actual_value
                result = self.run_step(
                    'assert', '#target', assert_type='hasAttribute', assert_value=specification
                )

                self.assertEqual(result['success'], expected, result.get('error'))
                self.locator.get_attribute.assert_called_with(
                    specification.partition('=')[0], timeout=100
                )

    def test_has_attribute_rejects_empty_attribute_name(self):
        result = self.run_step('assert', '#target', assert_type='hasAttribute', assert_value='=value')

        self.assertFalse(result['success'])
        self.assertIn('需要指定属性名', result['error'])
        self.locator.get_attribute.assert_not_called()


class PearMigrationSerializerTests(SimpleTestCase):
    def test_has_attribute_syntax_is_documented_in_api_schema(self):
        help_text = TestCaseStepSerializer().fields['assert_value'].help_text

        self.assertIn('属性名=期望值', help_text)

    def test_custom_shop_add_locator_keeps_pear_disambiguation(self):
        """Pear 的 +Add 自定义动作必须保留 CSS 过滤和首个匹配语义。"""
        page = MagicMock()
        locator = MagicMock()
        page.locator.return_value = locator

        result = build_playwright_locator(
            page,
            'css',
            'button[data-testid="enhance-button"]:has([data-testid="AddLineIcon"]):has-text("Add")',
            {'first': True},
        )

        page.locator.assert_called_once_with(
            'button[data-testid="enhance-button"]:has([data-testid="AddLineIcon"]):has-text("Add")'
        )
        self.assertIs(result, locator.first)


class ElementPageGroupingTests(TestCase):
    """确保 API 创建元素时页面字段会同步到元素分组。"""

    def setUp(self):
        user = get_user_model().objects.create_user(username='element-group-user', password='test-pass-123')
        self.project = UiProject.objects.create(
            name='Element grouping test project',
            base_url='https://example.test',
            owner=user,
        )
        self.strategy = LocatorStrategy.objects.create(name='role')

    def _serializer(self, **overrides):
        data = {
            'project_id': self.project.id,
            'name': 'add-button',
            'element_type': 'BUTTON',
            'page': 'Storefront',
            'locator_strategy_id': self.strategy.id,
            'locator_value': 'button',
            'locator_options': {'name': 'Add'},
        }
        data.update(overrides)
        serializer = ElementSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer

    def test_create_page_without_group_creates_and_links_top_level_group(self):
        element = self._serializer().save()

        self.assertIsNotNone(element.group_id)
        self.assertEqual(element.group.name, 'Storefront')
        self.assertEqual(element.group.project_id, self.project.id)

    def test_update_page_without_group_moves_element_to_new_page_group(self):
        element = self._serializer().save()
        serializer = ElementSerializer(
            element,
            data={'page': 'Checkout'},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        element = serializer.save()

        self.assertEqual(element.page, 'Checkout')
        self.assertEqual(element.group.name, 'Checkout')

    def test_create_with_explicit_null_group_keeps_element_unassigned(self):
        element = self._serializer(group_id=None).save()

        self.assertIsNone(element.group_id)
    def test_update_with_explicit_null_group_clears_existing_group(self):
        element = self._serializer().save()
        serializer = ElementSerializer(
            element,
            data={'group_id': None},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        element = serializer.save()

        self.assertIsNone(element.group_id)


class TestCaseStepApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='step-api-user', password='test-pass-123')
        self.project = UiProject.objects.create(
            name='Step API project', base_url='https://example.test', owner=self.user
        )
        self.test_case = UiTestCase.objects.create(
            name='API-created step case', project=self.project, created_by=self.user
        )
        self.strategy = LocatorStrategy.objects.create(name='css')
        self.element = Element.objects.create(
            project=self.project,
            name='target',
            locator_strategy=self.strategy,
            locator_value='#target',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_step_through_api_assigns_accessible_test_case(self):
        response = self.client.post('/api/ui-automation/test-case-steps/', {
            'test_case': self.test_case.id,
            'step_number': 1,
            'action_type': 'click',
            'element': self.element.id,
            'wait_time': 1000,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['element'], self.element.id)
        self.assertTrue(self.test_case.steps.filter(step_number=1, element=self.element).exists())

    def test_create_step_cannot_target_another_users_test_case(self):
        other_user = get_user_model().objects.create_user(username='other-step-user', password='test-pass-123')
        other_project = UiProject.objects.create(
            name='Other step project', base_url='https://other.example', owner=other_user
        )
        other_case = UiTestCase.objects.create(
            name='Private case', project=other_project, created_by=other_user
        )

        response = self.client.post('/api/ui-automation/test-case-steps/', {
            'test_case': other_case.id,
            'step_number': 1,
            'action_type': 'wait',
            'wait_time': 1000,
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(other_case.steps.exists())
