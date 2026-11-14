"""Validation and normalization for locally recorded Playwright scripts."""

import ast
import copy
import hashlib
import os
import re
from typing import Any


MAX_SCRIPT_BYTES = 2 * 1024 * 1024

ALLOWED_IMPORTS = {'os', 're', 'playwright'}
FORBIDDEN_IMPORTS = {
    'asyncio', 'ctypes', 'http', 'httpx', 'importlib', 'pathlib', 'requests',
    'socket', 'subprocess', 'sys', 'urllib',
}
FORBIDDEN_CALLS = {
    '__import__', 'compile', 'eval', 'exec', 'open',
    'getattr', 'setattr', 'delattr', 'globals', 'locals', 'vars',
}
LOCATOR_METHODS = {
    'get_by_alt_text', 'get_by_label', 'get_by_placeholder', 'get_by_role',
    'get_by_test_id', 'get_by_text', 'get_by_title', 'locator', 'frame_locator',
}


class ScriptSecurityError(ValueError):
    """Raised when a script is invalid or outside the supported safe subset."""


class _CodegenNormalizer(ast.NodeTransformer):
    """Normalize codegen scripts and add conservative replay adaptations."""

    def __init__(self):
        self.changed = False
        self.optimizations: list[dict[str, str | int]] = []

    def _record(self, category: str, node: ast.AST, action: str):
        self.optimizations.append({
            'category': category,
            'line': getattr(node, 'lineno', 0),
            'action': action,
        })

    @staticmethod
    def _env_value(name: str, default: str, *, boolean=False) -> ast.expr:
        if boolean:
            source = f"os.environ.get({name!r}, {default!r}) != '0'"
        else:
            source = f"os.environ.get({name!r}) or {default!r}"
        return ast.parse(source, mode='eval').body

    def visit_Call(self, node):  # noqa: N802 - ast API name
        node = self.generic_visit(node)
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr == 'launch':
            for keyword in node.keywords:
                if keyword.arg == 'headless' and isinstance(keyword.value, ast.Constant):
                    keyword.value = self._env_value('HEADLESS', '1' if keyword.value.value else '0', boolean=True)
                    self.changed = True
        if isinstance(function, ast.Attribute) and function.attr == 'goto' and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                node.args[0] = self._env_value('BASE_URL', first.value)
                self.changed = True
        if isinstance(function, ast.Attribute) and function.attr == 'click' and self._is_toggle_target(function.value):
            node = ast.Call(
                func=ast.Name(id='_testhub_toggle_click', ctx=ast.Load()),
                args=[function.value],
                keywords=node.keywords,
            )
            self.changed = True
            self._record(
                'toggle_state', function,
                '已改为运行时读取 aria-expanded/checked 状态后再点击，避免重复翻转已展开控件。',
            )
        if isinstance(function, ast.Attribute) and any(keyword.arg in {'path', 'storage_state'} for keyword in node.keywords):
            node = self._rewrite_path_keyword(node)
        return node

    @staticmethod
    def _is_toggle_target(target: ast.AST) -> bool:
        text = ast.unparse(target).lower()
        return any(term in text for term in ('toggle', 'expand', 'collapse', 'accordion', 'menu', '展开', '折叠', '收起', '菜单'))

    def _rewrite_path_keyword(self, node: ast.Call) -> ast.Call:
        for keyword in node.keywords:
            if keyword.arg not in {'path', 'storage_state'} or not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, str):
                continue
            value = keyword.value.value
            if re.match(r'^(?:/(?:Users|home|private|tmp|var)/|[A-Za-z]:\\)', value):
                name = os.path.basename(value) or 'artifact'
                keyword.value = ast.parse(
                    f"os.path.join(os.environ.get('WORKDIR') or '.', {name!r})",
                    mode='eval',
                ).body
                self.changed = True
                self._record('environment_coupling', keyword, f'已将本机绝对路径改写为运行目录下的相对文件 {name}。')
        return node


