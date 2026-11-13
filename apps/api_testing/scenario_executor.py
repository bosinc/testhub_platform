"""Scenario step-tree executor.

One implementation serves every entry point (page, suite, scheduled task, MCP):
it walks the ``ScenarioStep`` tree, keeps a variable scope, and records each
step result incrementally so a long scenario reports progress.

Control flow is structural: ``condition`` owns its "then" children and the
sibling ``else`` node is executed only when the paired condition was false.
Loops re-run their children with a ``loop`` frame. A ``scenario_ref`` step calls
back into this executor with an isolated variable scope but the caller's HTTP
session, so cookies carry over while outputs stay per invocation.
"""

import time
from datetime import timedelta
from uuid import UUID

import requests
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import TestExecution
from .request_executor import execute_request
from .scenario_variables import (
    MISSING,
    VariableResolutionError,
    VariableScope,
    resolve_value,
)
from .utils import execute_assertions, extract_response_variables

CONTINUE = 'continue'
BREAK_LOOP = 'break_loop'
ABORT = 'abort_scenario'

MAX_SCENARIO_DEPTH = 8
WAIT_SLICE_SECONDS = 0.1
DEFAULT_TOTAL_TIMEOUT_MS = 600000

PASSED = 'PASSED'
FAILED = 'FAILED'
ERROR = 'ERROR'
SKIPPED = 'SKIPPED'

# Types this build can actually run. ``script`` has a schema but no confirmed
# isolated runtime, so it is rejected before execution instead of being silently
# treated as a no-op.
RUNNABLE_TYPES = frozenset({'http', 'group', 'condition', 'else', 'for', 'foreach',
                            'wait', 'scenario_ref'})


class ScenarioExecutionError(Exception):
    """A step-level failure that must stop or fail the run."""


class StepFailure(Exception):
    """A step failed; the caller decides whether to continue."""

    def __init__(self, message, *, output=None):
        super().__init__(message)
        self.message = message
        self.output = output or {}


def _monotonic():
    return time.monotonic()


def _body_preview(response, limit=4000):
    text = response.text or ''
    content_type = (response.headers.get('content-type') or '').lower()
    if 'json' in content_type:
        try:
            return response.json()
        except ValueError:
            pass
    return text[:limit]


class ExecutionRecorder:
    """Writes step results and progress.

    Each step becomes one ``StepExecution`` row so the page can poll
    ``?after_sequence=`` incrementally, while ``TestExecution.results`` keeps the
    aggregate payload the current report view already reads.
    """

    def __init__(self, execution=None):
        self.execution = execution
        self.results = []
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.sequence = 0

    def add(self, result):
        self.results.append(result)
        status = result.get('status')
        if status == PASSED:
            self.passed += 1
        elif status in (FAILED, ERROR):
            self.failed += 1
        else:
            self.skipped += 1
        self.sequence += 1
        if self.execution is not None:
            self._persist_step(result)
        self.flush()

    def _persist_step(self, result):
        from .models import StepExecution

        duration = result.get('duration_ms') or 0
        finished = timezone.now()
        started = finished - timedelta(milliseconds=float(duration))
        status = result.get('status')
        if status == ERROR:
            status = FAILED
        elif status == SKIPPED and self.execution.cancel_requested:
            status = 'CANCELLED'
        step_id = result.get('step_id')
        try:
            snapshot_id = UUID(str(step_id)) if step_id else None
        except ValueError:
            snapshot_id = None
        StepExecution.objects.create(
            execution=self.execution,
            step_id_snapshot=snapshot_id,
            name_snapshot=(result.get('name') or '')[:255],
            type_snapshot=(result.get('type') or '')[:20],
            invocation_id=(result.get('invocation_id') or '')[:255],
            parent_invocation_id=(result.get('parent_invocation_id') or '')[:255],
            iteration_path=(result.get('iteration_path') or '')[:255],
            sequence=self.sequence,
            status=status,
            request_data=result.get('request') or {},
            response_data=result.get('response') or {},
            assertion_results=result.get('assertions') or [],
            extraction_results=result.get('extractions') or [],
            logs=result.get('logs') or [],
            started_at=started,
            finished_at=finished,
            error=(result.get('error') or '')[:2000],
        )

    def flush(self):
        if self.execution is None:
            return
        self.execution.results = list(self.results)
        self.execution.passed_requests = self.passed
        self.execution.failed_requests = self.failed
        self.execution.skipped_steps = self.skipped
        self.execution.total_requests = self.passed + self.failed
        self.execution.heartbeat_at = timezone.now()
        self.execution.save(update_fields=[
            'results', 'passed_requests', 'failed_requests', 'skipped_steps',
            'total_requests', 'heartbeat_at'])


