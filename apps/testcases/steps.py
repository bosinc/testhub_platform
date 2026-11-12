"""测试用例步骤工具：Excel 文本与结构化步骤互转、统一校验、增量保存。

Excel 约定：一行对应一条用例，「操作步骤」「预期结果」两列用行首 `#` 标记步骤，
一个行首 `#` 对应一条步骤，两列按 `#` 出现的先后顺序对齐，并以步骤条数为准：
预期少于步骤时补空（例如步骤 4 条、预期 3 条，则第 4 条预期为空），

分隔只看**行首**：行中间的 `#`（例如 URL 里的 `#/login`）属于正文，不会切段；
带缩进的 `  #正文` 同样属于正文。一个行首 `#` 到下一个行首 `#` 之间的所有行
（含换行）都属于同一条步骤。`#` 之后的内容原样保留（包括 `1.` / `1、` 这类序号）。

转义：续行本身以 `#` 开头时导出为 `\\#`，以 `\\` 开头时导出为 `\\\\`；
导入时按同样规则还原，保证「导出 → 再导入」内容完全不变。
"""
from __future__ import annotations

import re
from typing import Iterable, List

from django.db import transaction

STEP_SEPARATOR = '#'
ESCAPE_CHAR = '\\'
ESCAPED_SEPARATOR = ESCAPE_CHAR + STEP_SEPARATOR
ESCAPED_ESCAPE = ESCAPE_CHAR * 2
BR_PATTERN = re.compile(r'<br\s*/?>', re.IGNORECASE)

# 步骤文本长度上限：REST、MCP、Excel 导入、AI 采纳统一使用该规则。
STEP_TEXT_MAX_LENGTH = 2000


def _to_lines(text) -> str:
    """统一换行：`<br>` / `\\r\\n` / `\\r` 一律归一化为 `\\n`。"""
    normalized = str(text).replace('\r\n', '\n').replace('\r', '\n')
    return BR_PATTERN.sub('\n', normalized)


def _is_escaped_line(line: str) -> bool:
    """行首是转义前缀（`\\\\` 或 `\\#`）时，该行是正文而不是分隔符。"""
    return line.startswith(ESCAPED_ESCAPE) or line.startswith(ESCAPED_SEPARATOR)


def _unescape_line(line: str) -> str:
    """还原行首转义：`\\\\` → `\\`，`\\#` → `#`。"""
    return line[1:] if _is_escaped_line(line) else line


def _escape_line(line: str) -> str:
    """转义行首：`#` → `\\#`，`\\` → `\\\\`；其余原样。"""
    if line.startswith(ESCAPE_CHAR) or line.startswith(STEP_SEPARATOR):
        return ESCAPE_CHAR + line
    return line


def split_step_segments(text) -> List[str]:
    """按行首 `#` 切段；整段没有行首 `#` 时整块作为一条步骤。

    只认**行首第一个字符**是 `#`：带缩进的 `  #正文` 属于续行，不会被当成
    分隔符（代码块、缩进列表里的 `#` 因此不会被误切）。行中间的 `#` 同理。
    行首的 `\\#` / `\\\\` 是转义前缀，按正文还原。
    """
    if text is None:
        return []
    normalized = _to_lines(text)
    if not normalized.strip():
        return []

    # 只去行尾空白，保留行首缩进 —— 缩进是判断“是不是分隔符”的依据。
    lines = [line.rstrip() for line in normalized.split('\n')]
    if not any(line.startswith(STEP_SEPARATOR) for line in lines):
        # 没有任何一行以 `#` 开头：整块内容就是一条步骤，保留内部换行。
        return [normalized.strip()]

    segments: List[str] = []
    current = None
    for line in lines:
        if _is_escaped_line(line):
            body = _unescape_line(line)
            current = body if current is None else f'{current}\n{body}'
        elif line.startswith(STEP_SEPARATOR):
            if current is not None:
                segments.append(current.strip())
            current = line[1:]
        elif current is not None:
            # 续行：属于上一条步骤，`#` 出现在中间也不会断段。
            current = f'{current}\n{line}' if current else line
    if current is not None:
        segments.append(current.strip())
    return segments


def parse_step_pairs(steps_text, expected_text) -> List[dict]:
    """把两列文本解析为步骤数组，按行首 `#` 顺序一一对齐。

    以「操作步骤」的条数为准：步骤少于预期时，多出来的预期直接忽略（不报警、
    也不生成空操作步骤 —— 空操作步骤在编辑页无法保存）；预期少于步骤时补空。
    """
    step_segments = split_step_segments(steps_text)
    expected_segments = split_step_segments(expected_text)
    return [
        {
            'action': action,
            'expected': expected_segments[index] if index < len(expected_segments) else '',
        }
        for index, action in enumerate(step_segments)
    ]


def _format_segment(value) -> str:
    """把一条步骤渲染成 `#` 开头的多行文本。

    首行直接跟在 `#` 后（若首行本身以 `#` 开头，结果是 `##...`，导入时能原样还原）；
    续行按 `_escape_line` 转义，避免被当成分隔符或误还原。
    """
    text = '' if value is None else _to_lines(value)
    lines = text.split('\n')
    parts = [f'{STEP_SEPARATOR}{lines[0]}']
    parts.extend(_escape_line(line) for line in lines[1:])
    return '\n'.join(parts)


def format_step_pairs(steps: Iterable[dict]) -> tuple[str, str]:
    """把步骤数组格式化为两列文本，用于 Excel 导出。

    不在 `#` 后追加序号，且对续行做行首转义，保证「导出 → 再导入」内容不变。
    """
    rows = list(steps or [])
    step_lines = [_format_segment(item.get('action', '')) for item in rows]
    expected_lines = [_format_segment(item.get('expected', '')) for item in rows]
    return '\n'.join(step_lines), '\n'.join(expected_lines)


