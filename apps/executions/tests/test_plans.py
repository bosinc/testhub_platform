from unittest import mock

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.executions import services
from apps.executions.models import TestPlan, TestRun, TestRunCase
from apps.projects.models import Project
from apps.testcases.models import TestCase
from apps.versions.models import Version

User = get_user_model()


class PlanTestBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username='plan_owner', email='owner@example.com')
        self.assignee = User.objects.create(username='plan_assignee', email='assignee@example.com')
        self.project = Project.objects.create(name='计划项目', owner=self.user)
        self.other_project = Project.objects.create(name='另一个项目', owner=self.user)
        self.case_a = TestCase.objects.create(project=self.project, title='用例 A', author=self.user)
        self.case_b = TestCase.objects.create(project=self.project, title='用例 B', author=self.user)
        self.version = Version.objects.create(name='v1.0', created_by=self.user)
        self.client.force_authenticate(self.user)

    def create_plan(self, **overrides):
        params = {
            'creator': self.user,
            'fields': {'name': '回归计划'},
            'version_id': self.version.id,
            'assignee_ids': [self.assignee.id],
            'project_ids': [self.project.id],
            'testcase_ids': [self.case_a.id, self.case_b.id],
        }
        params.update(overrides)
        return services.create_test_plan(**params)

    def run_case(self, plan, testcase):
        return TestRunCase.objects.get(test_run__test_plan=plan, testcase=testcase)


class TestPlanServiceTests(PlanTestBase):
    """services 是视图与 MCP 共用的唯一写入口。"""

    def test_create_builds_runs_and_assigns_single_assignee_as_executor(self):
        plan = self.create_plan(fields={'name': '回归计划', 'description': '说明'})

        self.assertEqual(plan.description, '说明')
        self.assertEqual(plan.version, self.version)
        run = TestRun.objects.get(test_plan=plan)
        self.assertEqual(run.project, self.project)
        self.assertEqual(run.run_cases.count(), 2)
        self.assertEqual(self.run_case(plan, self.case_a).executed_by, self.assignee)

    def test_create_ignores_cases_outside_plan_projects(self):
        foreign_case = TestCase.objects.create(
            project=self.other_project, title='别的项目用例', author=self.user)

        plan = self.create_plan(testcase_ids=[self.case_a.id, foreign_case.id])

        self.assertEqual(
            list(TestRunCase.objects.filter(test_run__test_plan=plan).values_list('testcase_id', flat=True)),
            [self.case_a.id],
        )

    def test_create_rolls_back_when_run_sync_fails(self):
        with mock.patch.object(services, 'sync_test_runs', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.create_plan()

        self.assertFalse(TestPlan.objects.exists())

    def test_update_without_relations_keeps_runs_version_and_assignees(self):
        plan = self.create_plan()
        TestRunCase.objects.filter(pk=self.run_case(plan, self.case_a).pk).update(status='passed')

        services.update_test_plan(plan, fields={'name': '改名', 'is_active': False})

        plan.refresh_from_db()
        self.assertEqual((plan.name, plan.is_active), ('改名', False))
        self.assertEqual(plan.version, self.version)
        self.assertEqual(list(plan.assignees.all()), [self.assignee])
        self.assertEqual(self.run_case(plan, self.case_a).status, 'passed')

    def test_update_version_none_clears_version(self):
        plan = self.create_plan()

        services.update_test_plan(plan, version_id=None)

        plan.refresh_from_db()
        self.assertIsNone(plan.version)

    def test_update_testcases_keeps_results_of_remaining_cases(self):
        plan = self.create_plan()
        TestRunCase.objects.filter(pk=self.run_case(plan, self.case_a).pk).update(status='passed')
        case_c = TestCase.objects.create(project=self.project, title='用例 C', author=self.user)

        services.update_test_plan(plan, testcase_ids=[self.case_a.id, case_c.id])

        self.assertEqual(self.run_case(plan, self.case_a).status, 'passed')
        self.assertFalse(
            TestRunCase.objects.filter(test_run__test_plan=plan, testcase=self.case_b).exists())
        self.assertEqual(self.run_case(plan, case_c).status, 'untested')

    def test_update_projects_only_keeps_current_cases(self):
        plan = self.create_plan()

        services.update_test_plan(plan, project_ids=[self.project.id, self.other_project.id])

        self.assertEqual(TestRun.objects.filter(test_plan=plan).count(), 2)
        self.assertEqual(TestRunCase.objects.filter(test_run__test_plan=plan).count(), 2)

    def test_accessible_plans_cover_creator_assignee_and_project_member(self):
        plan = self.create_plan()
        member = User.objects.create(username='plan_member', email='member@example.com')
        self.project.members.add(member)
        stranger = User.objects.create(username='plan_stranger', email='stranger@example.com')

        for user in (self.user, self.assignee, member):
            self.assertEqual(list(services.accessible_test_plans(user)), [plan])
        self.assertFalse(services.accessible_test_plans(stranger).exists())


class TestPlanApiTests(PlanTestBase):
    """计划接口经 services 落库后的对外行为。"""

    list_url = '/api/executions/plans/'

    def detail_url(self, plan):
        return f'{self.list_url}{plan.id}/'

    def test_create_saves_description_and_builds_runs(self):
        response = self.client.post(self.list_url, {
            'name': '接口创建',
            'description': '页面填写的描述',
            'projects': [self.project.id],
            'version': self.version.id,
            'testcases': [self.case_a.id],
            'assignees': [self.assignee.id],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        plan = TestPlan.objects.get(id=response.data['id'])
        self.assertEqual(plan.creator, self.user)
        self.assertEqual(plan.description, '页面填写的描述')
        self.assertEqual(plan.version, self.version)
        self.assertEqual(self.run_case(plan, self.case_a).executed_by, self.assignee)

    def test_status_toggle_keeps_version_and_runs(self):
        plan = self.create_plan()

        response = self.client.patch(self.detail_url(plan), {'is_active': False}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        plan.refresh_from_db()
        self.assertFalse(plan.is_active)
        self.assertEqual(plan.version, self.version)
        self.assertEqual(list(plan.assignees.all()), [self.assignee])
        self.assertEqual(TestRunCase.objects.filter(test_run__test_plan=plan).count(), 2)

    def test_full_update_syncs_runs_and_clears_assignees(self):
        plan = self.create_plan()
        TestRunCase.objects.filter(pk=self.run_case(plan, self.case_a).pk).update(status='failed')

        response = self.client.put(self.detail_url(plan), {
            'name': '回归计划-v2',
            'description': '改过的描述',
            'projects': [self.project.id],
            'version': None,
            'testcases': [self.case_a.id],
            'assignees': [],
            'is_active': True,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        plan.refresh_from_db()
        self.assertEqual((plan.name, plan.description), ('回归计划-v2', '改过的描述'))
        self.assertIsNone(plan.version)
        self.assertFalse(plan.assignees.exists())
        self.assertEqual(self.run_case(plan, self.case_a).status, 'failed')
        self.assertFalse(
            TestRunCase.objects.filter(test_run__test_plan=plan, testcase=self.case_b).exists())

    def test_link_testcases_replaces_plan_cases(self):
        plan = self.create_plan(testcase_ids=[self.case_a.id])

        response = self.client.post(
            f'{self.detail_url(plan)}link_testcases/', {'testcases': [self.case_b.id]}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            list(TestRunCase.objects.filter(test_run__test_plan=plan).values_list('testcase_id', flat=True)),
            [self.case_b.id],
        )
