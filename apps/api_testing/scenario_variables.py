"""Namespaced variable resolution for scenario step execution.

The plan replaces the legacy flat ``{{name}}`` lookup with explicit namespaces::

    {{env.tenant_id}}                           environment variables
    {{vars.token}}                              scenario run variables
    {{steps.<step_uuid>.response.body.data}}    a previous step output
    {{loop.item.id}}                            current ForEach element
    {{loop.index}}                              current loop index

A placeholder that occupies a whole string keeps its native JSON type, so a
ForEach expression can return a real list. Embedded placeholders are rendered as
text. Missing values raise instead of silently resolving to an empty string: a
typo must fail the step, not produce a request against a wrong URL.

``${...}`` dynamic functions are still handled downstream by
``apps.api_testing.variable_resolver``.
"""

import json
import re

MISSING = object()

PLACEHOLDER = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')
FULL_PLACEHOLDER = re.compile(r'^\s*\{\{\s*([^{}]+?)\s*\}\}\s*$')
SEGMENT = re.compile(r'([^.\[\]]+)|\[(\d+)\]')

NAMESPACES = ('env', 'vars', 'steps', 'loop')


class VariableResolutionError(ValueError):
    """Raised when a placeholder cannot be resolved inside the current scope."""


def navigate(value, path):
    """Walk ``a.b[0].c`` through dicts and lists, returning ``MISSING`` on gaps."""
    if not path:
        return value
    for key, index in SEGMENT.findall(path):
        if index:
            position = int(index)
            if not isinstance(value, list) or position >= len(value):
                return MISSING
            value = value[position]
            continue
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def _as_text(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return '' if value is None else str(value)


class VariableScope:
    """Mutable resolution scope shared by every step of one run.

    ``path`` identifies the current call path (empty for the scenario being
    executed, ``('<ref_step_uuid>',)`` inside a referenced scenario). Step
    outputs are stored per call path so a referenced scenario invoked twice, or
    a loop body run on several iterations, cannot read a neighbour's output.
    """

    def __init__(self, *, environment=None, variables=None, path=()):
        self.environment = dict(environment or {})
        self.variables = dict(variables or {})
        self.outputs = {}
        self.path = tuple(path)
        self.loop_stack = []

    # -- scope bookkeeping -------------------------------------------------
    def push_loop(self, frame):
        self.loop_stack.append(frame)

    def pop_loop(self):
        self.loop_stack.pop()

    def spawn(self, invocation_id, *, variables=None):
        """Return a scope one call level deeper.

        Step outputs are always shared, so a referenced scenario can still be
        addressed from its caller; ``variables`` starts a fresh run-variable set
        when given, which is what a scenario reference needs.
        """
        scope = VariableScope.__new__(VariableScope)
        scope.environment = self.environment
        scope.variables = self.variables if variables is None else dict(variables)
        scope.outputs = self.outputs
        scope.path = self.path + (str(invocation_id),)
        scope.loop_stack = list(self.loop_stack)
        return scope

    def record_output(self, step_id, output):
        self.outputs['/'.join(self.path + (str(step_id),))] = output

    def _output_paths(self, step_id):
        """Search the current call path first, then its ancestors."""
        return [
            '/'.join(self.path[:depth] + (str(step_id),))
            for depth in range(len(self.path), -1, -1)
        ]

    # -- lookup ------------------------------------------------------------
    def lookup(self, expression):
        expression = (expression or '').strip()
        if not expression:
            return MISSING
        head, _, rest = expression.partition('.')
        if head == 'env':
            return navigate(self.environment, rest)
        if head == 'vars':
            return navigate(self.variables, rest)
        if head == 'steps':
            step_id, _, path = rest.partition('.')
            for candidate in self._output_paths(step_id):
                if candidate in self.outputs:
                    return navigate(self.outputs[candidate], path)
            return MISSING
        if head == 'loop':
            if not self.loop_stack:
                return MISSING
            return navigate(self.loop_stack[-1], rest)
        # Legacy bare ``{{name}}``: environment first, then run variables. Kept
        # only for data that predates the migration; new references must be
        # namespaced.
        value = navigate(self.environment, expression)
        if value is MISSING:
            value = navigate(self.variables, expression)
        return value

    def require(self, expression):
        value = self.lookup(expression)
        if value is MISSING:
            raise VariableResolutionError(f'变量 {{{{ {expression} }}}} 不存在或不可用')
        return value

    # -- rendering ---------------------------------------------------------
    def resolve_value(self, value):
        """Recursively resolve a JSON value; whole placeholders keep their type."""
        if isinstance(value, str):
            return self.resolve_string(value)
        if isinstance(value, list):
            return [self.resolve_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.resolve_value(item) for key, item in value.items()}
        return value

    def resolve_string(self, text):
        whole = FULL_PLACEHOLDER.match(text)
        if whole:
            return self.require(whole.group(1))

        def replace(match):
            return _as_text(self.require(match.group(1)))

        return PLACEHOLDER.sub(replace, text)


def resolve_value(value, scope):
    return scope.resolve_value(value)


def resolve_text(text, scope):
    return scope.resolve_string(text)


def expression_names(expression):
    """Return the placeholder paths inside a template, for validation helpers."""
    return [match.group(1) for match in PLACEHOLDER.finditer(expression or '')]
