"""Translate the common Playwright codegen subset to editable UI steps."""

import ast
import io
import os
import tokenize

from .playwright_engine import playwright_locator_expression


# Playwright 方法到结构化步骤动作的映射，前端只处理统一的 action_type。
ACTION_MAP = {
    'click': 'click', 'fill': 'fill', 'hover': 'hover', 'press': 'press',
    'select_option': 'select', 'check': 'check', 'uncheck': 'uncheck',
    'inner_text': 'getText', 'text_content': 'getText',
    'goto': 'navigate', 'screenshot': 'screenshot', 'wait_for_timeout': 'wait',
    'wait_for': 'waitFor',
}
# Playwright 定位器工厂到元素库定位策略的映射。
LOCATOR_MAP = {
    'get_by_role': 'role', 'get_by_label': 'label',
    'get_by_placeholder': 'placeholder', 'get_by_text': 'text',
    'get_by_test_id': 'test-id', 'get_by_title': 'title',
    'get_by_alt_text': 'alt-text', 'locator': 'css',
}


class UnsupportedRecordingCode(ValueError):
    def __init__(self, line, message):
        self.line = line
        super().__init__(f'第{line}行无法转换为结构化步骤：{message}')


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.unparse(node)


def _locator(node):
    """从 AST 调用链提取定位策略、定位值和附加选项。"""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        name = node.func.attr
        if name in LOCATOR_MAP:
            strategy = LOCATOR_MAP[name]
            value = _literal(node.args[0]) if node.args else ''
            options = {kw.arg: _literal(kw.value) for kw in node.keywords if kw.arg}
            return {'strategy': strategy, 'value': str(value), 'options': options}
        parent = _locator(node.func.value)
        if parent and name == 'nth':
            parent['options']['nth'] = _literal(node.args[0]) if node.args else 0
            return parent
    if isinstance(node, ast.Attribute) and node.attr == 'first':
        parent = _locator(node.value)
        if parent:
            parent['options']['first'] = True
        return parent
    return None


def _statements(nodes):
    for node in nodes:
        if isinstance(node, (ast.With, ast.AsyncWith, ast.Try)):
            yield from _statements(node.body)
            for handler in getattr(node, 'handlers', []):
                yield from _statements(handler.body)
            yield from _statements(getattr(node, 'finalbody', []))
        else:
            yield node


def _step_comments(content):
    """读取代码行注释，供反解析结果生成可读步骤描述。"""
    comments = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                value = token.string[1:].strip()
                if value and not value.startswith('testhub-step:'):
                    comments[token.start[0]] = value
    except (tokenize.TokenError, IndentationError):
        return comments
    return comments


def _description_for(statement, comments, action, locator, value='', wait_time=1000):
    comment = comments.get(statement.lineno) or comments.get(statement.lineno - 1)
    if comment:
        return comment
    if action == 'click':
        verb = '点击'
    elif action == 'fill':
        verb = '输入文本'
    elif action == 'press':
        verb = '按键'
    elif action == 'navigate':
        verb = '跳转页面'
    elif action in {'assert', 'waitFor'}:
        verb = '断言' if action == 'assert' else '等待元素'
    elif action == 'wait':
        verb = '等待'
    elif action == 'hover':
        verb = '悬停'
    elif action == 'select':
        verb = '选择选项'
    elif action == 'check':
        verb = '勾选'
    elif action == 'uncheck':
        verb = '取消勾选'
    elif action == 'getText':
        verb = '获取文本'
    elif action == 'screenshot':
        verb = '截图'
    elif action == 'storageState':
        verb = '保存登录态'
    else:
        verb = action
    target = ''
    if locator:
        options = locator.get('options') or {}
        target = str(options.get('name') or options.get('label') or options.get('placeholder') or locator.get('value') or '')
        target = f'“{target}”' if target else ''
    if not locator:
        if action == 'wait':
            return f'等待{wait_time}ms'
        if action == 'navigate' and value:
            return f'跳转URL“{value}”'
        if action == 'switchTab' and value != '':
            return f'切换到标签页“{value}”'
        if action == 'press' and value:
            return f'{verb}“{value}”'
    return f'{verb}{target}'