class _SecurityVisitor(ast.NodeVisitor):
    def __init__(self):
        self.errors: list[str] = []
        self.locator_count = 0
        self.urls: list[str] = []

    def visit_Import(self, node):  # noqa: N802 - ast API name
        for alias in node.names:
            self._check_import(alias.name, node.lineno)
            if alias.name.split('.')[0] == 'os' and alias.asname:
                self.errors.append(f'第 {node.lineno} 行不允许为 os 使用别名')
        self.generic_visit(node)

    def visit_ImportFrom(self, node):  # noqa: N802 - ast API name
        self._check_import(node.module or '', node.lineno)
        if (node.module or '').split('.')[0] == 'os':
            self.errors.append(f'第 {node.lineno} 行不允许从 os 导入成员')
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa: N802 - ast API name
        name = ''
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in LOCATOR_METHODS:
                self.locator_count += 1
            if name == 'goto' and node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    self.urls.append(node.args[0].value)
        if name in FORBIDDEN_CALLS:
            self.errors.append(f'第 {node.lineno} 行禁止调用 {name}')
        self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa: N802 - ast API name
        if node.attr in {'system', 'popen', 'spawn', 'fork'}:
            self.errors.append(f'第 {node.lineno} 行禁止访问 {node.attr}')
        if isinstance(node.value, ast.Name) and node.value.id == 'os' and node.attr not in {'environ', 'getenv'}:
            self.errors.append(f'第 {node.lineno} 行禁止访问 os.{node.attr}')
        self.generic_visit(node)

    def _check_import(self, module: str, lineno: int):
        root = module.split('.')[0]
        if root in FORBIDDEN_IMPORTS or root not in ALLOWED_IMPORTS:
            self.errors.append(f'第 {lineno} 行不允许导入 {module}')


def _decode_script(content: str) -> bytes:
    try:
        encoded = content.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ScriptSecurityError('脚本必须使用UTF-8编码') from exc
    if len(encoded) > MAX_SCRIPT_BYTES:
        raise ScriptSecurityError('脚本文件不能超过2MB')
    return encoded


def analyze_script(content: str) -> dict[str, Any]:
    """Parse, validate and normalize a Python Playwright codegen script."""
    encoded = _decode_script(content)
    try:
        tree = ast.parse(content, mode='exec')
    except SyntaxError as exc:
        raise ScriptSecurityError(f'Python语法错误：第{exc.lineno}行 {exc.msg}') from exc

    visitor = _SecurityVisitor()
    visitor.visit(tree)
    if visitor.errors:
        raise ScriptSecurityError('；'.join(visitor.errors[:8]))

    normalizer = _CodegenNormalizer()
    normalized_tree = normalizer.visit(copy.deepcopy(tree))
    ast.fix_missing_locations(normalized_tree)
    normalized = ast.unparse(normalized_tree)
    if 'import os' not in normalized.splitlines()[:5]:
        normalized = 'import os\n' + normalized

    start_url = visitor.urls[0] if visitor.urls else ''
    diagnostics = _diagnostics(tree)
    warnings = list(dict.fromkeys([*_warnings(tree), *(item['message'] for item in diagnostics)]))
    return {
        'language': 'python',
        'framework': 'playwright',
        'locator_count': visitor.locator_count,
        'start_url': start_url,
        'warnings': warnings,
        'diagnostics': diagnostics,
        'optimizations': normalizer.optimizations,
        'content_hash': hashlib.sha256(encoded).hexdigest(),
        'normalized_content': normalized,
    }


def _warnings(tree: ast.AST) -> list[str]:
    warnings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == 'get_by_text':
                warnings.append('发现文本定位器，页面文案变化可能导致回放失败')
            if node.func.attr == 'locator' and node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str) and ':has-text' in node.args[0].value:
                    warnings.append('发现依赖文本的CSS定位器，建议在编辑器中确认唯一性')
    return list(dict.fromkeys(warnings))[:10]


