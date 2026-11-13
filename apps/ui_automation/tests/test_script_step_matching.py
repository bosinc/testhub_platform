from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.ui_automation.models import Element, ElementGroup, LocatorStrategy, TestScript, UiProject, TestCaseStep


class ScriptStepMatchingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='step-matching-user')
        cls.project = UiProject.objects.create(name='Step matching', owner=cls.user)
        cls.role = LocatorStrategy.objects.create(name='role')
        cls.css = LocatorStrategy.objects.create(name='CSS')
        cls.script = TestScript.objects.create(project=cls.project, name='Recorded script')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f'/api/ui-automation/test-scripts/{self.script.pk}/parse-steps/'

    def parse(self, actions):
        content = 'def run(page):\n' + '\n'.join(f'    {action}' for action in actions)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.url, {'content': content}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['steps'], len(queries)

    def test_query_count_stays_constant_for_repeated_and_distinct_locators(self):
        elements = [Element.objects.create(
            project=self.project, name=f'Submit {index}', page='Login',
            locator_strategy=self.role, locator_value='button',
            locator_options={'name': f'Submit {index}', 'exact': True},
        ) for index in range(40)]
        actions = [f'page.get_by_role("button", name="Submit {index}", exact=True).click()'
                   for index in range(40)]
        _, small_queries = self.parse(actions[:1])
        steps, large_queries = self.parse(actions * 3)

        # 包含权限校验、批量匹配及回显数据构建，查询数不随步骤数增长。
        self.assertEqual(small_queries, 2)
        self.assertEqual(large_queries, small_queries)
        self.assertEqual([step['element_id'] for step in steps], [item.pk for item in elements] * 3)
        self.assertEqual(steps[0]['target_element'], {
            'id': elements[0].pk, 'name': 'Submit 0', 'page': 'Login',
            'locator_strategy': {'name': 'role'}, 'locator_value': 'button',
            'locator_options': {'name': 'Submit 0', 'exact': True},
        })
        self.assertEqual(Element.objects.count(), 40)

    def test_matches_only_same_project_strategy_value_and_options(self):
        other_project = UiProject.objects.create(name='Other project', owner=self.user)
        Element.objects.create(project=other_project, name='Foreign', locator_strategy=self.role,
                               locator_value='button', locator_options={'name': 'Foreign'})
        Element.objects.create(project=self.project, name='Different strategy', locator_strategy=self.css,
                               locator_value='button', locator_options={'name': 'Foreign'})
        Element.objects.create(project=self.project, name='Different options', locator_strategy=self.role,
                               locator_value='button', locator_options={'name': 'Foreign', 'exact': True})
        css = Element.objects.create(project=self.project, name='Submit CSS', locator_strategy=self.css,
                                     locator_value='#submit')

        steps, queries = self.parse([
            'page.get_by_role("button", name="Foreign").click()',
            'page.locator("#submit").click()',
            'page.locator("#missing").click()',
            'page.wait_for_timeout(100)',
        ])
        self.assertEqual(queries, 2)
        self.assertEqual([step['element_id'] for step in steps], ['', css.pk, '', ''])
        self.assertNotIn('target_element', steps[0])

    def test_duplicate_locator_keeps_existing_element_library_order(self):
        for page, order, name in [('B', 0, 'First created'), ('A', 2, 'Later'), ('A', 1, 'Preferred')]:
            # 特意以不同 JSON 键顺序保存，验证索引与定位器参数顺序无关。
            element = Element.objects.create(
                project=self.project, name=name, page=page, order=order,
                locator_strategy=self.role, locator_value='button',
                locator_options={'exact': True, 'name': 'Submit'},
            )
        steps, _ = self.parse(['page.get_by_role("button", name="Submit", exact=True).click()'])
        self.assertEqual(steps[0]['element_id'], element.pk)

    def test_non_element_actions_do_not_query_elements(self):
        steps, queries = self.parse(['page.goto("https://example.test")', 'page.wait_for_timeout(100)'])
        self.assertEqual(queries, 1)
        self.assertEqual([step['element_id'] for step in steps], ['', ''])

    def test_english_editor_fallback_and_added_navigation_descriptions(self):
        response = self.client.put(
            f'/api/ui-automation/test-scripts/{self.script.pk}/editor/',
            {
                'locale': 'en', 'content': 'def run(page):\n    page.wait_for_timeout(100)\n',
                'steps': [{'action_type': 'wait', 'wait_time': 100}],
            }, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.script.steps.get().description, 'Step 1')

        response = self.client.post(
            f'/api/ui-automation/test-scripts/{self.script.pk}/to-test-case/',
            {'locale': 'en', 'start_url': 'https://example.test'}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        descriptions = list(TestCaseStep.objects.filter(test_case_id=response.data['id'])
                            .order_by('step_number').values_list('description', flat=True))
        self.assertEqual(descriptions, ['Open start URL', 'Step 1'])

    def save_unmatched_steps(self, pages):
        steps, _ = self.parse(['page.get_by_placeholder("Email").fill("demo")'] * len(pages))
        for step, page in zip(steps, pages):
            step['element_page'] = page
        response = self.client.put(
            f'/api/ui-automation/test-scripts/{self.script.pk}/editor/',
            {
                'content': 'def run(page):\n    page.get_by_placeholder("Email").fill("demo")\n',
                'start_url': 'https://example.test/inferred-page', 'steps': steps,
            }, format='json',
        )
        return response

    def test_unmatched_element_is_created_in_manually_selected_nested_page_on_save(self):
        root = ElementGroup.objects.create(project=self.project, name='Account')
        selected = ElementGroup.objects.create(project=self.project, name='Login', parent_group=root)
        response = self.save_unmatched_steps(['Login', 'Login'])
        self.assertEqual(response.status_code, 200, response.data)
        element = Element.objects.get(project=self.project, locator_value='Email')
        self.assertEqual(element.group_id, selected.pk)
        self.assertEqual(element.page, 'Login')
        self.assertEqual(list(self.script.steps.values_list('target_element_id', flat=True)), [element.pk] * 2)
        self.assertFalse(ElementGroup.objects.filter(project=self.project, name='inferred page').exists())

        # 保存后重新解析能够命中刚入库的元素，且不需要再次创建。
        steps, _ = self.parse(['page.get_by_placeholder("Email").fill("demo")'])
        self.assertEqual(steps[0]['element_id'], element.pk)

    def test_same_locator_in_two_selected_pages_creates_elements_in_each_page(self):
        first = ElementGroup.objects.create(project=self.project, name='Login')
        second = ElementGroup.objects.create(project=self.project, name='Register')
        response = self.save_unmatched_steps(['Login', 'Register', 'Login'])
        self.assertEqual(response.status_code, 200, response.data)
        elements = {element.group_id: element for element in Element.objects.all()}
        self.assertEqual(set(elements), {first.pk, second.pk})
        self.assertEqual(list(self.script.steps.values_list('target_element_id', flat=True)), [
            elements[first.pk].pk, elements[second.pk].pk, elements[first.pk].pk,
        ])

    def test_no_page_selection_keeps_element_unassigned_despite_start_url(self):
        response = self.save_unmatched_steps(['', ''])
        self.assertEqual(response.status_code, 200, response.data)
        element = Element.objects.get(project=self.project)
        self.assertEqual(element.page, '')
        self.assertIsNone(element.group_id)
        self.assertFalse(ElementGroup.objects.filter(project=self.project).exists())
        self.assertEqual(list(self.script.steps.values_list('target_element_id', flat=True)), [element.pk] * 2)

    def test_unselected_page_does_not_reuse_element_created_under_selected_page(self):
        selected = ElementGroup.objects.create(project=self.project, name='Login')
        response = self.save_unmatched_steps(['Login', '', ''])
        self.assertEqual(response.status_code, 200, response.data)
        assigned = Element.objects.get(project=self.project, group=selected)
        unassigned = Element.objects.get(project=self.project, group__isnull=True, page='')
        self.assertEqual(list(self.script.steps.values_list('target_element_id', flat=True)), [
            assigned.pk, unassigned.pk, unassigned.pk,
        ])

    def test_page_from_another_project_is_rejected_without_partial_save(self):
        other = UiProject.objects.create(name='Other project', owner=self.user)
        ElementGroup.objects.create(project=other, name='Foreign page')
        response = self.save_unmatched_steps(['Foreign page'])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Element.objects.exists())
        self.assertFalse(self.script.steps.exists())