class ScenarioExecutor:
    """Runs one scenario tree and returns the aggregated outcome."""

    def __init__(self, *, nodes, environment, variables=None, session=None,
                 execution_config=None, recorder=None, cancel_check=None,
                 depth=0, scenario_stack=(), scenario=None, user=None, scope=None):
        self.nodes = list(nodes)
        self.by_id = {node['id']: node for node in self.nodes}
        self.children = {}
        for node in self.nodes:
            self.children.setdefault(node.get('parent'), []).append(node)
        for group in self.children.values():
            group.sort(key=lambda item: (item.get('position', 0), item['id']))

        self.scenario = scenario
        self.user = user
        self.environment = environment or {}
        self.base_url = (self.environment.get('base_url') or '').rstrip('/')
        self.scope = scope or VariableScope(
            environment=self.environment.get('variables') or {},
            variables=variables or {},
        )
        self.session = session or requests.Session()
        self.owns_session = session is None
        self.execution_config = execution_config or {}
        self.recorder = recorder or ExecutionRecorder()
        self.cancel_check = cancel_check
        self.depth = depth
        self.scenario_stack = tuple(scenario_stack)
        # Container ids between the current call root and the running step, so a
        # nested step records which group/loop/branch it belongs to.
        self.invocation_stack = []

        timeout_ms = self.execution_config.get('total_timeout_ms') or DEFAULT_TOTAL_TIMEOUT_MS
        self.deadline = _monotonic() + timeout_ms / 1000
        self.on_error = self.execution_config.get('on_error', 'stop')
        self.aborted = False
        self.abort_reason = ''

    # -- public ------------------------------------------------------------
    def run(self):
        """Execute the tree; returns ``{'results', 'passed', 'failed', 'error'}``."""
        try:
            flow = self._run_sequence(self.children.get(None, []))
            if flow == ABORT and not self.abort_reason:
                self.abort_reason = '执行被中止'
        finally:
            if self.owns_session:
                self.session.close()
        executed = self.recorder.passed + self.recorder.failed
        error = ''
        if self.abort_reason:
            error = self.abort_reason
        elif executed == 0:
            error = '场景未包含启用的步骤，无法执行'
        return {
            'results': self.recorder.results,
            'passed': self.recorder.passed,
            'failed': self.recorder.failed,
            'total': executed,
            'error': error,
        }

    # -- guards ------------------------------------------------------------
    def _check_budget(self):
        if self.cancel_check is not None and self.cancel_check():
            self.abort_reason = '执行已取消'
            return ABORT
        if _monotonic() > self.deadline:
            self.abort_reason = '场景执行超过总时限'
            return ABORT
        return CONTINUE

    # -- sequencing --------------------------------------------------------
    def _run_children(self, node):
        """Run a container's children while tracking the invocation path."""
        self.invocation_stack.append(node['id'])
        try:
            return self._run_sequence(self.children.get(node['id'], []))
        finally:
            self.invocation_stack.pop()

    def _run_sequence(self, nodes):
        index = 0
        handled = set()
        while index < len(nodes):
            node = nodes[index]
            guard = self._check_budget()
            if guard == ABORT:
                self._record_remaining(nodes[index:], '执行已中止')
                return ABORT

            if node['id'] in handled:
                index += 1
                continue

            if not node.get('enabled', True):
                self._record(node, SKIPPED, error='步骤已禁用')
                self._record_subtree_skipped(node, '步骤已禁用')
                index += 1
                continue

            step_type = node.get('type')
            if step_type not in RUNNABLE_TYPES:
                message = f'步骤类型 {step_type!r} 当前无法执行'
                self._record(node, ERROR, error=message)
                self.abort_reason = message
                return ABORT

            if step_type == 'condition':
                flow = self._run_condition(node, nodes, handled)
            elif step_type == 'else':
                flow = self._run_branch(node, PASSED)
            else:
                flow = STEP_HANDLERS[step_type](self, node)

            if flow == ABORT:
                self._record_remaining(nodes[index + 1:], self.abort_reason or '执行已中止')
                return ABORT
            if flow == BREAK_LOOP:
                return BREAK_LOOP
            index += 1
        return CONTINUE

    def _run_condition(self, node, siblings, handled):
        """Run the "then" branch and mark the paired ``else`` skipped when true."""
        paired = self._paired_else(node, siblings)
        try:
            matched = self._evaluate_condition(node['config'])
        except (VariableResolutionError, ScenarioExecutionError) as exc:
            self._record(node, ERROR, error=str(exc))
            self._record_subtree_skipped(node, '条件表达式无效')
            if paired is not None:
                self._record(paired, SKIPPED, error='条件表达式无效')
                self._record_subtree_skipped(paired, '条件表达式无效')
                handled.add(paired['id'])
            return self._fail(str(exc))

        output = {'matched': matched}
        self._record(node, PASSED, output=output)
        if not matched:
            self._record_subtree_skipped(node, '条件不成立')
            return CONTINUE

        flow = self._run_children(node)
        if flow == ABORT:
            return ABORT
        if paired is not None:
            self._record(paired, SKIPPED, error='条件成立，跳过否则分支')
            self._record_subtree_skipped(paired, '条件成立，跳过否则分支')
            handled.add(paired['id'])
        return BREAK_LOOP if flow == BREAK_LOOP else CONTINUE

    def _paired_else(self, node, siblings):
        for candidate in siblings:
            if (candidate.get('type') == 'else'
                    and candidate.get('config', {}).get('if_step_id') == node['id']):
                return candidate
        return None

    def _run_branch(self, node, status):
        self._record(node, status)
        return self._run_children(node)

    # -- recording ---------------------------------------------------------
    def _record(self, node, status, *, output=None, error=None, started=None,
                request_info=None, response_info=None, assertions=None,
                extractions=None):
        duration = None if started is None else round((_monotonic() - started) * 1000, 2)
        path = self.scope.path + tuple(self.invocation_stack)
        result = {
            'step_id': node['id'],
            'name': node.get('name'),
            'type': node.get('type'),
            'status': status,
            'duration_ms': duration,
            'error': error,
            'request': request_info,
            'response': response_info,
            'assertions': assertions or [],
            'extractions': extractions or [],
            'output': output or {},
            'invocation_id': '/'.join(path + (node['id'],)),
            'parent_invocation_id': '/'.join(path),
            'iteration_path': '/'.join(
                str(frame.get('index')) for frame in self.scope.loop_stack),
        }
        self.recorder.add(result)
        return result

    def _record_subtree_skipped(self, node, reason):
        for child in self.children.get(node['id'], []):
            self._record(child, SKIPPED, error=reason)
            self._record_subtree_skipped(child, reason)

    def _record_remaining(self, nodes, reason):
        for node in nodes:
            self._record(node, SKIPPED, error=reason)
            self._record_subtree_skipped(node, reason)

    def _fail(self, message):
        """Apply the scenario-level on_error policy for a failed step."""
        if self.on_error == 'continue':
            return CONTINUE
        self.abort_reason = message
        return ABORT

    # -- conditions --------------------------------------------------------
    def _evaluate_condition(self, config):
        left = config.get('left')
        if isinstance(left, str):
            left = self.scope.resolve_string(left)
        operator = config.get('operator')
        right = config.get('right')

        if operator == 'exists':
            return left is not MISSING and left is not None
        if operator == 'not_exists':
            return left is MISSING or left is None
        if operator == 'is_empty':
            return left is MISSING or left is None or left == '' or left == [] or left == {}
        if operator == 'not_empty':
            return not (left is MISSING or left is None or left == '' or left == [] or left == {})
        if left is MISSING:
            raise VariableResolutionError('条件左值不存在')

        if operator == 'eq':
            return left == right
        if operator == 'ne':
            return left != right
        if operator in ('gt', 'gte', 'lt', 'lte'):
            if not isinstance(left, (int, float)) or isinstance(left, bool):
                raise VariableResolutionError('大小比较的左值必须是数字')
            return {
                'gt': left > right, 'gte': left >= right,
                'lt': left < right, 'lte': left <= right,
            }[operator]
        if operator == 'contains':
            if isinstance(left, (list, dict, str)):
                return right in left
            raise VariableResolutionError('包含比较的左值必须是字符串、数组或对象')
        if operator == 'not_contains':
            if isinstance(left, (list, dict, str)):
                return right not in left
            raise VariableResolutionError('包含比较的左值必须是字符串、数组或对象')
        raise ScenarioExecutionError(f'未知条件运算符 {operator!r}')

    # -- loops -------------------------------------------------------------
    def _run_loop_body(self, node, frame, policy):
        """Run the loop body once with ``frame`` visible as ``loop``."""
        self.scope.push_loop(frame)
        try:
            if self._loop_should_break(node):
                return BREAK_LOOP
            flow = self._run_children(node)
        finally:
            self.scope.pop_loop()
        if flow == ABORT:
            return ABORT
        if flow == BREAK_LOOP:
            if policy == 'abort_scenario':
                self.abort_reason = self.abort_reason or '循环内步骤中止了场景'
                return ABORT
            if policy == 'continue':
                return CONTINUE
            return BREAK_LOOP
        return CONTINUE

    def _loop_should_break(self, node):
        condition = node.get('config', {}).get('break_if')
        if not condition:
            return False
        try:
            return self._evaluate_condition(condition)
        except VariableResolutionError:
            return False


