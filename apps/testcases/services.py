from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from io import BytesIO
from typing import Dict, List, Optional

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction, models
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from apps.users.models import User
from apps.projects.models import Project
from apps.versions.models import Version

from .models import TestCase, TestCaseModule, TestCaseImportRecord
from .steps import (normalize_steps, parse_step_pairs, replace_steps,
                    validate_step_payload)


TEMPLATE_VERSION = 'v1'
# 生成模板时给第一个工作表的名称；导入时不再校验名称，只读取第一个工作表。
TEMPLATE_SHEET_NAME = '用例导入模板'
INSTRUCTION_SHEET_NAME = '填写说明'


@dataclass
class ImportSummary:
    total_rows: int
    success_count: int
    failed_count: int
    skip_count: int
    failure_details: List[dict]


class TestCaseImportTemplateService:
    HEADERS = [
        *[f'{level}级模块' for level in ('一', '二', '三')],
        '用例标题', '优先级', '测试类型', '前置条件', '操作步骤', '预期结果', '备注', '维护人',
    ]

    SAMPLE_ROW = [
        *([''] * 3), '用户登录成功校验', '高', '功能测试', '用户已注册且账号状态正常',
        '#打开登录页\n#输入正确用户名和密码\n#点击登录按钮',
        '#登录页正常展示\n#用户名和密码输入成功\n#页面跳转到首页，并显示登录成功信息', '', '',
    ]

    INSTRUCTIONS = [
        ['模板版本', TEMPLATE_VERSION],
        ['说明', '请勿修改现有表头名称，按模板字段填写并上传；默认提供三级模块列。如需更深层级，可按“4级模块”至“8级模块”自行新增列，并连续填写父级路径'],
        ['工作表', '导入时只读取 Excel 的第一个工作表，工作表名称不限；请把用例数据放在第一个工作表，其他工作表会被忽略'],
        ['默认处理', '模板中未提供的字段将使用系统默认值：描述为空、状态=active(激活)、标签=[]、指派人为空'],
        ['用例标题', '必填，测试用例标题，建议唯一且明确'],
        ['前置条件', '可选'],
        ['操作步骤', '必填。一行即一条用例；以 # 开头的一行表示一条步骤，一个 # 对应一条步骤，例如：#打开登录页。'
                     '不以 # 开头的行是上一条步骤的续行（用于多行描述），行中间的 #（如 URL 里的 #/login）不会被当作分隔符；'
                     '正文某一行本身以 # 开头时写成 \\#，以 \\ 开头时写成 \\\\；每条步骤不超过 2000 字符。'
                     '单条步骤的操作描述与预期结果可以只填一项'],
        ['预期结果', '同样以 # 开头表示一条预期结果，与“操作步骤”按 # 出现的先后顺序一一对应；'
                     '数量少于步骤时，多出的步骤预期结果按空值处理；数量多于步骤时，多出的预期结果直接忽略'],
        ['一级模块至三级模块', '可选，逐级填写模块名称；全部留空时用例归入无所属模块。需要更深层级时，可自行新增“4级模块”至“8级模块”列，不能跳级填写'],
        ['创建时间、更新时间', '如需导入原始时间，可在导入表格中自行添加“创建时间”和“更新时间”列；未添加或留空时由系统按现有逻辑自动设置'],
        ['优先级', '可选，支持中文或英文：低/中/高/紧急 或 low/medium/high/critical；默认 medium(中)'],
        ['测试类型', '可选，支持中文或英文：功能测试/集成测试/API测试/UI测试/性能测试/安全测试 或 functional/integration/api/ui/performance/security；默认 functional(功能测试)'],
        ['中英文示例', '优先级示例：高 或 high；测试类型示例：功能测试 或 functional'],
        ['维护人', '可选，填写项目成员的用户名或邮箱；留空时使用导入操作人'],
    ]

    @classmethod
    def build_template(cls) -> BytesIO:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = TEMPLATE_SHEET_NAME
        worksheet.append(cls.HEADERS)
        worksheet.append(cls.SAMPLE_ROW)

        header_fill = PatternFill(fill_type='solid', fgColor='D9EAF7')
        required_fill = PatternFill(fill_type='solid', fgColor='FDE2E2')
        header_font = Font(bold=True)

        for index, header in enumerate(cls.HEADERS, start=1):
            cell = worksheet.cell(row=1, column=index)
            cell.font = header_font
            cell.fill = required_fill if header.endswith('*') else header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

        for row in worksheet.iter_rows(min_row=2, max_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)

        column_widths = [18] * 3 + [28, 14, 18, 34, 40, 34, 30, 24]
        for idx, width in enumerate(column_widths, start=1):
            worksheet.column_dimensions[chr(64 + idx)].width = width

        instruction_sheet = workbook.create_sheet(INSTRUCTION_SHEET_NAME)
        for row in cls.INSTRUCTIONS:
            instruction_sheet.append(row)
        instruction_sheet.column_dimensions['A'].width = 22
        instruction_sheet.column_dimensions['B'].width = 80
        for row in instruction_sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        for cell in instruction_sheet[1]:
            cell.font = header_font
            cell.fill = header_fill

        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return output


