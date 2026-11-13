import json
import shutil
import subprocess
import unittest
from unittest import mock

from django.conf import settings
from django.test import TestCase as DjangoTestCase
from django.test import override_settings
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.base import ContentFile
from openpyxl import Workbook
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.projects.models import Project
from apps.users.models import User

from ..models import TestCase, TestCaseImportRecord, TestCaseStep
from ..services import TestCaseExcelImportService, TestCaseImportTemplateService
from ..steps import (STEP_TEXT_MAX_LENGTH, duplicate_step_ids, format_step_pairs,
                     normalize_steps, parse_step_pairs, replace_steps,
                     split_step_segments, sync_steps, validate_step_payload)


class StepParsingTests(DjangoTestCase):
    def test_parse_pairs_by_hash_order(self):
        pairs = parse_step_pairs('#1.打开登录页\n#2.输入账号密码', '#1.页面展示\n#2.输入成功')
        self.assertEqual(pairs, [
            {'action': '1.打开登录页', 'expected': '1.页面展示'},
            {'action': '2.输入账号密码', 'expected': '2.输入成功'},
        ])

    def test_missing_expected_padded_with_empty(self):
        pairs = parse_step_pairs('#1.步骤一\n#2.步骤二\n#3.步骤三\n#4.步骤四', '#1.预期一\n#2.预期二\n#3.预期三')
        self.assertEqual(len(pairs), 4)
        self.assertEqual(pairs[3], {'action': '4.步骤四', 'expected': ''})

    def test_empty_segment_becomes_empty_value(self):
        pairs = parse_step_pairs('#1.步骤一\n#\n#3.步骤三', '#1.预期一')
        self.assertEqual(pairs[1], {'action': '', 'expected': ''})
        self.assertEqual(pairs[2], {'action': '3.步骤三', 'expected': ''})

    def test_content_after_hash_kept_verbatim(self):
        """`#` 后的序号和数字内容都原样保留。"""
        self.assertEqual(parse_step_pairs('#1.5 倍流量压测', '')[0]['action'], '1.5 倍流量压测')
        self.assertEqual(parse_step_pairs('#1、点击登录', '')[0]['action'], '1、点击登录')
        self.assertEqual(parse_step_pairs('#1)提交', '')[0]['action'], '1)提交')

    def test_text_without_hash_is_single_step(self):
        self.assertEqual(split_step_segments('第一行\n第二行'), ['第一行\n第二行'])

    def test_hash_inside_content_does_not_split(self):
        """URL 里的 # 不是分隔符。"""
        pairs = parse_step_pairs(
            '#打开 https://example.com/#/login\n#输入账号',
            '#页面加载完成\n#登录成功')
        self.assertEqual([item['action'] for item in pairs], [
            '打开 https://example.com/#/login', '输入账号'])
        self.assertEqual([item['expected'] for item in pairs],
                         ['页面加载完成', '登录成功'])

    def test_continuation_lines_belong_to_previous_step(self):
        """不以 # 开头的行是上一条步骤的续行。"""
        text = (
            '#The manage button is displayed on home page\n'
            '#1. The new added ticket is displayed on app and web\n'
            '2. The info is synced to pear and auto-sync post\n'
            '3. The ticket is added to resell post\n'
            '#1. The ticket info is updated on app and web\n'
            '2. The info is synced to pear and auto-sync post\n'
            '3. The ticket is added to resell post'
        )
        segments = split_step_segments(text)
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0], 'The manage button is displayed on home page')
        self.assertEqual(
            segments[1],
            '1. The new added ticket is displayed on app and web\n'
            '2. The info is synced to pear and auto-sync post\n'
            '3. The ticket is added to resell post')

    def test_indented_hash_is_content_not_separator(self):
        """带缩进的 `#` 属于正文，不能被当成分隔符。"""
        segments = split_step_segments('#步骤一\n  #这是正文里的井号\n#步骤二')
        self.assertEqual(segments, ['步骤一\n  #这是正文里的井号', '步骤二'])

    def test_normalize_steps_keeps_expected_only_row(self):
        """只填预期结果的行是合法步骤，必须保留，不能静默丢弃。"""
        normalized = normalize_steps([
            {'action': '', 'expected': '只有预期'},
            {'action': '有效步骤', 'expected': ''},
        ])
        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0], {
            'id': None, 'sort_order': None, 'action': '', 'expected': '只有预期'})
        self.assertEqual(normalized[1]['action'], '有效步骤')

    def test_normalize_steps_drops_fully_empty_row(self):
        """整行全空的占位行（含 `#` 后没有内容）才丢弃。"""
        normalized = normalize_steps([
            {'action': '', 'expected': ''},
            {'action': '   ', 'expected': '  '},
            {'action': '有效步骤', 'expected': ''},
        ])
        self.assertEqual([item['action'] for item in normalized], ['有效步骤'])

    def test_br_tags_are_treated_as_line_breaks(self):
        """提示词与 Excel 里常用 <br> 换行，必须先归一化再按行首判定。"""
        self.assertEqual(
            split_step_segments('#打开登录页<br>#输入账号<br />#点击登录'),
            ['打开登录页', '输入账号', '点击登录'])

    def test_export_escapes_leading_hash_in_continuation(self):
        steps_text, _ = format_step_pairs([{'action': 'a\n#b', 'expected': ''}])
        self.assertEqual(steps_text, '#a\n\\#b')

    def test_export_escapes_leading_backslash(self):
        """续行原文以 `\\` 开头时导出成 `\\\\`，否则再导入会被当成转义前缀吃掉。"""
        steps_text, _ = format_step_pairs([{'action': 'a\n\\#b\n\\c', 'expected': ''}])
        self.assertEqual(steps_text, '#a\n\\\\#b\n\\\\c')
        self.assertEqual(split_step_segments(steps_text), ['a\n\\#b\n\\c'])

    def test_round_trip_keeps_backslash_in_content(self):
        steps = [
            {'action': '路径 C:\\temp\\log', 'expected': ''},
            {'action': '第一行\n\\#正文\n\\\\双反斜杠', 'expected': '预期\n\\#转义'},
        ]
        steps_text, expected_text = format_step_pairs(steps)
        self.assertEqual(parse_step_pairs(steps_text, expected_text), steps)

    def test_extra_expected_segments_are_ignored(self):
        """预期多于步骤时多出来的忽略，不生成空操作步骤。"""
        pairs = parse_step_pairs('#步骤一', '#预期一\n#预期二')
        self.assertEqual(pairs, [{'action': '步骤一', 'expected': '预期一'}])

    def test_round_trip_keeps_hash_in_content(self):
        steps = [
            {'action': '打开 https://example.com/#/login', 'expected': '页面展示'},
            {'action': '第一行\n#第二行是正文', 'expected': '第二行\n#预期正文'},
            {'action': '#标签检查', 'expected': ''},
        ]
        steps_text, expected_text = format_step_pairs(steps)
        self.assertEqual(parse_step_pairs(steps_text, expected_text), steps)

    def test_format_step_pairs_round_trip(self):
        steps = [
            {'action': '打开登录页', 'expected': '页面展示'},
            {'action': '输入账号密码', 'expected': '输入成功'},
        ]
        steps_text, expected_text = format_step_pairs(steps)
        self.assertEqual(steps_text, '#打开登录页\n#输入账号密码')
        self.assertEqual(expected_text, '#页面展示\n#输入成功')
        self.assertEqual(parse_step_pairs(steps_text, expected_text), steps)

    def test_duplicate_step_ids_detected(self):
        self.assertEqual(duplicate_step_ids([
            {'id': 3, 'action': 'a'},
            {'id': 5, 'action': 'b'},
            {'id': 3, 'action': 'c'},
        ]), [3])
        self.assertEqual(duplicate_step_ids([{'action': 'a'}, {'id': None}]), [])


class TestCaseStepApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='step_tester', password='pass123456')
        self.project = Project.objects.create(name='步骤测试项目', owner=self.user)
        self.client.force_authenticate(self.user)

    def test_create_returns_steps_with_sort_order(self):
        payload = {
            'title': '步骤顺序验证',
            'steps': [
                {'action': '第一步', 'expected': '预期一'},
                {'action': '第二步', 'expected': '预期二'},
            ],
            'project_id': self.project.id,
        }
        response = self.client.post('/api/testcases/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            [(item['sort_order'], item['action']) for item in response.data['steps']],
            [(0, '第一步'), (1, '第二步')],
        )

    def test_update_reorders_steps(self):
        case = TestCase.objects.create(title='顺序用例', project=self.project, author=self.user)
        replace_steps(case, [
            {'action': 'A', 'expected': 'a'},
            {'action': 'B', 'expected': 'b'},
        ])
        response = self.client.put(f'/api/testcases/{case.id}/', {
            'title': '顺序用例',
            'steps': [
                {'action': 'B', 'expected': 'b'},
                {'action': 'A', 'expected': 'a'},
            ],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            list(case.step_details.values_list('sort_order', 'action')),
            [(0, 'B'), (1, 'A')],
        )

    def test_update_by_id_keeps_step_identity(self):
        """带 id 的步骤按主键原地更新，不重建记录。"""
        case = TestCase.objects.create(title='增量用例', project=self.project, author=self.user)
        replace_steps(case, [
            {'action': 'A', 'expected': 'a'},
            {'action': 'B', 'expected': 'b'},
        ])
        first, second = list(case.step_details.order_by('sort_order'))
        response = self.client.put(f'/api/testcases/{case.id}/', {
            'title': '增量用例',
            'steps': [
                {'id': second.id, 'sort_order': 0, 'action': 'B2', 'expected': 'b2'},
                {'id': first.id, 'sort_order': 1, 'action': 'A2', 'expected': 'a2'},
            ],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            list(case.step_details.values_list('id', 'sort_order', 'action')),
            [(second.id, 0, 'B2'), (first.id, 1, 'A2')],
        )

    def test_update_without_id_creates_and_drops_missing(self):
        """不带 id 视为新步骤；未提交的旧步骤被删除。"""
        case = TestCase.objects.create(title='新增用例', project=self.project, author=self.user)
        replace_steps(case, [{'action': '旧步骤', 'expected': '旧预期'}])
        old_id = case.step_details.first().id
        response = self.client.put(f'/api/testcases/{case.id}/', {
            'title': '新增用例',
            'steps': [{'action': '新步骤', 'expected': '新预期'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(TestCaseStep.objects.filter(id=old_id).exists())
        self.assertEqual(
            list(case.step_details.values_list('action', 'sort_order')),
            [('新步骤', 0)],
        )

    def test_sync_keeps_new_steps_when_bulk_create_returns_no_ids(self):
        """MySQL 下 bulk_create 不回填主键时，新建步骤不能被清理逻辑误删。"""
        case = TestCase.objects.create(title='批量新建用例', project=self.project, author=self.user)
        original_bulk_create = TestCaseStep.objects.bulk_create

        def bulk_create_without_ids(objs, *args, **kwargs):
            result = original_bulk_create(objs, *args, **kwargs)
            for obj in objs:
                obj.pk = None
            return result

        with mock.patch.object(TestCaseStep.objects, 'bulk_create',
                               side_effect=bulk_create_without_ids):
            created = replace_steps(case, [
                {'action': '步骤一', 'expected': '预期一'},
                {'action': '步骤二', 'expected': '预期二'},
            ])
        self.assertEqual(created, 2)
        self.assertEqual(
            list(case.step_details.values_list('sort_order', 'action')),
            [(0, '步骤一'), (1, '步骤二')],
        )

        # 再次同步（仍不带 id）：旧步骤删除，新步骤保留。
        old_ids = set(case.step_details.values_list('id', flat=True))
        with mock.patch.object(TestCaseStep.objects, 'bulk_create',
                               side_effect=bulk_create_without_ids):
            replace_steps(case, [{'action': '新步骤', 'expected': ''}])
        self.assertFalse(TestCaseStep.objects.filter(id__in=old_ids).exists())
        self.assertEqual(
            list(case.step_details.values_list('sort_order', 'action')),
            [(0, '新步骤')],
        )

    def test_foreign_step_id_rejected(self):
        case = TestCase.objects.create(title='归属用例', project=self.project, author=self.user)
        other = TestCase.objects.create(title='别的用例', project=self.project, author=self.user)
        replace_steps(other, [{'action': '别人的步骤', 'expected': ''}])
        foreign_id = other.step_details.first().id
        response = self.client.put(f'/api/testcases/{case.id}/', {
            'title': '归属用例',
            'steps': [{'id': foreign_id, 'action': '篡改', 'expected': ''}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_step_id_rejected(self):
        """同一条步骤提交两次必须 400，且不得改动已有步骤。"""
        case = TestCase.objects.create(title='重复用例', project=self.project, author=self.user)
        replace_steps(case, [
            {'action': 'A', 'expected': 'a'},
            {'action': 'B', 'expected': 'b'},
        ])
        first = case.step_details.order_by('sort_order').first()
        response = self.client.put(f'/api/testcases/{case.id}/', {
            'title': '重复用例',
            'steps': [
                {'id': first.id, 'action': '篡改一', 'expected': ''},
                {'id': first.id, 'action': '篡改二', 'expected': ''},
            ],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('steps', response.data)
        self.assertIn('重复', str(response.data['steps']))
        # 校验失败不落库：两条旧步骤原样保留，没有被覆盖或删除
        self.assertEqual(
            list(case.step_details.values_list('action', flat=True)), ['A', 'B'])

    def test_create_with_duplicate_step_id_rejected(self):
        response = self.client.post('/api/testcases/', {
            'title': '重复创建用例',
            'project_id': self.project.id,
            'steps': [
                {'id': 1, 'action': 'A', 'expected': ''},
                {'id': 1, 'action': 'B', 'expected': ''},
            ],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('steps', response.data)

    def test_sync_steps_rejects_duplicate_ids(self):
        """绕过序列化器直接调服务层时，同样不能重复提交同一个 id。"""
        case = TestCase.objects.create(title='服务层用例', project=self.project, author=self.user)
        replace_steps(case, [{'action': '原步骤', 'expected': ''}])
        step_id = case.step_details.first().id
        with self.assertRaises(ValueError):
            sync_steps(case, [
                {'id': step_id, 'action': '一', 'expected': ''},
                {'id': step_id, 'action': '二', 'expected': ''},
            ])
        self.assertEqual(case.step_details.first().action, '原步骤')

    def test_empty_steps_rejected(self):
        response = self.client.post('/api/testcases/', {
            'title': '无步骤用例',
            'steps': [],
            'project_id': self.project.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('steps', response.data)

    def test_blank_action_allowed(self):
        """单条步骤的操作描述可以为空（只填预期结果也是合法步骤）。"""
        response = self.client.post('/api/testcases/', {
            'title': '空步骤用例',
            'steps': [{'action': '  ', 'expected': '预期'}],
            'project_id': self.project.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            [(item['action'], item['expected']) for item in response.data['steps']],
            [('', '预期')],
        )

    def test_blank_expected_allowed(self):
        """预期结果为空同样允许保存。"""
        response = self.client.post('/api/testcases/', {
            'title': '空预期用例',
            'steps': [{'action': '点击登录', 'expected': ''}],
            'project_id': self.project.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['steps'][0]['expected'], '')

    def test_step_text_over_length_limit_rejected(self):
        """REST 入口与 Excel 导入共用同一个长度上限。"""
        response = self.client.post('/api/testcases/', {
            'title': '超长步骤用例',
            'steps': [{'action': 'a' * (STEP_TEXT_MAX_LENGTH + 1), 'expected': '预期'}],
            'project_id': self.project.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(str(STEP_TEXT_MAX_LENGTH), str(response.data))

    def test_delete_case_removes_steps(self):
        case = TestCase.objects.create(title='待删用例', project=self.project, author=self.user)
        replace_steps(case, [{'action': 'A', 'expected': 'a'}])
        self.client.delete(f'/api/testcases/{case.id}/')
        self.assertEqual(TestCaseStep.objects.count(), 0)


class StepResponseShapeTests(APITestCase):
    """锁定响应结构：steps 是数组，不再出现 expected_result / step_details。"""

    def setUp(self):
        self.user = User.objects.create_user(username='shape_tester', password='pass123456')
        self.project = Project.objects.create(name='结构项目', owner=self.user)
        self.client.force_authenticate(self.user)
        self.case = TestCase.objects.create(
            title='结构用例', project=self.project, author=self.user)
        replace_steps(self.case, [
            {'action': '第一步', 'expected': '预期一'},
            {'action': '第二步', 'expected': '预期二'},
        ])

    def test_detail_returns_steps_array_only(self):
        response = self.client.get(f'/api/testcases/{self.case.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('expected_result', response.data)
        self.assertNotIn('step_details', response.data)
        self.assertIsInstance(response.data['steps'], list)
        self.assertEqual(
            [(item['sort_order'], item['action'], item['expected'])
             for item in response.data['steps']],
            [(0, '第一步', '预期一'), (1, '第二步', '预期二')],
        )
        self.assertTrue(all('id' in item for item in response.data['steps']))

    def test_list_returns_steps_array_only(self):
        response = self.client.get('/api/testcases/', {'project': self.project.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = next(item for item in response.data['results'] if item['id'] == self.case.id)
        self.assertNotIn('expected_result', row)
        self.assertNotIn('step_details', row)
        self.assertIsInstance(row['steps'], list)
        self.assertEqual([item['action'] for item in row['steps']], ['第一步', '第二步'])


class StepImportTests(APITestCase):
    """Excel 一行一条用例，两列用 # 分步骤并按顺序对齐。"""

    def setUp(self):
        self.user = User.objects.create_user(username='step_importer', password='pass123456')
        self.project = Project.objects.create(name='步骤导入项目', owner=self.user)

    def _import(self, steps_text, expected_text):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = '用例导入模板'
        worksheet.append(TestCaseImportTemplateService.HEADERS)
        worksheet.append(['', '', '', '哈希步骤用例', '中', '功能测试', '', steps_text, expected_text, '', ''])
        output = BytesIO()
        workbook.save(output)

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            record = TestCaseImportRecord.objects.create(
                import_no='IMP_STEP_HASH',
                project=self.project,
                created_by=self.user,
            )
            record.import_file.save('steps.xlsx', ContentFile(output.getvalue()), save=True)
            summary = TestCaseExcelImportService.import_record(record)
        return summary

    def test_import_pairs_steps_by_hash_order(self):
        summary = self._import('#1.第一步\n#2.第二步', '#1.预期一\n#2.预期二')
        self.assertEqual((summary.success_count, summary.failed_count), (1, 0))
        case = TestCase.objects.get(title='哈希步骤用例')
        self.assertEqual(
            list(case.step_details.values_list('sort_order', 'action', 'expected')),
            [(0, '1.第一步', '1.预期一'), (1, '2.第二步', '2.预期二')],
        )

    def test_import_pads_missing_expected_with_empty(self):
        summary = self._import('#1.第一步\n#2.第二步', '#1.预期一')
        self.assertEqual((summary.success_count, summary.failed_count), (1, 0))
        case = TestCase.objects.get(title='哈希步骤用例')
        self.assertEqual(case.step_details.count(), 2)
        self.assertEqual(case.step_details.order_by('sort_order').last().expected, '')

    def test_import_rejects_hash_only_steps(self):
        """`#` 后面没有任何内容、预期也为空时，整行没有可导入的步骤。"""
        summary = self._import('#', '#')
        self.assertEqual((summary.success_count, summary.failed_count), (0, 1))
        self.assertFalse(TestCase.objects.filter(title='哈希步骤用例').exists())

    def test_import_allows_expected_without_action(self):
        """单条步骤允许只填预期结果、不填操作描述。"""
        summary = self._import('#步骤一\n#', '#预期一\n#预期二')
        self.assertEqual((summary.success_count, summary.failed_count), (1, 0))
        case = TestCase.objects.get(title='哈希步骤用例')
        self.assertEqual(
            list(case.step_details.order_by('sort_order').values_list('action', 'expected')),
            [('步骤一', '预期一'), ('', '预期二')],
        )

    def test_import_allows_action_without_expected(self):
        """单条步骤允许只填操作描述、预期结果留空。"""
        summary = self._import('#步骤一\n#步骤二', '#预期一')
        self.assertEqual((summary.success_count, summary.failed_count), (1, 0))
        case = TestCase.objects.get(title='哈希步骤用例')
        self.assertEqual(
            list(case.step_details.order_by('sort_order').values_list('action', 'expected')),
            [('步骤一', '预期一'), ('步骤二', '')],
        )

    def test_import_rejects_over_length_step_with_row_number(self):
        """导入与 REST 共用 2000 字符上限，报错带行号与步骤序号。"""
        summary = self._import('#' + 'a' * (STEP_TEXT_MAX_LENGTH + 1), '#预期一')
        self.assertEqual((summary.success_count, summary.failed_count), (0, 1))
        errors = '；'.join(summary.failure_details[0]['errors'])
        self.assertIn('第 2 行第 1 条步骤的操作描述超过', errors)
        self.assertIn(str(STEP_TEXT_MAX_LENGTH), errors)

    def test_import_ignores_extra_expected_segments(self):
        """预期条数多于步骤时多余的忽略，不生成多余步骤、也不报错。"""
        summary = self._import('#步骤一\n#步骤二', '#预期一\n#预期二\n#预期三')
        self.assertEqual((summary.success_count, summary.failed_count), (1, 0))
        case = TestCase.objects.get(title='哈希步骤用例')
        self.assertEqual(
            list(case.step_details.order_by('sort_order').values_list('action', 'expected')),
            [('步骤一', '预期一'), ('步骤二', '预期二')],
        )


class StepParserConsistencyTests(DjangoTestCase):
    """前后端两套解析器必须对同一组样例给出一致结果。

    样例放在 frontend/tests/testcaseSteps.samples.json，前端
    frontend/tests/check-testcase-steps.mjs 读取后输出实际结果，这里与后端实现比对。
    未安装 node 时自动跳过，不影响后端测试。
    """

    samples_path = Path(settings.BASE_DIR) / 'frontend' / 'tests' / 'testcaseSteps.samples.json'
    script_path = Path(settings.BASE_DIR) / 'frontend' / 'tests' / 'check-testcase-steps.mjs'

    @classmethod
    def setUpClass(cls):
        # 先检查样例再开启 TestCase 事务，避免跳过时遗留事务影响后续迁移测试。
        if not cls.samples_path.exists():
            raise unittest.SkipTest(f'缺少样例文件: {cls.samples_path}')
        cls.samples = json.loads(cls.samples_path.read_text(encoding='utf-8'))
        super().setUpClass()

    def _run_frontend(self) -> dict:
        node = shutil.which('node')
        if not node or not self.script_path.exists():
            self.skipTest('未安装 node，跳过前后端一致性检查')
        completed = subprocess.run(
            [node, str(self.script_path)],
            capture_output=True, text=True, timeout=120,
            cwd=str(self.script_path.parent))
        if completed.returncode != 0:
            self.fail(f'前端解析脚本执行失败: {completed.stderr.strip()}')
        return json.loads(completed.stdout)

    def test_samples_are_valid_for_backend(self):
        """先确认样例本身符合后端实现，避免样例写错导致一致性检查失去意义。"""
        for sample in self.samples['parse']:
            with self.subTest(sample=sample['name']):
                self.assertEqual(
                    split_step_segments(sample['stepsText']), sample['segments'])
                self.assertEqual(
                    parse_step_pairs(sample['stepsText'], sample['expectedText']),
                    sample['pairs'])
        for sample in self.samples['format']:
            with self.subTest(sample=sample['name']):
                steps_text, expected_text = format_step_pairs(sample['steps'])
                self.assertEqual(steps_text, sample['stepsText'])
                self.assertEqual(expected_text, sample['expectedText'])

    def test_frontend_matches_backend(self):
        frontend = self._run_frontend()
        self.assertEqual(len(frontend['parse']), len(self.samples['parse']))
        self.assertEqual(len(frontend['format']), len(self.samples['format']))
        for sample, actual in zip(self.samples['parse'], frontend['parse']):
            with self.subTest(sample=sample['name']):
                self.assertEqual(actual['segments'], split_step_segments(sample['stepsText']))
                self.assertEqual(
                    actual['pairs'],
                    parse_step_pairs(sample['stepsText'], sample['expectedText']))
        for sample, actual in zip(self.samples['format'], frontend['format']):
            with self.subTest(sample=sample['name']):
                steps_text, expected_text = format_step_pairs(sample['steps'])
                self.assertEqual(actual['stepsText'], steps_text)
                self.assertEqual(actual['expectedText'], expected_text)