def _diagnostics(tree: ast.AST) -> list[dict[str, str | int]]:
    nodes = list(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    calls = [node for node in nodes if isinstance(node, ast.Call)]
    diagnostics = []
    seen = set()

    def add(category: str, node: ast.AST, message: str, recommendation: str):
        key = (category, getattr(node, 'lineno', 0), message)
        if key not in seen:
            seen.add(key)
            diagnostics.append({
                'category': category,
                'line': getattr(node, 'lineno', 0),
                'message': message,
                'recommendation': recommendation,
            })

    def call_name(call: ast.Call) -> str:
        return call.func.attr if isinstance(call.func, ast.Attribute) else ''

    def explicitly_narrowed(call: ast.Call) -> bool:
        parent = parents.get(call)
        return (isinstance(parent, ast.Attribute) and parent.attr == 'first') or (
            isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == 'nth'
        )

    for call in calls:
        name = call_name(call)
        if name == 'get_by_role':
            role = call.args[0].value.lower() if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str) else ''
            accessible_name = next((kw.value for kw in call.keywords if kw.arg == 'name'), None)
            if role in {'button', 'link', 'menuitem', 'tab', 'checkbox', 'radio'} and not explicitly_narrowed(call) and (
                accessible_name is None or isinstance(accessible_name, ast.Constant) and not str(accessible_name.value).strip()
            ):
                add(
                    'locator_ambiguity', call,
                    f'第{call.lineno}行使用未限定名称的 {role or "role"} 定位器，可能匹配多个元素。',
                    '补充 name/语义条件；确需多个匹配时显式使用 .first 或 .nth(index)。',
                )
        elif name == 'get_by_text' and call.args and isinstance(call.args[0], ast.Constant):
            if isinstance(call.args[0].value, str) and not call.args[0].value.strip():
                add(
                    'locator_ambiguity', call,
                    f'第{call.lineno}行使用空文本定位器，容易匹配到多个图标按钮或空节点。',
                    '改用可访问名称、测试ID或稳定 CSS 定位；避免用空文本定位。',
                )
        elif name == 'locator' and call.args and isinstance(call.args[0], ast.Constant):
            selector = call.args[0].value
            if isinstance(selector, str) and (selector.strip() == '' or re.search(r'(?:^|\s)text\s*=\s*(?:$|["\'])', selector)):
                add(
                    'locator_ambiguity', call,
                    f'第{call.lineno}行使用空文本或空白 CSS 文本定位器。',
                    '换成语义定位或稳定属性定位，并确认唯一匹配。',
                )

    navigation_lines = [call.lineno for call in calls if call_name(call) in {'goto', 'reload'}]
    has_wait_after_navigation = any(
        call_name(call) in {'wait_for', 'wait_for_selector', 'wait_for_load_state', 'wait_for_url'}
        and any(call.lineno > line for line in navigation_lines)
        for call in calls
    )
    has_navigation = bool(navigation_lines)
    has_interaction = any(call_name(call) in {'click', 'fill', 'press', 'check', 'uncheck'} for call in calls)
    if has_navigation and has_interaction and not has_wait_after_navigation:
        first_navigation = next(call for call in calls if call_name(call) in {'goto', 'reload'})
        add(
            'wait_state', first_navigation,
            '脚本有页面跳转和后续交互，但没有显式页面状态等待。',
            '关键跳转后检查 wait_for_load_state、wait_for_url 或目标元素等待；Playwright 定位器本身带自动等待。',
        )

    toggle_terms = ('toggle', 'expand', 'collapse', 'accordion', 'menu', '展开', '折叠', '收起', '菜单')
    for call in calls:
        if call_name(call) != 'click' or not isinstance(call.func, ast.Attribute):
            continue
        target = ast.unparse(call.func.value).lower()
        if any(term in target for term in toggle_terms):
            add(
                'toggle_state', call,
                f'第{call.lineno}行点击疑似 toggle/菜单控件，录制与回放时的初始状态可能不同。',
                '先读取 aria-expanded/is_visible/is_checked 等状态，仅在目标未展开或不可见时切换。',
            )

    absolute_path = re.compile(r'(?:(?:^|["\'\s=])/(?:Users|home|private|tmp|var)/[^\s"\']+|\b[A-Za-z]:\\[^\s"\']+)')
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and absolute_path.search(node.value):
            add(
                'environment_coupling', node,
                f'第{getattr(node, "lineno", 0)}行包含本机绝对路径，换机器或 CI 可能失效。',
                '改用项目相对路径或运行配置；确认 storage_state、截图和下载文件由执行环境提供。',
            )
    for call in calls:
        if any(kw.arg in {'storage_state', 'path'} for kw in call.keywords):
            if not any(
                diag['category'] == 'environment_coupling' and diag['line'] == call.lineno
                for diag in diagnostics
            ):
                add(
                    'environment_coupling', call,
                    f'第{call.lineno}行使用文件路径或 storage_state，需确认目标文件在回放环境存在。',
                    '将依赖文件放入项目并使用相对路径，或在目标环境预置登录态数据。',
                )
    return diagnostics[:30]