def normalize_steps(steps) -> List[dict]:
    """清洗步骤数组：保留 id / sort_order，只丢弃整行全空的占位行。

    单条步骤的操作描述与预期结果都允许为空（只填一侧也是合法步骤），
    只有「action 与 expected 都为空」的行才视为未填写的占位行丢弃。
    """
    if not steps:
        return []
    if not isinstance(steps, (list, tuple)):
        raise ValueError('steps 必须是数组')
    normalized = []
    for item in steps:
        if not isinstance(item, dict):
            raise ValueError('每个步骤必须是对象')
        action = str(item.get('action', '') or '').strip()
        expected = str(item.get('expected', '') or '').strip()
        if not action and not expected:
            continue
        normalized.append({
            'id': item.get('id') or None,
            'sort_order': item.get('sort_order'),
            'action': action,
            'expected': expected,
        })
    return normalized


def coerce_steps_payload(steps, expected_text='') -> List[dict]:
    """兼容两种入参：结构化数组直接用，文本则按 `#` 解析后与预期列对齐。"""
    if isinstance(steps, (list, tuple)):
        return normalize_steps(steps)
    return parse_step_pairs(steps, expected_text)


def duplicate_step_ids(steps) -> List[int]:
    """返回步骤数组里重复出现的 id（按首次出现顺序去重）。"""
    seen, duplicates = set(), []
    for item in steps or []:
        if not isinstance(item, dict):
            continue
        step_id = item.get('id')
        if not step_id:
            continue
        if step_id in seen and step_id not in duplicates:
            duplicates.append(step_id)
        seen.add(step_id)
    return duplicates


def validate_step_payload(steps, *, allowed_ids=None) -> List[str]:
    """统一校验步骤数组，返回错误信息列表（空列表表示通过）。

    REST、MCP、Excel 导入、AI 采纳共用这一套规则：两列长度不超过
    `STEP_TEXT_MAX_LENGTH`、id 唯一且属于当前用例。单条步骤的操作描述
    与预期结果都允许为空（只填一侧也是合法步骤），不做必填校验。
    """
    errors: List[str] = []
    seen_ids = set()
    for index, item in enumerate(steps or [], 1):
        if not isinstance(item, dict):
            errors.append(f'第 {index} 条步骤格式不正确')
            continue
        action = str(item.get('action', '') or '').strip()
        expected = str(item.get('expected', '') or '').strip()
        if len(action) > STEP_TEXT_MAX_LENGTH:
            errors.append(
                f'第 {index} 条步骤的操作描述超过 {STEP_TEXT_MAX_LENGTH} 字符'
                f'（当前 {len(action)}）')
        if len(expected) > STEP_TEXT_MAX_LENGTH:
            errors.append(
                f'第 {index} 条步骤的预期结果超过 {STEP_TEXT_MAX_LENGTH} 字符'
                f'（当前 {len(expected)}）')
        step_id = item.get('id')
        if not step_id:
            continue
        if step_id in seen_ids:
            errors.append(f'步骤 id {step_id} 重复，同一条步骤不能提交两次')
        seen_ids.add(step_id)
        if allowed_ids is not None and step_id not in allowed_ids:
            errors.append(f'步骤 {step_id} 不属于当前用例')
    return errors


def sync_steps(testcase, steps) -> dict:
    """按主键增量同步步骤：带 id 的更新、不带 id 的新建、未提交的删除。

    先校验再落库；内容未变化的步骤不写库，新增/更新分别批量提交。
    返回 {'created': n, 'updated': n, 'deleted': n}，便于接口与 MCP 回显。
    """
    from .models import TestCaseStep

    prepared = normalize_steps(steps)
    existing = {step.id: step for step in testcase.step_details.all()}
    errors = validate_step_payload(prepared, allowed_ids=set(existing))
    if errors:
        raise ValueError('；'.join(errors))

    to_create: List[TestCaseStep] = []
    to_update: List[TestCaseStep] = []
    kept_ids = set()
    with transaction.atomic():
        for index, item in enumerate(prepared):
            sort_order = item['sort_order'] if item['sort_order'] is not None else index
            step = existing.get(item['id']) if item['id'] else None
            if step is None:
                to_create.append(TestCaseStep(
                    testcase=testcase,
                    sort_order=sort_order,
                    action=item['action'],
                    expected=item['expected'],
                ))
                continue
            kept_ids.add(step.id)
            if (step.sort_order, step.action, step.expected) == (
                    sort_order, item['action'], item['expected']):
                continue
            step.sort_order = sort_order
            step.action = item['action']
            step.expected = item['expected']
            to_update.append(step)
        if to_create:
            TestCaseStep.objects.bulk_create(to_create)
            kept_ids.update(pk for pk in (step.id for step in to_create) if pk is not None)
        if to_update:
            TestCaseStep.objects.bulk_update(
                to_update, ['sort_order', 'action', 'expected'], batch_size=500)
        stale_ids = [step_id for step_id in existing if step_id not in kept_ids]
        if stale_ids:
            deleted, _ = TestCaseStep.objects.filter(
                testcase=testcase, id__in=stale_ids).delete()
        else:
            deleted = 0
    return {'created': len(to_create), 'updated': len(to_update), 'deleted': deleted}


def replace_steps(testcase, steps) -> int:
    """整体替换（导入、AI 采纳等无 id 场景）：等价于全量新建。"""
    return sync_steps(testcase, steps)['created']