def _http_step(executor, node):
    started = _monotonic()
    request_dto = node.get('config', {}).get('request') or {}
    try:
        resolved = resolve_value(request_dto, executor.scope)
        sent = execute_request(
            resolved, base_url=executor.base_url, variables={}, session=executor.session)
    except VariableResolutionError as exc:
        executor._record(node, ERROR, error=str(exc), started=started)
        return executor._fail(str(exc))
    except ValidationError as exc:
        message = _validation_message(exc)
        executor._record(node, ERROR, error=message, started=started)
        return executor._fail(message)
    except requests.RequestException as exc:
        message = f'请求发送失败：{exc}'
        executor._record(node, ERROR, error=message, started=started)
        return executor._fail(message)

    response = sent['response']
    assertions = list(resolved.get('assertions') or [])
    for assertion in assertions:
        if assertion.get('type') == 'response_time':
            assertion['actual_time'] = sent['response_time']
    assertion_results = execute_assertions(response, assertions)
    extraction_results, extracted = extract_response_variables(
        response, resolved.get('extractors') or [])
    if extracted:
        executor.scope.variables.update(extracted)

    failed_assertions = [item for item in assertion_results if not item['passed']]
    failed_extractions = [item for item in extraction_results if not item['success']]
    output = {
        'status_code': response.status_code,
        'headers': dict(response.headers),
        'body': _body_preview(response),
        'text': (response.text or '')[:4000],
        'response_time': round(sent['response_time'], 2),
        'extracted': extracted,
    }
    executor.scope.record_output(node['id'], {
        'response': {'status_code': response.status_code, 'headers': dict(response.headers),
                     'body': _body_preview(response), 'text': (response.text or '')[:4000]},
        'request': sent['request'],
        'response_time': round(sent['response_time'], 2),
        'extracted': extracted,
        'passed': not failed_assertions and not failed_extractions,
    })

    errors = []
    if failed_assertions:
        errors.append('断言未通过：' + '、'.join(item['name'] for item in failed_assertions))
    if failed_extractions:
        errors.append('变量提取失败：' + '、'.join(
            item['name'] or item['expression'] for item in failed_extractions))
    error = '；'.join(errors) or None
    status = FAILED if error else PASSED
    executor._record(
        node, status, started=started, error=error,
        request_info=sent['request'], response_info=output,
        assertions=assertion_results,
        extractions=[{'name': item['name'], 'expression': item['expression'],
                      'success': item['success'], 'value': item['value'],
                      'error': item['error']} for item in extraction_results],
    )
    if error:
        return executor._fail(error)
    return CONTINUE