def parse_recording(content):
    """将受支持的 Playwright Python 语句转换为编辑器步骤。"""
    try:
        tree = ast.parse(content, mode='exec')
    except SyntaxError as exc:
        raise UnsupportedRecordingCode(exc.lineno or 1, exc.msg) from exc

    steps = []
    comments = _step_comments(content)
    function_nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not function_nodes:
        function_nodes = [tree]
    for node in function_nodes:
            for statement in _statements(node.body):
                if isinstance(statement, (ast.Import, ast.ImportFrom, ast.Pass)):
                    continue
                if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    assigned = statement.value if isinstance(statement, ast.Assign) else statement.value
                    if isinstance(assigned, ast.Call) and isinstance(assigned.func, ast.Attribute) and assigned.func.attr in {'launch', 'new_context', 'new_page'}:
                        continue
                    if (isinstance(assigned, ast.Subscript) and isinstance(assigned.value, ast.Attribute)
                            and assigned.value.attr == 'pages'):
                        value = str(_literal(assigned.slice))
                        steps.append({
                            'action_type': 'switchTab', 'input_value': value, 'wait_time': 1000,
                            'description': _description_for(statement, comments, 'switchTab', None, value),
                            'action_params': {},
                            'source_line_start': statement.lineno,
                            'source_line_end': getattr(statement, 'end_lineno', statement.lineno),
                            'raw_code': ast.unparse(statement),
                        })
                        continue
                    raise UnsupportedRecordingCode(getattr(statement, 'lineno', 1), ast.unparse(statement))
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                    call = statement.value
                    method = call.func.attr if isinstance(call.func, ast.Attribute) else ''
                    if method in {'to_be_visible', 'to_be_hidden', 'to_have_attribute', 'to_have_text', 'to_contain_text'} and isinstance(call.func.value, ast.Call):
                        expectation = call.func.value
                        target = expectation.args[0] if expectation.args else None
                        locator = _locator(target)
                        if not locator:
                            raise UnsupportedRecordingCode(statement.lineno, ast.unparse(statement))
                        assert_value = str(_literal(call.args[0])) if call.args else ''
                        if method == 'to_have_attribute':
                            # 仅将生成器的任意值正则识别为属性存在，其他正则无法用精确值表达。
                            if not call.args or len(call.args) > 2 or '=' in assert_value:
                                raise UnsupportedRecordingCode(statement.lineno, ast.unparse(statement))
                            if len(call.args) == 2:
                                expected = call.args[1]
                                presence_pattern = (
                                    isinstance(expected, ast.Call)
                                    and ast.unparse(expected) == "re.compile('(?s).*')"
                                )
                                if not presence_pattern:
                                    if not isinstance(expected, ast.Constant) or not isinstance(expected.value, str):
                                        raise UnsupportedRecordingCode(statement.lineno, ast.unparse(statement))
                                    assert_value += '=' + expected.value
                        steps.append({
                            'action_type': 'assert', 'input_value': '', 'wait_time': 1000,
                            'assert_type': {'to_be_visible': 'isVisible', 'to_be_hidden': 'isNotVisible', 'to_have_attribute': 'hasAttribute', 'to_have_text': 'textEquals', 'to_contain_text': 'textContains'}[method],
                            'assert_value': assert_value,
                            'description': _description_for(statement, comments, 'assert', locator),
                            'action_params': {'locator': locator},
                            'source_line_start': statement.lineno,
                            'source_line_end': getattr(statement, 'end_lineno', statement.lineno),
                            'raw_code': ast.unparse(statement),
                        })
                        continue
                    if method not in ACTION_MAP:
                        if method == 'storage_state':
                            path = next(
                                (_literal(keyword.value) for keyword in call.keywords if keyword.arg == 'path'),
                                'storage_state.json',
                            )
                            name = os.path.splitext(os.path.basename(str(path)))[0] or 'storage_state'
                            steps.append({
                                'action_type': 'storageState',
                                'input_value': '',
                                'wait_time': 1000,
                                'description': _description_for(statement, comments, 'storageState', None),
                                'action_params': {
                                    'operation': 'save',
                                    'mode': 'save_as',
                                    'name': name,
                                    'storage_state_id': None,
                                },
                                'source_line_start': statement.lineno,
                                'source_line_end': getattr(statement, 'end_lineno', statement.lineno),
                                'raw_code': ast.unparse(statement),
                            })
                            continue
                        if method in {'launch', 'new_context', 'new_page', 'close', 'stop'}:
                            continue
                        raise UnsupportedRecordingCode(statement.lineno, ast.unparse(statement))
                    action = ACTION_MAP[method]
                    target = call.func.value if isinstance(call.func, ast.Attribute) else None
                    locator = _locator(target) if target else None
                    if action not in {'navigate', 'screenshot', 'wait'} and not locator:
                        raise UnsupportedRecordingCode(statement.lineno, '无法识别目标定位器')
                    value = _literal(call.args[0]) if call.args else ''
                    params = {'locator': locator} if locator else {}
                    wait_time = 1000
                    if method == 'wait_for_timeout':
                        params['wait_time'] = value
                        wait_time = int(value) if str(value).isdigit() else 1000
                        value = ''
                    if method == 'select_option':
                        params['select_value'] = value
                    steps.append({
                        'action_type': action,
                        'input_value': str(value) if action in {'fill', 'navigate', 'press'} else '',
                        'wait_time': wait_time,
                        'description': _description_for(statement, comments, action, locator, value, wait_time),
                        'action_params': params,
                        'source_line_start': statement.lineno,
                        'source_line_end': getattr(statement, 'end_lineno', statement.lineno),
                        'raw_code': ast.unparse(statement),
                    })
                    continue
                raise UnsupportedRecordingCode(getattr(statement, 'lineno', 1), ast.unparse(statement))
    # 前端按当前界面语言生成默认文案；代码注释属于用户内容，必须保留原文。
    for step in steps:
        line = step['source_line_start']
        step['description_generated'] = not (line in comments or line - 1 in comments)
    return steps


