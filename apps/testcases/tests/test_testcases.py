from io import BytesIO
from tempfile import TemporaryDirectory
from datetime import datetime

from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from openpyxl import Workbook, load_workbook

from apps.projects.models import Project, ProjectMember
from apps.testcases.models import TestCase as TCModel, TestCaseAutomationLink, TestCaseImportRecord, TestCaseModule, TestCaseStep, TestCaseComment
from apps.testcases.services import TestCaseExcelImportService, TestCaseImportTemplateService
from apps.users.models import User
from apps.api_testing.models import ApiProject, ScenarioCase, ScenarioCaseFolder
from apps.ui_automation.models import TestCase as UiTestCase, TestCaseFolder as UiTestCaseFolder, UiProject

# 注意：testcase-list / testcase-detail URL name 与 ui_automation 模块的 router 冲突，
# 这里直接使用路径常量以避免 reverse() 解析到错误的路由。
TC_LIST_URL = '/api/testcases/'


def tc_detail_url(pk):
    return f'/api/testcases/{pk}/'


class TestCaseApiTests(APITestCase):
    """Testcases 模块 - 测试用例 API 冒烟测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='tc_tester', password='pass123456')
        self.other_user = User.objects.create_user(username='tc_other', password='pass123456')
        self.project = Project.objects.create(name='用例测试项目', owner=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role='tester')
        self.client.force_authenticate(self.user)
        self.list_url = TC_LIST_URL

    def test_list_requires_authentication(self):
        """未认证用户无法访问用例列表"""
        anon_client = APIClient()
        response = anon_client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_testcase(self):
        """创建测试用例"""
        payload = {
            'title': '登录功能验证',
            'description': '验证正常登录流程',
            'preconditions': '用户已注册',
            'steps': '1. 打开登录页\n2. 输入账号密码\n3. 点击登录',
            'expected_result': '登录成功进入首页',
            'priority': 'high',
            'test_type': 'functional',
            'project_id': self.project.id,
        }
        response = self.client.post(self.list_url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['title'], '登录功能验证')
        self.assertEqual(TCModel.objects.count(), 1)

    def test_list_testcases_with_pagination(self):
        """列表查询支持分页"""
        for i in range(15):
            TCModel.objects.create(
                title=f'用例{i}', expected_result='通过', project=self.project, author=self.user,
            )

        response = self.client.get(self.list_url, {'page': 1, 'page_size': 5})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 5)
        self.assertEqual(response.data['count'], 15)

    def test_retrieve_testcase_detail(self):
        """获取用例详情"""
        tc = TCModel.objects.create(
            title='详情测试', expected_result='通过', project=self.project, author=self.user,
        )
        TestCaseStep.objects.create(testcase=tc, step_number=1, action='点击按钮', expected='跳转')
        detail_url = tc_detail_url(tc.id)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], '详情测试')
        self.assertEqual(len(response.data['step_details']), 1)

    def test_update_testcase(self):
        """更新测试用例"""
        tc = TCModel.objects.create(
            title='旧标题', expected_result='通过', project=self.project, author=self.user,
        )
        detail_url = tc_detail_url(tc.id)
        response = self.client.patch(detail_url, {'title': '新标题'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tc.refresh_from_db()
        self.assertEqual(tc.title, '新标题')

    def test_delete_testcase(self):
        """删除测试用例"""
        tc = TCModel.objects.create(
            title='待删除', expected_result='通过', project=self.project, author=self.user,
        )
        detail_url = tc_detail_url(tc.id)
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(TCModel.objects.count(), 0)

    def test_search_testcase_by_title(self):
        """按标题搜索用例"""
        TCModel.objects.create(
            title='支付流程验证', expected_result='通过', project=self.project, author=self.user,
        )
        TCModel.objects.create(
            title='登录流程验证', expected_result='通过', project=self.project, author=self.user,
        )
        response = self.client.get(self.list_url, {'search': '支付'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertIn('支付', response.data['results'][0]['title'])

    def test_filter_by_priority(self):
        """按优先级过滤用例"""
        TCModel.objects.create(
            title='高优用例', expected_result='通过', project=self.project, author=self.user, priority='high',
        )
        TCModel.objects.create(
            title='低优用例', expected_result='通过', project=self.project, author=self.user, priority='low',
        )
        response = self.client.get(self.list_url, {'priority': 'high'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['priority'], 'high')

    def test_only_project_members_see_testcases(self):
        """非项目成员无法看到用例"""
        TCModel.objects.create(
            title='内部用例', expected_result='通过', project=self.project, author=self.user,
        )
        self.client.force_authenticate(self.other_user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 0)


class TestCaseModuleApiTests(APITestCase):
    """模块树层级、移动和用例归属的接口验证。"""

    def setUp(self):
        self.user = User.objects.create_user(username='module_tester', password='pass123456')
        self.project = Project.objects.create(name='模块测试项目', owner=self.user)
        self.other_project = Project.objects.create(name='其他项目', owner=self.user)
        self.client.force_authenticate(self.user)

    def create_module(self, project=None, parent=None, name='模块'):
        return TestCaseModule.objects.create(project=project or self.project, parent=parent, name=name)

    def test_module_creation_enforces_eight_level_limit(self):
        parent = None
        for level in range(1, 9):
            response = self.client.post('/api/testcases/modules/', {
                'project_id': self.project.id, 'parent_id': parent, 'name': f'第{level}层',
            }, format='json')
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            parent = response.data['id']

        response = self.client.post('/api/testcases/modules/', {
            'project_id': self.project.id, 'parent_id': parent, 'name': '第9层',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_module_move_rejects_cycle_and_depth_overflow(self):
        root = self.create_module(name='根')
        child = self.create_module(parent=root, name='子')
        response = self.client.put(f'/api/testcases/modules/{root.id}/', {'parent_id': child.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        deep_parent = None
        for level in range(1, 8):
            deep_parent = self.create_module(name=f'深{level}') if deep_parent is None else self.create_module(parent=deep_parent, name=f'深{level}')
        response = self.client.put(f'/api/testcases/modules/{root.id}/', {'parent_id': deep_parent.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_module_move_rejects_cross_project_parent(self):
        module = self.create_module(name='待移动')
        foreign_parent = self.create_module(project=self.other_project, name='外部模块')
        response = self.client.put(f'/api/testcases/modules/{module.id}/', {'parent_id': foreign_parent.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_module_reorder_persists_manual_sibling_order(self):
        first = self.create_module(name='Charlie')
        second = self.create_module(name='Alpha')
        third = self.create_module(name='Bravo')

        response = self.client.get('/api/testcases/modules/', {'project': self.project.id})
        self.assertEqual([module['name'] for module in response.data], ['Alpha', 'Bravo', 'Charlie'])

        response = self.client.post('/api/testcases/modules/reorder/', {
            'project_id': self.project.id,
            'parent_id': None,
            'module_ids': [first.id, second.id, third.id],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get('/api/testcases/modules/', {'project': self.project.id})
        self.assertEqual([module['id'] for module in response.data], [first.id, second.id, third.id])

    def test_module_reorder_requires_the_complete_sibling_set(self):
        parent = self.create_module(name='父目录')
        child = self.create_module(parent=parent, name='子目录')
        sibling = self.create_module(name='另一个根目录')

        response = self.client.post('/api/testcases/modules/reorder/', {
            'project_id': self.project.id,
            'parent_id': None,
            'module_ids': [parent.id],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post('/api/testcases/modules/reorder/', {
            'project_id': self.project.id,
            'parent_id': parent.id,
            'module_ids': [sibling.id],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post('/api/testcases/modules/reorder/', {
            'project_id': self.project.id,
            'parent_id': parent.id,
            'module_ids': [child.id, child.id],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_new_and_moved_modules_are_appended_to_siblings(self):
        self.create_module(name='Zulu')
        self.create_module(name='Alpha')
        response = self.client.post('/api/testcases/modules/', {
            'project_id': self.project.id, 'parent_id': None, 'name': 'Aardvark',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        destination = self.create_module(name='目标目录')
        self.create_module(parent=destination, name='Zulu 子目录')
        self.create_module(parent=destination, name='Alpha 子目录')
        source = self.create_module(name='待移动目录')
        response = self.client.put(f'/api/testcases/modules/{source.id}/', {
            'parent_id': destination.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get('/api/testcases/modules/', {'project': self.project.id})
        roots = {module['name']: module for module in response.data}
        root_names = [module['name'] for module in response.data]
        self.assertLess(root_names.index('Alpha'), root_names.index('Zulu'))
        self.assertEqual(root_names[-1], 'Aardvark')
        self.assertEqual(
            [module['name'] for module in roots['目标目录']['children']],
            ['Alpha 子目录', 'Zulu 子目录', '待移动目录'],
        )

    def test_module_filter_includes_descendants_and_cases_can_move_to_unassigned(self):
        parent = self.create_module(name='父模块')
        child = self.create_module(parent=parent, name='子模块')
        case = TCModel.objects.create(title='子模块用例', expected_result='通过', project=self.project, author=self.user, module=child)
        response = self.client.get(TC_LIST_URL, {'project': self.project.id, 'module': parent.id, 'include_children': 'true'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

        response = self.client.post('/api/testcases/move/', {'testcase_ids': [case.id], 'module_id': None}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        case.refresh_from_db()
        self.assertIsNone(case.module_id)


class TestCaseImportTemplateTests(APITestCase):
    def test_template_matches_module_columns_and_case_fields(self):
        workbook = load_workbook(TestCaseImportTemplateService.build_template(), read_only=True)
        headers = list(next(workbook['用例导入模板'].iter_rows(min_row=1, max_row=1, values_only=True)))
        self.assertEqual(headers[:3], [f'{level}级模块' for level in ('一', '二', '三')])
        self.assertEqual(headers[3:], ['用例名称', '优先级', '测试类型', '前置条件', '步骤描述', '预期结果', '备注', '维护人'])


class TestCaseModuleImportTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='module_importer', password='pass123456')
        self.project = Project.objects.create(name='模块导入项目', owner=self.user)

    def test_import_builds_and_reuses_module_hierarchy_from_eight_level_columns(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = '用例导入模板'
        module_headers = [f'{level}级模块' for level in ('一', '二', '三', '四', '五', '六', '七', '八')]
        worksheet.append(module_headers + TestCaseImportTemplateService.HEADERS[3:])
        full_path = [f'模块{level}' for level in range(1, 9)]
        case_fields = ['高', '功能测试', '', '步骤描述', '预期结果', '备注内容', '']
        worksheet.append(full_path + ['完整路径用例1'] + case_fields)
        worksheet.append(full_path + ['完整路径用例2'] + case_fields)
        worksheet.append(['模块1'] + [''] * 7 + ['一级模块用例'] + ['中', '功能测试', '', '步骤描述', '预期结果', '', ''])
        worksheet.append([''] * 8 + ['无模块用例'] + ['低', '功能测试', '', '步骤描述', '预期结果', '', ''])
        worksheet.append(['跳级父模块', '', '跳级子模块'] + [''] * 5 + ['跳级用例'] + ['中', '功能测试', '', '步骤描述', '预期结果', '', ''])
        output = BytesIO()
        workbook.save(output)

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            record = TestCaseImportRecord.objects.create(
                import_no='IMP_MODULE_PATH_TEST',
                project=self.project,
                created_by=self.user,
            )
            record.import_file.save('module-paths.xlsx', ContentFile(output.getvalue()), save=True)
            summary = TestCaseExcelImportService.import_record(record)

        self.assertEqual((summary.total_rows, summary.success_count, summary.failed_count), (5, 4, 1))
        self.assertIn('模块层级不能跳级填写', summary.failure_details[0]['errors'])
        self.assertEqual(TestCaseModule.objects.filter(project=self.project).count(), 8)

        leaf = TestCaseModule.objects.get(project=self.project, name='模块8')
        self.assertEqual(leaf.parent.name, '模块7')
        self.assertEqual(leaf.parent.parent.name, '模块6')
        full_path_cases = TCModel.objects.filter(title__startswith='完整路径用例')
        self.assertEqual(full_path_cases.count(), 2)
        self.assertEqual(set(full_path_cases.values_list('module_id', flat=True)), {leaf.id})
        self.assertEqual(set(full_path_cases.values_list('priority', flat=True)), {'high'})
        self.assertEqual(TCModel.objects.get(title='一级模块用例').module.name, '模块1')
        self.assertEqual(TCModel.objects.get(title='一级模块用例').priority, 'medium')
        self.assertIsNone(TCModel.objects.get(title='无模块用例').module_id)
        self.assertEqual(TCModel.objects.get(title='无模块用例').priority, 'low')

    def test_import_preserves_optional_created_and_updated_timestamps(self):
        created_at = datetime(2023, 4, 5, 6, 7, 8)
        updated_at = datetime(2024, 5, 6, 7, 8, 9)
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = '用例导入模板'
        worksheet.append(TestCaseImportTemplateService.HEADERS + ['创建时间', '更新时间'])
        worksheet.append([''] * 3 + [
            '时间导入用例', '中', '功能测试', '', '步骤描述', '预期结果', '', '', created_at, updated_at,
        ])
        output = BytesIO()
        workbook.save(output)

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            record = TestCaseImportRecord.objects.create(
                import_no='IMP_IMPORT_CASE_TIMESTAMPS',
                project=self.project,
                created_by=self.user,
            )
            record.import_file.save('case-timestamps.xlsx', ContentFile(output.getvalue()), save=True)
            summary = TestCaseExcelImportService.import_record(record)

        self.assertEqual((summary.success_count, summary.failed_count), (1, 0))
        testcase = TCModel.objects.get(title='时间导入用例')
        self.assertEqual(testcase.created_at, timezone.make_aware(created_at, timezone.get_current_timezone()))
        self.assertEqual(testcase.updated_at, timezone.make_aware(updated_at, timezone.get_current_timezone()))

    def test_import_accepts_reordered_headers(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = '用例导入模板'
        headers = ['步骤描述', '二级模块', '用例名称', '优先级', '测试类型', '前置条件', '预期结果', '备注', '维护人', '一级模块']
        worksheet.append(headers)
        worksheet.append(['步骤描述', '子模块', '可调换顺序用例', '高', '功能测试', '', '预期结果', '', '', '父模块'])
        output = BytesIO()
        workbook.save(output)

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            record = TestCaseImportRecord.objects.create(
                import_no='IMP_IMPORT_REORDERED_HEADERS',
                project=self.project,
                created_by=self.user,
            )
            record.import_file.save('reordered-headers.xlsx', ContentFile(output.getvalue()), save=True)
            summary = TestCaseExcelImportService.import_record(record)

        self.assertEqual((summary.success_count, summary.failed_count), (1, 0))
        testcase = TCModel.objects.get(title='可调换顺序用例')
        self.assertEqual(testcase.steps, '步骤描述')
        self.assertEqual(testcase.priority, 'high')
        self.assertEqual(testcase.module.name, '子模块')
        self.assertEqual(testcase.module.parent.name, '父模块')


class TestCaseAutomationLinkTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='automation_link_user', password='pass123456')
        self.workspace_project = Project.objects.create(name='自动化关联项目', owner=self.user)
        self.test_case = TCModel.objects.create(
            title='正式登录用例',
            expected_result='登录成功',
            project=self.workspace_project,
            author=self.user,
        )
        self.ui_project = UiProject.objects.create(
            name='UI关联项目',
            base_url='https://example.test',
            owner=self.user,
            workspace_project=self.workspace_project,
        )
        self.ui_folder = UiTestCaseFolder.objects.create(name='UI登录目录', project=self.ui_project)
        self.ui_case = UiTestCase.objects.create(
            name='UI登录流程',
            project=self.ui_project,
            folder=self.ui_folder,
            created_by=self.user,
        )
        self.api_project = ApiProject.objects.create(
            name='接口关联项目',
            project_type='HTTP',
            status='IN_PROGRESS',
            owner=self.user,
            workspace_project=self.workspace_project,
        )
        self.api_folder = ScenarioCaseFolder.objects.create(name='接口登录目录', project=self.api_project)
        self.scenario_case = ScenarioCase.objects.create(
            name='登录接口场景',
            project=self.api_project,
            folder=self.api_folder,
            created_by=self.user,
        )
        self.client.force_authenticate(self.user)

    def test_formal_case_can_link_one_case_of_each_type(self):
        ui_response = self.client.post(
            f'/api/testcases/{self.test_case.id}/automation-links/',
            {'type': 'ui_test_case', 'object_id': self.ui_case.id},
            format='json',
        )
        api_response = self.client.post(
            f'/api/testcases/{self.test_case.id}/automation-links/',
            {'type': 'api_scenario_case', 'object_id': self.scenario_case.id},
            format='json',
        )
        self.assertEqual(ui_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(api_response.status_code, status.HTTP_201_CREATED)

        list_response = self.client.get(TC_LIST_URL)
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        listed_case = list_response.data['results'][0]
        self.assertEqual(listed_case['id'], self.test_case.id)
        self.assertEqual(
            {link['type'] for link in listed_case['automation_links']},
            {'ui_test_case', 'api_scenario_case'},
        )
        self.assertEqual(
            {link['target_name'] for link in listed_case['automation_links']},
            {'UI登录流程', '登录接口场景'},
        )

    def test_duplicate_type_returns_conflict(self):
        url = f'/api/testcases/{self.test_case.id}/automation-links/'
        first = self.client.post(url, {'type': 'ui_test_case', 'object_id': self.ui_case.id}, format='json')
        second = self.client.post(url, {'type': 'ui_test_case', 'object_id': self.ui_case.id}, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(TestCaseAutomationLink.objects.filter(test_case=self.test_case).count(), 1)

    def test_options_are_limited_to_same_workspace_project(self):
        other_workspace = Project.objects.create(name='其他项目', owner=self.user)
        other_ui_project = UiProject.objects.create(
            name='其他UI项目',
            base_url='https://other.example.test',
            owner=self.user,
            workspace_project=other_workspace,
        )
        UiTestCase.objects.create(name='不可关联的用例', project=other_ui_project, created_by=self.user)

        response = self.client.get(
            f'/api/testcases/{self.test_case.id}/automation-options/',
            {'type': 'ui_test_case'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item['id'] for item in response.data['results']], [self.ui_case.id])

    def test_options_include_folder_hierarchy_for_tree_selection(self):
        ui_response = self.client.get(
            f'/api/testcases/{self.test_case.id}/automation-options/',
            {'type': 'ui_test_case'},
        )
        api_response = self.client.get(
            f'/api/testcases/{self.test_case.id}/automation-options/',
            {'type': 'api_scenario_case'},
        )

        self.assertEqual(ui_response.data['results'][0]['folder_id'], self.ui_folder.id)
        self.assertEqual(ui_response.data['folders'][0]['id'], self.ui_folder.id)
        self.assertEqual(ui_response.data['folders'][0]['parent_id'], None)
        self.assertEqual(api_response.data['results'][0]['folder_id'], self.api_folder.id)
        self.assertEqual(api_response.data['folders'][0]['id'], self.api_folder.id)

    def test_link_rejects_target_from_another_workspace_project(self):
        other_workspace = Project.objects.create(name='不匹配的关联项目', owner=self.user)
        other_ui_project = UiProject.objects.create(
            name='不匹配UI项目',
            base_url='https://other-workspace.example.test',
            owner=self.user,
            workspace_project=other_workspace,
        )
        other_ui_case = UiTestCase.objects.create(
            name='跨项目UI用例',
            project=other_ui_project,
            created_by=self.user,
        )

        response = self.client.post(
            f'/api/testcases/{self.test_case.id}/automation-links/',
            {'type': 'ui_test_case', 'object_id': other_ui_case.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(TestCaseAutomationLink.objects.filter(test_case=self.test_case).exists())

    def test_deleting_automation_target_clears_its_link(self):
        link = TestCaseAutomationLink.objects.create(
            test_case=self.test_case,
            case_type='ui_test_case',
            object_id=self.ui_case.id,
            linked_by=self.user,
        )
        self.ui_case.delete()
        self.assertFalse(TestCaseAutomationLink.objects.filter(pk=link.pk).exists())

    def test_missing_target_remains_visible_for_unlinking(self):
        link = TestCaseAutomationLink.objects.create(
            test_case=self.test_case,
            case_type='ui_test_case',
            object_id=999999,
            linked_by=self.user,
        )
        response = self.client.get(TC_LIST_URL)
        listed_link = response.data['results'][0]['automation_links'][0]
        self.assertEqual(listed_link['id'], link.id)
        self.assertFalse(listed_link['target_exists'])
        self.assertIsNone(listed_link['detail_url'])

        unlink_response = self.client.delete(
            f'/api/testcases/{self.test_case.id}/automation-links/{link.id}/'
        )
        self.assertEqual(unlink_response.status_code, status.HTTP_204_NO_CONTENT)