def _group_step(executor, node):
    started = _monotonic()
    executor._record(node, PASSED, started=started)
    return executor._run_children(node)


def _wait_step(executor, node):
    started = _monotonic()
    remaining = node['config']['duration_ms'] / 1000
    while remaining > 0:
        guard = executor._check_budget()
        if guard == ABORT:
            executor._record(node, ERROR, error=executor.abort_reason, started=started)
            return ABORT
        time.sleep(min(WAIT_SLICE_SECONDS, remaining))
        remaining -= WAIT_SLICE_SECONDS
    executor._record(node, PASSED, started=started)
    return CONTINUE


def _for_step(executor, node):
    started = _monotonic()
    config = node['config']
    count = config['count']
    if isinstance(count, str):
        try:
            count = executor.scope.resolve_string(count)
        except VariableResolutionError as exc:
            executor._record(node, ERROR, error=str(exc), started=started)
            return executor._fail(str(exc))
    try:
        count = int(count)
    except (TypeError, ValueError):
        message = 'for 循环次数不是整数'
        executor._record(node, ERROR, error=message, started=started)
        return executor._fail(message)
    count = max(0, min(count, config['max_iterations']))

    policy = config.get('on_error', 'abort_scenario')
    for index in range(count):
        guard = executor._check_budget()
        if guard == ABORT:
            executor._record(node, ERROR, error=executor.abort_reason, started=started)
            return ABORT
        flow = executor._run_loop_body(node, {'index': index, 'item': None}, policy)
        if flow == BREAK_LOOP:
            break
        if flow == ABORT:
            executor._record(node, ERROR, error=executor.abort_reason, started=started)
            return ABORT
    executor._record(node, PASSED, started=started, output={'iterations': count})
    return CONTINUE


