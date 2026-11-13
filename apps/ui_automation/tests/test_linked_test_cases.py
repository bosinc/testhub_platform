from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.projects.models import Project, ProjectMember
from apps.testcases.models import (
    TestCase as FormalTestCase, TestCaseAutomationLink, TestCaseStep,
)
from apps.ui_automation.models import TestCase as UiTestCase, UiProject


class LinkedTestCaseDetailTests(TestCase):
    """关联详情遵循正式用例权限，并且不增加 UI 用例列表查询开销。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='linked-case-owner')
        cls.member = get_user_model().objects.create_user(username='linked-case-member')
        cls.workspace = Project.objects.create(name='Workspace', owner=cls.user)
        cls.project = UiProject.objects.create(
            name='UI project', base_url='https://example.test', owner=cls.user,
            workspace_project=cls.workspace,
        )
        cls.ui_case = UiTestCase.objects.create(
            project=cls.project, created_by=cls.user, name='UI case',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f'/api/ui-automation/test-cases/{self.ui_case.pk}/'

    def link_case(self, title='Formal case', project=None, case_type='ui_test_case', object_id=None):
        case = FormalTestCase.objects.create(
            title=title, project=project or self.workspace, author=self.user,
            priority='high', test_type='functional', preconditions='已登录<br>打开页面',
            notes='用例备注', description='用例描述',
        )
        TestCaseAutomationLink.objects.create(
            test_case=case, case_type=case_type,
            object_id=object_id if object_id is not None else self.ui_case.pk,
        )
        return case

    def test_detail_returns_multiple_cases_with_ordered_steps_and_display_fields(self):
        first = self.link_case()
        second = self.link_case('Second case')
        TestCaseStep.objects.create(testcase=first, sort_order=2, action='提交', expected='保存成功')
        TestCaseStep.objects.create(testcase=first, sort_order=1, action='填写', expected='正常回显')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        cases = response.data['linked_test_cases']
        self.assertEqual([case['id'] for case in cases], [first.pk, second.pk])
        self.assertEqual(cases[0]['title'], first.title)
        self.assertEqual(cases[0]['project'], {'id': self.workspace.pk, 'name': self.workspace.name})
        self.assertEqual(cases[0]['author']['username'], self.user.username)
        for field in ('priority', 'test_type', 'preconditions', 'notes', 'description'):
            self.assertEqual(cases[0][field], getattr(first, field))
        self.assertEqual([step['action'] for step in cases[0]['steps']], ['填写', '提交'])

    def test_empty_detail_and_unbound_project_return_empty_array(self):
        self.assertEqual(self.client.get(self.url).data['linked_test_cases'], [])
        self.link_case()
        self.project.workspace_project = None
        self.project.save(update_fields=['workspace_project'])
        self.assertEqual(self.client.get(self.url).data['linked_test_cases'], [])

    def test_filters_other_types_targets_and_mismatched_workspaces(self):
        other_workspace = Project.objects.create(name='Other workspace', owner=self.user)
        self.link_case(project=other_workspace)
        self.link_case(case_type='api_scenario_case')
        self.link_case(object_id=self.ui_case.pk + 100)
        self.assertEqual(self.client.get(self.url).data['linked_test_cases'], [])

    def test_ui_members_need_formal_project_permission(self):
        case = self.link_case()
        self.project.members.add(self.member)
        self.client.force_authenticate(self.member)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['linked_test_cases'], [])
        ProjectMember.objects.create(project=self.workspace, user=self.member)
        self.assertEqual(self.client.get(self.url).data['linked_test_cases'][0]['id'], case.pk)
        self.workspace.is_deleted = True
        self.workspace.save(update_fields=['is_deleted'])
        self.assertEqual(self.client.get(self.url).data['linked_test_cases'], [])

    def test_requires_authentication_and_ui_project_permission(self):
        self.link_case()
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_list_does_not_query_or_serialize_formal_cases(self):
        self.link_case()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/ui-automation/test-cases/', {'project': self.project.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('linked_test_cases', response.data['results'][0])
        self.assertFalse(any('testcase_automation_links' in query['sql'] for query in queries))

    def test_linked_cases_are_readonly_when_updating_ui_case(self):
        case = self.link_case()
        response = self.client.patch(self.url, {
            'name': 'Updated UI case', 'linked_test_cases': [],
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.url).data['linked_test_cases'][0]['id'], case.pk)

    def test_query_count_does_not_grow_with_linked_cases(self):
        self.link_case()
        with CaptureQueriesContext(connection) as first_queries:
            self.client.get(self.url)
        for index in range(5):
            self.link_case(f'Extra case {index}')
        with CaptureQueriesContext(connection) as many_queries:
            response = self.client.get(self.url)
        self.assertEqual(len(response.data['linked_test_cases']), 6)
        self.assertEqual(len(first_queries), len(many_queries))