def _py_literal(value):
    return repr(value)


def _locator_code(locator):
    return playwright_locator_expression(
        locator.get('strategy', 'css'), locator.get('value', ''), locator.get('options'),
    )


def _storage_state_name(params):
    """将手动用例的登录态名称转换成脚本可用的相对 JSON 文件名。"""
    name = str((params or {}).get('name') or '').strip()
    safe_name = os.path.basename(name).strip() or 'storage_state'
    return safe_name if safe_name.endswith('.json') else f'{safe_name}.json'


def generate_code(steps, start_url=''):
    """将结构化步骤生成可再次被 AST 反解析的 Playwright Python 代码。"""
    lines = [
        'from playwright.sync_api import sync_playwright, expect', 'import re', '',
        'def run(playwright):',
        '    browser = playwright.chromium.launch(headless=False)',
        '    context = browser.new_context(ignore_https_errors=True)',
        '    page = context.new_page()',
    ]
    if start_url and not any(step.get('action_type') == 'navigate' for step in steps):
        lines.append(f'    page.goto({_py_literal(start_url)})')
    for index, step in enumerate(steps, 1):
        action = step.get('action_type')
        locator = (step.get('action_params') or {}).get('locator')
        target = _locator_code(locator) if locator else 'page'
        description = str(step.get('description') or '').strip()
        lines.append(f'    # {description}' if description else '    #')
        if action == 'navigate':
            lines.append(f'    page.goto({_py_literal(step.get("input_value", ""))})')
        elif action in {'click', 'hover', 'check', 'uncheck'}:
            lines.append(f'    {target}.{action}()')
        elif action == 'fill':
            lines.append(f'    {target}.fill({_py_literal(step.get("input_value", ""))})')
        elif action == 'press':
            lines.append(f'    {target}.press({_py_literal(step.get("input_value", ""))})')
        elif action == 'select':
            value = (step.get('action_params') or {}).get('select_value', step.get('input_value', ''))
            lines.append(f'    {target}.select_option({_py_literal(value)})')
        elif action == 'wait':
            lines.append(f'    page.wait_for_timeout({step.get("wait_time", 1000)})')
        elif action == 'waitFor':
            lines.append(f'    {target}.wait_for()')
        elif action == 'scroll':
            lines.append(f'    {target}.scroll_into_view_if_needed()')
        elif action == 'getText':
            lines.append(f'    {target}.inner_text()')
        elif action == 'switchTab':
            page_index = step.get('input_value') or '0'
            lines.append(f'    page = context.pages[{page_index}]')
        elif action == 'screenshot':
            lines.append('    page.screenshot()')
        elif action == 'storageState':
            params = step.get('action_params') or {}
            operation = params.get('operation')
            if operation == 'save':
                # 参数结构与手动用例的 storageState 动作保持一致；文件只作为脚本回放的落点。
                lines.append(f'    context.storage_state(path={_py_literal(_storage_state_name(params))})')
            elif operation == 'switch':
                lines.extend([
                    '    context.close()',
                    f'    context = browser.new_context(ignore_https_errors=True, storage_state={_py_literal(_storage_state_name(params))})',
                    '    page = context.new_page()',
                ])
            elif operation == 'clear':
                lines.extend([
                    '    context.close()',
                    '    context = browser.new_context(ignore_https_errors=True)',
                    '    page = context.new_page()',
                ])
            else:
                lines.append('    # 登录态动作参数无效')
        elif action == 'assert':
            assert_type = step.get('assert_type')
            if assert_type == 'isVisible':
                lines.append(f'    expect({target}).to_be_visible()')
            elif assert_type == 'isNotVisible':
                lines.append(f'    expect({target}).to_be_hidden()')
            elif assert_type == 'hasAttribute':
                # 用任意值正则检查属性存在，空字符串属性同样通过。
                attr_name, separator, expected_value = str(step.get('assert_value') or '').strip().partition('=')
                attr_name = attr_name.strip()
                if not attr_name:
                    raise ValueError('hasAttribute 断言需要指定属性名')
                expected = _py_literal(expected_value) if separator else "re.compile('(?s).*')"
                lines.append(f'    expect({target}).to_have_attribute({_py_literal(attr_name)}, {expected})')
            elif assert_type in {'textContains', 'textEquals'}:
                matcher = 'to_contain_text' if assert_type == 'textContains' else 'to_have_text'
                lines.append(f'    expect({target}).{matcher}({_py_literal(step.get("assert_value", ""))})')
            else:
                lines.append(f'    # Manual assertion required: {step.get("description", "assert")}')
        else:
            raw_code = step.get('raw_code')
            if raw_code:
                lines.append('    ' + raw_code.replace('\n', '\n    '))
            else:
                lines.append(f'    # Unsupported action: {action}')
    lines.extend(['    context.close()', '    browser.close()', '', '', 'with sync_playwright() as playwright:', '    run(playwright)', ''])
    return '\n'.join(lines)