def _foreach_step(executor, node):
    started = _monotonic()
    config = node['config']
    try:
        items = executor.scope.resolve_string(config['array_expression'])
    except VariableResolutionError as exc:
        executor._record(node, ERROR, error=str(exc), started=started)
        return executor._fail(str(exc))
    if not isinstance(items, (list, tuple)):
        message = 'ForEach 表达式必须返回数组'
        executor._record(node, ERROR, error=message, started=started)
        return executor._fail(message)

    policy = config.get('on_error', 'abort_scenario')
    limit = min(len(items), config['max_iterations'])
    for index in range(limit):
        guard = executor._check_budget()
        if guard == ABORT:
            executor._record(node, ERROR, error=executor.abort_reason, started=started)
            return ABORT
        flow = executor._run_loop_body(node, {'index': index, 'item': items[index]}, policy)
        if flow == BREAK_LOOP:
            break
        if flow == ABORT:
            executor._record(node, ERROR, error=executor.abort_reason, started=started)
            return ABORT
    executor._record(node, PASSED, started=started, output={'iterations': limit})
    return CONTINUE


def _scenario_ref_step(executor, node):
    started = _monotonic()
    target_id = node.get('source_id')
    if executor.depth + 1 > MAX_SCENARIO_DEPTH:
        message = '场景引用层级过深'
        executor._record(node, ERROR, error=message, started=started)
        return executor._fail(message)
    if str(target_id) in executor.scenario_stack:
        message = '场景引用出现循环'
        executor._record(node, ERROR, error=message, started=started)
        return executor._fail(message)

    try:
        target = _load_referenced_scenario(executor, target_id)
    except (NotFound, PermissionDenied) as exc:
        message = f'引用的场景不可用：{exc.detail if hasattr(exc, "detail") else exc}'
        executor._record(node, ERROR, error=message, started=started)
        return executor._fail(message)

    config = node.get('config') or {}
    child_variables = dict(target.variables or {})
    try:
        for name, expression in (config.get('input_mapping') or {}).items():
            value = expression
            if isinstance(expression, str):
                value = executor.scope.resolve_string(expression)
            child_variables[name] = value
    except VariableResolutionError as exc:
        executor._record(node, ERROR, error=str(exc), started=started)
        return executor._fail(str(exc))

    child_scope = executor.scope.spawn(node['id'], variables=child_variables)
    child = ScenarioExecutor(
        nodes=_load_definition(target),
        environment=executor.environment,
        variables=child_variables,
        session=executor.session,
        execution_config=target.execution_config or {},
        recorder=executor.recorder,
        cancel_check=executor.cancel_check,
        depth=executor.depth + 1,
        scenario_stack=executor.scenario_stack + (str(target.pk),),
        scenario=target,
        user=executor.user,
        scope=child_scope,
    )

    outcome = child.run()
    if outcome['failed'] or outcome['error']:
        message = outcome['error'] or '引用场景执行失败'
        executor._record(node, FAILED, error=message, started=started,
                         output={'failed': outcome['failed']})
        return executor._fail(message)

    try:
        for name, expression in (config.get('output_mapping') or {}).items():
            executor.scope.variables[name] = child.scope.resolve_string(expression)
    except VariableResolutionError as exc:
        executor._record(node, ERROR, error=str(exc), started=started)
        return executor._fail(str(exc))
    executor._record(node, PASSED, started=started, output={'passed': outcome['passed']})
    return CONTINUE