class TestCaseExcelImportService:
    PRIORITY_MAP = {
        'low': 'low', '低': 'low',
        'medium': 'medium', '中': 'medium',
        'high': 'high', '高': 'high',
        'critical': 'critical', '紧急': 'critical',
    }
    TEST_TYPE_MAP = {
        'functional': 'functional', '功能测试': 'functional',
        'integration': 'integration', '集成测试': 'integration',
        'api': 'api', 'API测试': 'api',
        'ui': 'ui', 'UI测试': 'ui',
        'performance': 'performance', '性能测试': 'performance',
        'security': 'security', '安全测试': 'security',
    }

    # 键为去掉空白后的表头文本；标题列与步骤列只接受模板新表头，不兼容任何旧写法。
    HEADER_ALIASES = {
        '用例标题': 'title',
        '前置条件': 'preconditions',
        '操作步骤': 'steps',
        '预期结果': 'expected',
        '预期结果*': 'expected',
        '备注': 'notes',
        '优先级': 'priority',
        '测试类型': 'test_type',
        '关联项目': 'project',
        '项目': 'project',
        '维护人': 'maintainer',
        '创建时间': 'created_at',
        '更新时间': 'updated_at',
        **{f'{level}级模块': f'module_{index}' for index, level in enumerate(('一', '二', '三', '四', '五', '六', '七', '八'), start=1)},
    }
    EXPECTED_HEADER_FIELDS = [
        'title',
        'priority',
        'test_type',
        'preconditions',
        'steps',
        'expected',
        'notes',
        'maintainer',
    ]

    @classmethod
    def import_record(cls, record: TestCaseImportRecord) -> ImportSummary:
        workbook = load_workbook(record.import_file.path)
        # 不限制工作表名称：始终读取第一个工作表，其余工作表（如填写说明）忽略。
        if not workbook.worksheets:
            raise ValueError('Excel 中没有工作表')
        worksheet = workbook.worksheets[0]
        raw_headers = [cls._normalize_header(cell.value) for cell in next(worksheet.iter_rows(min_row=1, max_row=1))]
        header_entries = [
            (column_index, cls.HEADER_ALIASES.get(header))
            for column_index, header in enumerate(raw_headers)
            if header
        ]
        header_fields = [field for _, field in header_entries]
        populated_fields = [field for field in header_fields if field]
        base_fields = [field for field in header_fields if not (field or '').startswith('module_')]
        module_fields = [field for field in header_fields if (field or '').startswith('module_')]
        timestamp_fields = [field for field in base_fields if field in {'created_at', 'updated_at'}]
        required_base_fields = [field for field in base_fields if field not in {'created_at', 'updated_at'}]
        expected_module_fields = [f'module_{i}' for i in range(1, len(module_fields) + 1)]
        if (
            any(field is None for _, field in header_entries)
            or set(required_base_fields) != set(cls.EXPECTED_HEADER_FIELDS)
            or len(required_base_fields) != len(cls.EXPECTED_HEADER_FIELDS)
            or len(timestamp_fields) != len(set(timestamp_fields))
            or len(populated_fields) != len(set(populated_fields))
            or set(module_fields) != set(expected_module_fields)
            or len(module_fields) != len(expected_module_fields)
        ):
            raise ValueError('导入模板表头不匹配，请先下载最新模板后再导入')

        rows = list(worksheet.iter_rows(min_row=2, values_only=True))
        effective_rows = [row for row in rows if any(cls._normalize_text(value) for value in row)]
        total_rows = len(effective_rows)
        success_count = 0
        failed_count = 0
        skip_count = 0
        failure_details: List[dict] = []

        accessible_projects = Project.objects.filter(
            models.Q(owner=record.created_by) | models.Q(members=record.created_by)
        ).distinct()
        projects_by_name = {}
        ambiguous_project_names = set()
        for project in accessible_projects:
            if project.name in projects_by_name:
                ambiguous_project_names.add(project.name)
            else:
                projects_by_name[project.name] = project

        for index, row in enumerate(effective_rows, start=2):
            row_data = {
                field: cls._normalize_text(row[column_index] if column_index < len(row) else None)
                for column_index, field in header_entries
                if field
            }

            project_name = row_data.get('project')
            project = projects_by_name.get(project_name) if project_name else record.project
            error_messages = []
            if project_name in ambiguous_project_names:
                error_messages.append(f'项目名称不唯一，请先确保可访问项目名称唯一: {project_name}')
            if project is None:
                error_messages.append(f'项目不存在或无权访问: {project_name}')
            version_map = {
                version.name: version
                for version in Version.objects.filter(projects=project).distinct()
            } if project else {}
            error_messages.extend(cls._validate_row(row_data, version_map, row_number=index))
            imported_timestamps = {}
            for field, label in (('created_at', '创建时间'), ('updated_at', '更新时间')):
                value = row_data.get(field)
                if value:
                    try:
                        imported_timestamps[field] = cls._parse_import_datetime(value)
                    except ValueError:
                        error_messages.append(f'{label}格式无效: {value}')
            modules = [row_data.get(f'module_{level}', '') for level in range(1, 9)]
            if any(modules[level] and not modules[level - 1] for level in range(1, 8)):
                error_messages.append('模块层级不能跳级填写')
            maintainer = cls._get_project_maintainer(project, row_data.get('maintainer')) if project else None
            if row_data.get('maintainer') and maintainer is None:
                error_messages.append(f"维护人不是项目成员或用户名/邮箱不存在: {row_data['maintainer']}")
            if error_messages:
                failed_count += 1
                failure_details.append({
                    'row_number': index,
                    'title': row_data.get('title', ''),
                    'errors': error_messages,
                })
                continue

            version_names = cls._split_csv(row_data.get('versions'))

            with transaction.atomic():
                module = cls._get_or_create_module_path(project, modules)
                testcase = TestCase.objects.create(
                    project=project,
                    module=module,
                    title=row_data['title'],
                    description='',
                    notes=row_data.get('notes', ''),
                    preconditions=row_data.get('preconditions', ''),
                    priority=cls.PRIORITY_MAP.get(row_data.get('priority', '').lower(), 'medium'),
                    test_type=cls.TEST_TYPE_MAP.get(row_data.get('test_type', '').lower(), 'functional'),
                    tags=[],
                    author=maintainer or record.created_by,
                    assignee=None,
                )
                replace_steps(testcase, parse_step_pairs(
                    row_data.get('steps'), row_data.get('expected')))
                if imported_timestamps:
                    # auto_now overrides updated_at during save, so preserve imported values with a direct update.
                    TestCase.objects.filter(pk=testcase.pk).update(**imported_timestamps)
                if version_names:
                    testcase.versions.set([version_map[name].id for name in version_names])

            success_count += 1
            record.progress = int(success_count / total_rows * 100) if total_rows else 100
            record.success_count = success_count
            record.failed_count = failed_count
            record.total_rows = total_rows
            record.save(update_fields=['progress', 'success_count', 'failed_count', 'total_rows', 'updated_at'])

        return ImportSummary(
            total_rows=total_rows,
            success_count=success_count,
            failed_count=failed_count,
            skip_count=skip_count,
            failure_details=failure_details,
        )

    @classmethod
    def build_failure_report(cls, record: TestCaseImportRecord) -> Optional[ContentFile]:
        if not record.failure_details:
            return None

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = '失败明细'
        worksheet.append(['行号', '标题', '失败原因'])
        for item in record.failure_details:
            worksheet.append([
                item.get('row_number'),
                item.get('title', ''),
                '；'.join(item.get('errors', [])),
            ])

        for column in ('A', 'B', 'C'):
            worksheet.column_dimensions[column].width = 20 if column != 'C' else 80

        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return ContentFile(output.read(), name=f'{record.import_no}_failed_rows.xlsx')

    @classmethod
    def generate_import_no(cls) -> str:
        return f'IMP_{timezone.now().strftime("%Y%m%d%H%M%S")}_{uuid.uuid4().hex[:6].upper()}'

    @classmethod
    def _validate_row(cls, row_data: Dict[str, str], version_map: Dict[str, Version],
                      row_number: Optional[int] = None) -> List[str]:
        errors = []
        if not row_data.get('title'):
            errors.append('标题不能为空')
        # 步骤校验与 REST / MCP 共用同一套规则（长度上限、id 唯一），
        # 单条步骤的操作描述或预期结果可以为空，整行全空才算未填写。
        step_pairs = normalize_steps(parse_step_pairs(
            row_data.get('steps'), row_data.get('expected')))
        if not step_pairs:
            errors.append('至少填写一条步骤（操作描述与预期结果至少填一项）')
        else:
            prefix = f'第 {row_number} 行' if row_number else ''
            errors.extend(f'{prefix}{message}' for message in validate_step_payload(step_pairs))

        priority = row_data.get('priority', '')
        if priority and priority.lower() not in cls.PRIORITY_MAP and priority not in cls.PRIORITY_MAP:
            errors.append(f'优先级无效: {priority}')

        test_type = row_data.get('test_type', '')
        if test_type and test_type.lower() not in cls.TEST_TYPE_MAP and test_type not in cls.TEST_TYPE_MAP:
            errors.append(f'测试类型无效: {test_type}')

        version_names = cls._split_csv(row_data.get('versions'))
        invalid_versions = [name for name in version_names if name not in version_map]
        if invalid_versions:
            errors.append(f'版本不存在: {", ".join(invalid_versions)}')

        return errors

    @staticmethod
    def _get_project_maintainer(project: Project, identity: str) -> Optional[User]:
        if not identity:
            return None
        member = project.members.filter(
            models.Q(username__iexact=identity) | models.Q(email__iexact=identity)
        ).first()
        if member:
            return member
        owner = project.owner
        if owner.username.lower() == identity.lower() or (owner.email and owner.email.lower() == identity.lower()):
            return owner
        return None

    @staticmethod
    def _get_or_create_module_path(project: Project, names: List[str]) -> Optional[TestCaseModule]:
        """复用或逐级创建导入文件中的模块路径。"""
        parent = None
        for name in names:
            if not name:
                break
            module = TestCaseModule.objects.filter(project=project, parent=parent, name=name).first()
            if module is None:
                module, _ = TestCaseModule.objects.get_or_create(
                    project=project,
                    parent=parent,
                    name=name,
                    defaults={'sort_order': TestCaseModule.next_sort_order(project, parent)},
                )
            parent = module
        return parent

    @staticmethod
    def _split_csv(text: str) -> List[str]:
        if not text:
            return []
        return [item.strip() for item in str(text).split(',') if item and item.strip()]

    @staticmethod
    def _normalize_text(value) -> str:
        if value is None:
            return ''
        return str(value).strip()

    @staticmethod
    def _normalize_header(value) -> str:
        text = TestCaseExcelImportService._normalize_text(value)
        if not text:
            return ''
        text = text.replace('\ufeff', '').replace('\u200b', '')
        return re.sub(r'\s+', '', text)

    @staticmethod
    def _parse_import_datetime(value: str) -> datetime:
        """Parse Excel date values and common text date formats into the active timezone."""
        text = str(value).strip()
        parsed = parse_datetime(text)
        if parsed is None:
            parsed_date = parse_date(text)
            if parsed_date is not None:
                parsed = datetime.combine(parsed_date, time.min)
        if parsed is None:
            for date_format in ('%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M', '%Y/%m/%d'):
                try:
                    parsed = datetime.strptime(text, date_format)
                    break
                except ValueError:
                    continue
        if parsed is None:
            raise ValueError('Unsupported date value')
        if settings.USE_TZ and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