STEP_HANDLERS = {
    'http': _http_step,
    'group': _group_step,
    'wait': _wait_step,
    'for': _for_step,
    'foreach': _foreach_step,
    'scenario_ref': _scenario_ref_step,
}


def _validation_message(exc):
    detail = getattr(exc, 'detail', None)
    if isinstance(detail, dict):
        for value in detail.values():
            if isinstance(value, dict):
                return _validation_message_from_dict(value)
            if isinstance(value, (list, tuple)) and value:
                return str(value[0])
        return str(detail)
    return str(detail or exc)


def _validation_message_from_dict(value):
    for item in value.values():
        if isinstance(item, (list, tuple)) and item:
            return str(item[0])
        if isinstance(item, str):
            return item
    return str(value)


def _load_definition(scenario):
    from .scenario_service import load_definition

    return load_definition(scenario)


def _load_referenced_scenario(executor, target_id):
    from .scenario_service import resolve_source

    scenario = resolve_source(executor.scenario, 'scenario_case', target_id,
                              user=executor.user)
    if scenario is None:
        raise NotFound('引用的场景不存在或没有访问权限')
    return scenario


def cancel_requested(execution_id):
    """Read the cancellation flag written by the cancel endpoint."""
    return TestExecution.objects.filter(
        pk=execution_id, cancel_requested=True).exists()


def execute_scenario_tree(scenario, environment, user, execution=None, cancel_check=None):
    """Run a scenario tree and persist a ``TestExecution`` record.

    Returns the same shape as ``utils.execute_scenario_case`` so suite, task and
    MCP callers do not need a separate branch.
    """
    from .environment_resolver import EnvironmentResolver

    if execution is None:
        execution = TestExecution.objects.create(
            scenario_case=scenario, status='RUNNING',
            start_time=timezone.now(), executed_by=user)
    else:
        execution.status = 'RUNNING'
        execution.start_time = execution.start_time or timezone.now()
        execution.end_time = None
        execution.conclusion = ''
        execution.results = []
        execution.passed_requests = 0
        execution.failed_requests = 0
        execution.skipped_steps = 0
        execution.total_requests = 0
        execution.save()

    try:
        snapshot = EnvironmentResolver.snapshot(
            environment, user=user, project_id=scenario.project_id)
    except ValidationError as exc:
        return _finish(execution, [], message=_validation_message(exc))

    # The run snapshot is what the execution is judged against; later edits to
    # the environment do not retroactively change a finished report.
    execution.environment_snapshot = {
        'id': snapshot['id'], 'base_url': snapshot['base_url'],
        'variables': sorted(snapshot['variables'] or {}),
    }
    execution.heartbeat_at = timezone.now()
    execution.save(update_fields=['environment_snapshot', 'heartbeat_at'])

    nodes = _load_definition(scenario)
    unsupported = sorted({
        node.get('type') for node in nodes
        if node.get('type') not in RUNNABLE_TYPES
    })
    if unsupported:
        return _finish(execution, [], message=(
            '以下步骤类型当前无法执行：' + '、'.join(str(item) for item in unsupported)))

    recorder = ExecutionRecorder(execution)
    executor = ScenarioExecutor(
        nodes=nodes,
        environment=snapshot,
        variables=dict(scenario.variables or {}),
        execution_config=scenario.execution_config or {},
        recorder=recorder,
        cancel_check=cancel_check or (lambda: cancel_requested(execution.pk)),
        scenario_stack=(str(scenario.pk),),
        scenario=scenario,
        user=user,
    )
    try:
        outcome = executor.run()
    except Exception as exc:  # noqa: BLE001 - a crash must still close the record
        return _finish(execution, recorder.results, message=f'场景执行异常：{exc}')

    return _finish(execution, recorder.results, outcome=outcome)


def _finish(execution, results, *, outcome=None, message=''):
    execution.refresh_from_db(fields=['cancel_requested'])
    execution.end_time = timezone.now()
    execution.results = list(results)
    cancelled = execution.cancel_requested
    if outcome is None:
        execution.status = 'CANCELLED' if cancelled else FAILED
        execution.conclusion = 'CANCELLED' if cancelled else FAILED
        execution.results = list(results) + [{'status': ERROR, 'error': message}]
        execution.passed_requests = 0
        execution.failed_requests = 0
        execution.total_requests = 0
    else:
        execution.passed_requests = outcome['passed']
        execution.failed_requests = outcome['failed']
        execution.total_requests = outcome['total']
        message = outcome['error']
        if cancelled:
            # A cancelled run finished, but it is not a green test.
            execution.status = 'CANCELLED'
            execution.conclusion = 'CANCELLED'
        elif outcome['failed'] == 0 and outcome['total']:
            execution.status = 'COMPLETED'
            execution.conclusion = 'PASSED'
        else:
            execution.status = 'COMPLETED'
            execution.conclusion = FAILED
            message = message or '存在失败的步骤'
    execution.save()
    return {
        'success': execution.conclusion == 'PASSED',
        'execution_id': execution.id,
        'status': execution.status,
        'conclusion': execution.conclusion,
        'passed_count': execution.passed_requests,
        'failed_count': execution.failed_requests,
        'total_count': execution.total_requests,
        'results': execution.results,
        'error': message,
    }
