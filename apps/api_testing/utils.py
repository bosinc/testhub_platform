import json
import logging
import subprocess
import sys
from urllib3.util.retry import Retry
from django.utils import timezone
from rest_framework.exceptions import APIException
from .models import RequestHistory
from .environment_resolver import EnvironmentResolver
from .request_dto import request_from_model

logger = logging.getLogger(__name__)


def _api_request_retry(method):
    """Retry short-lived connection failures only for idempotent HTTP methods."""
    retry_methods = frozenset({'GET', 'HEAD', 'OPTIONS'})
    retry_count = 2 if str(method).upper() in retry_methods else 0
    return Retry(
        total=retry_count,
        connect=retry_count,
        read=0,
        status=0,
        backoff_factor=0.2,
        allowed_methods=retry_methods,
        raise_on_status=False,
    )


def normalize_scenario_case_assertions(assertions, response_time=None):
    """兼容场景用例中的 value 字段，并将断言转换为统一格式。"""
    normalized = []
    for assertion in assertions or []:
        item = dict(assertion)
        if 'expected' not in item and 'value' in item:
            item['expected'] = item['value']
        if item.get('type') == 'response_time' and response_time is not None:
            item['actual_time'] = response_time
        normalized.append(item)
    return normalized


def extract_response_variables(response, extractors):
    """从 JSON 响应中按接口配置提取变量，并返回逐条结果和变量映射。"""
    results = []
    extracted = {}
    response_json = None
    response_json_loaded = False

    for extractor in extractors or []:
        if not isinstance(extractor, dict) or extractor.get('enabled') is False:
            continue

        name = str(extractor.get('name') or '').strip()
        expression = str(extractor.get('expression') or '').strip()
        result = {'name': name, 'expression': expression, 'success': False, 'value': None, 'error': ''}
        try:
            if not name:
                raise ValueError('变量名不能为空')
            if not expression:
                raise ValueError('JSONPath 表达式不能为空')
            if not response_json_loaded:
                response_json = json.loads(response.text or '')
                response_json_loaded = True

            from jsonpath_ng import parse
            matches = parse(expression).find(response_json)
            if matches:
                value = matches[0].value
            elif extractor.get('default_value') not in (None, ''):
                value = extractor.get('default_value')
            else:
                raise ValueError('未匹配到响应数据，且未设置默认值')

            result.update(success=True, value=value)
            extracted[name] = value
        except Exception as error:
            result['error'] = str(error)
        results.append(result)

    return results, extracted


# JSONPath 断言支持的操作符。断言规则里 ``operator`` 缺省时按 ``equals`` 处理，
# 因此历史数据（只有 type + json_path + expected）行为完全不变。
JSON_ASSERTION_OPERATORS = (
    'equals', 'not_equals',
    'exists', 'not_exists',
    'is_not_empty', 'is_empty',
    'is_string', 'is_number', 'is_boolean', 'is_array', 'is_object',
    'includes', 'not_includes',
    'contains', 'not_contains',
    'at_least', 'greater_than', 'less_than', 'close_to',
    'length', 'matches', 'one_of',
)


def _assertion_to_str(value):
    """字符串化比较，语义对齐 Apifox / JS ``String(x)``。

    与 Python ``str()`` 的差异会影响断言结论，必须归一：
    ``True`` -> ``'true'``、``None`` -> ``'null'``、``2.0`` -> ``'2'``。
    """
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _assertion_is_empty(value):
    """空值判定：None / '' / [] / {} 视为空；0 与 False 视为有值。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value == ''
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


# 包含解释器启动、正则编译和匹配的总预算；不能由用户断言覆盖。
REGEX_ASSERTION_TIMEOUT_SECONDS = 0.5
_REGEX_MATCH_WORKER = """
import json
import re
import sys

pattern, text = json.load(sys.stdin)
try:
    result = {'passed': re.search(pattern, text) is not None, 'error': None}
except re.error as error:
    result = {'passed': False, 'error': f'正则表达式无效: {error}'}
json.dump(result, sys.stdout, ensure_ascii=True)
"""


def _match_assertion_regex(expected, actual):
    """隔离用户正则，超时后 kill 并回收子进程，不阻塞后续断言。

    保留标准库 re 的匹配语义；不用线程/Future 超时，因为无法终止回溯。
    -I 防止子进程导入项目/Django，也可在任务工作进程及非主线程中使用。
    """
    try:
        completed = subprocess.run(
            [sys.executable, '-I', '-c', _REGEX_MATCH_WORKER],
            input=json.dumps([str(expected), _assertion_to_str(actual)]),
            capture_output=True,
            text=True,
            timeout=REGEX_ASSERTION_TIMEOUT_SECONDS,
            check=True,
        )
        result = json.loads(completed.stdout)
        return result['passed'], result['error']
    except subprocess.TimeoutExpired:
        # subprocess.run 已在抛出异常前强制终止并等待子进程退出。
        return False, f'正则匹配超时（上限 {REGEX_ASSERTION_TIMEOUT_SECONDS:g} 秒）'
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        return False, f'正则匹配执行失败: {type(error).__name__}'


def _apply_json_operator(operator, actual, hits, expected, assertion):
    """按 operator 判定 JSONPath 断言是否通过；返回 (passed, error)。"""
    operator = (operator or 'equals').strip().lower()

    if operator == 'equals':
        return _assertion_to_str(actual) == _assertion_to_str(expected), None
    if operator == 'not_equals':
        return _assertion_to_str(actual) != _assertion_to_str(expected), None
    if operator == 'exists':
        return actual is not None, None
    if operator == 'not_exists':
        return actual is None, None
    if operator == 'is_not_empty':
        return not _assertion_is_empty(actual), None
    if operator == 'is_empty':
        return _assertion_is_empty(actual), None
    if operator == 'is_string':
        return isinstance(actual, str), None
    if operator == 'is_number':
        return isinstance(actual, (int, float)) and not isinstance(actual, bool), None
    if operator == 'is_boolean':
        return isinstance(actual, bool), None
    if operator == 'is_array':
        return isinstance(actual, list), None
    if operator == 'is_object':
        return isinstance(actual, dict), None
    if operator in ('includes', 'not_includes'):
        # 每个命中值按同一规则匹配：数组展开一层，标量直接匹配。
        # 不将后续数组整体字符串化，否则会遗漏元素或误匹配数组的文本表示。
        expected_text = _assertion_to_str(expected)
        hit = any(
            _assertion_to_str(value) == expected_text
            for match in hits
            for value in (match if isinstance(match, list) else [match])
        )
        return (hit if operator == 'includes' else not hit), None
    if operator in ('contains', 'not_contains'):
        hit = _assertion_to_str(expected) in _assertion_to_str(actual)
        return (hit if operator == 'contains' else not hit), None
    if operator in ('at_least', 'greater_than', 'less_than', 'close_to'):
        try:
            left, right = float(actual), float(expected)
        except (TypeError, ValueError):
            return False, f'数值比较失败：实际={actual!r} 期望={expected!r} 不可比较'
        if operator == 'at_least':
            return left >= right, None
        if operator == 'greater_than':
            return left > right, None
        if operator == 'less_than':
            return left < right, None
        tolerance = assertion.get('tolerance', 0.01)
        try:
            tolerance = float(tolerance)
        except (TypeError, ValueError):
            tolerance = 0.01
        return abs(left - right) <= tolerance + 1e-9, None
    if operator == 'length':
        try:
            want = int(expected)
        except (TypeError, ValueError):
            return False, f'长度断言期望值不是整数: {expected!r}'
        got = len(actual) if hasattr(actual, '__len__') else -1
        return got == want, None
    if operator == 'matches':
        return _match_assertion_regex(expected, actual)
    if operator == 'one_of':
        options = expected if isinstance(expected, (list, tuple)) else [expected]
        return any(_assertion_to_str(actual) == _assertion_to_str(x) for x in options), None

    return False, f'不支持的断言操作符: {operator}'


def execute_assertions(response, assertions):
    """执行断言验证"""
    results = []
    
    for assertion in assertions:
        json_path = assertion.get('json_path')
        result = {
            'name': assertion.get('name', '未命名断言'),
            'type': assertion.get('type'),
            'operator': assertion.get('operator') or None,
            # 保留断言定位字段，执行结果页需要用原始表达式还原完整断言。
            'json_path': json_path,
            'header_name': assertion.get('header_name'),
            'expected_value': assertion.get('expected_value'),
            'tolerance': assertion.get('tolerance'),
            'passed': False,
            'expected': assertion.get('expected'),
            'actual': None,
            'error': None
        }
        
        try:
            assertion_type = assertion.get('type')
            expected = assertion.get('expected')
            actual = None
            passed = False
            
            if assertion_type == 'status_code':
                actual = response.status_code
                operator = str(assertion.get('operator') or 'equals').strip().lower()
                if operator == 'one_of':
                    options = expected if isinstance(expected, (list, tuple)) else [expected]
                    try:
                        options = [int(x) for x in options]
                    except (TypeError, ValueError):
                        passed = False
                        result['error'] = f'状态码集合断言期望值不是整数: {expected!r}'
                    else:
                        passed = actual in options
                else:
                    passed = actual == expected
                
            elif assertion_type == 'response_time':
                # 响应时间断言在调用方处理
                actual = assertion.get('actual_time')
                passed = actual <= expected if actual else False
                
            elif assertion_type == 'contains':
                text = response.text or ''
                pattern = str(expected)
                actual = text[:200] + '...' if len(text) > 200 else text
                passed = pattern in str(text)
                
            elif assertion_type == 'json_path':
                json_path = json_path or ''
                expected_value = assertion.get('expected')
                actual = None
                passed = False
                
                try:
                    # 检查响应是否为JSON格式
                    content_type = response.headers.get('content-type', '').lower()
                    if 'application/json' not in content_type:
                        raise ValueError(f"响应不是JSON格式，Content-Type: {content_type}")
                    
                    response_json = json.loads(response.text)
                    
                    # 检查JSONPath表达式是否为空
                    if not json_path:
                        raise ValueError("JSON路径表达式不能为空")
                    
                    from jsonpath_ng import parse
                    matches = parse(json_path).find(response_json)
                    hits = [m.value for m in matches]
                    actual = hits[0] if hits else None
                    passed, operator_error = _apply_json_operator(
                        assertion.get('operator'), actual, hits, expected_value, assertion,
                    )
                    if operator_error:
                        result['error'] = operator_error

                    # 确保actual值被正确设置到result中
                    result['actual'] = actual
                except json.JSONDecodeError as e:
                    actual = None
                    passed = False
                    result['error'] = f"JSON解析失败: {str(e)}"
                    result['actual'] = actual
                except ImportError as e:
                    actual = None
                    passed = False
                    result['error'] = f"缺少依赖库: {str(e)}，请安装jsonpath-ng"
                    result['actual'] = actual
                except Exception as e:
                    actual = None
                    passed = False
                    result['error'] = f"执行错误: {str(e)}"
                    result['actual'] = actual
                    
            elif assertion_type == 'header':
                header_name = assertion.get('header_name', '')
                expected_value = assertion.get('expected_value')
                actual = response.headers.get(header_name)
                passed = actual == expected_value
                
            elif assertion_type == 'equals':
                actual = response.text.strip()
                passed = actual == str(expected).strip()
            
            # 确保在所有情况下都设置actual值
            if 'actual' not in result or result['actual'] is None:
                result['actual'] = actual
            result['passed'] = passed
            
        except Exception as e:
            result['error'] = str(e)
            result['passed'] = False
        
        results.append(result)
    
    return results



def execute_scenario_case(scenario_case, environment, executed_by, execution=None,
                          scheduled_task=None):
    """执行单个步骤树场景用例并返回结果。"""
    from .scenario_executor import execute_scenario_tree

    return execute_scenario_tree(
        scenario_case, environment, executed_by, execution=execution,
        scheduled_task=scheduled_task)

def execute_test_suite(test_suite, environment, executed_by, suite_execution=None,
                       scheduled_task=None):
    """依次运行测试套件中的场景用例，并落套件维度执行记录。

    未传入 ``suite_execution`` 时创建执行记录；MCP 异步执行器传入预建记录复用。

    Args:
        suite_execution: 可选的预建 ``ApiSuiteExecution``，传入时复用该记录，
            不再新建（异步执行靠它实现「先返回 ID，后执行」）。
    """
    from .models import ApiSuiteExecution, TestSuiteItem

    execution_environment = environment or test_suite.environment
    # 套件执行记录必须关联执行者：未显式传入时回退套件创建者。
    executed_by = executed_by or test_suite.created_by
    items = list(
        TestSuiteItem.objects.filter(test_suite=test_suite, enabled=True)
        .select_related('scenario_case', 'scenario_case__environment')
        .order_by('order')
    )

    if suite_execution is None:
        suite_execution = ApiSuiteExecution.objects.create(
            test_suite=test_suite,
            status='RUNNING',
            start_time=timezone.now(),
            total_scenarios=len(items),
            executed_by=executed_by,
            scheduled_task=scheduled_task,
        )
    else:
        # 复用调用方预建的记录：重置为运行态，保证轮询方看到一致的初始状态。
        suite_execution.status = 'RUNNING'
        suite_execution.start_time = suite_execution.start_time or timezone.now()
        suite_execution.end_time = None
        suite_execution.error_message = ''
        suite_execution.total_scenarios = len(items)
        suite_execution.passed_scenarios = 0
        suite_execution.failed_scenarios = 0
        suite_execution.results = []
        suite_execution.save()

    results = []
    passed = 0
    failed = 0
    try:
        for item in items:
            scenario_case = item.scenario_case
            result = execute_scenario_case(
                scenario_case,
                execution_environment or scenario_case.environment,
                executed_by,
                scheduled_task=scheduled_task,
            )
            failed_count = result.get('failed_count', 0)
            succeeded = bool(result.get('success')) and failed_count == 0
            if succeeded:
                passed += 1
            else:
                failed += 1
            results.append({
                'scenario_case_id': scenario_case.id,
                'scenario_case_name': scenario_case.name,
                'execution_id': result.get('execution_id'),
                'status': 'COMPLETED' if succeeded else 'FAILED',
                'passed': result.get('passed_count', 0),
                'failed': failed_count,
                'total': result.get('total_count', 0),
                'error': result.get('error') or (
                    '' if succeeded else '场景用例未包含启用的请求'),
            })
            # 每个场景执行完即刷新汇总，长套件执行中也能看到进度。
            suite_execution.passed_scenarios = passed
            suite_execution.failed_scenarios = failed
            suite_execution.results = list(results)
            suite_execution.save(
                update_fields=['passed_scenarios', 'failed_scenarios', 'results'])
        suite_execution.status = 'COMPLETED' if failed == 0 else 'FAILED'
        if not items:
            # 空套件没有可执行场景：落 FAILED，避免「0 场景 0 失败」被当成通过。
            suite_execution.status = 'FAILED'
            suite_execution.error_message = '测试套件未包含任何启用的场景用例，无法执行'
    except Exception as exc:  # noqa: BLE001 - 套件执行异常必须落终态
        logger.exception('[ApiSuite] 套件执行异常 suite_id=%s', test_suite.id)
        suite_execution.status = 'FAILED'
        suite_execution.error_message = str(exc)
    finally:
        suite_execution.end_time = timezone.now()
        suite_execution.passed_scenarios = passed
        suite_execution.failed_scenarios = failed
        suite_execution.results = list(results)
        suite_execution.save()

    return {
        # 空套件没有可执行场景，不能算通过。
        'success': bool(results) and all(
            result['status'] == 'COMPLETED' for result in results),
        'test_suite': test_suite.name,
        'suite_execution_id': suite_execution.id,
        'results': results,
    }


def execute_api_request(api_request, environment, executed_by, scheduled_task=None):
    """执行单个API请求并返回结果。

    与单接口页的「发送」共用环境校验、变量解析和发送逻辑，不再自带一套替换规则。
    """
    from .request_executor import execute_request, resolve_request
    from .scenario_executor import _validation_message

    try:
        project_id = api_request.collection.project_id if api_request.collection_id else None
        snapshot = EnvironmentResolver.snapshot(
            environment, user=executed_by, project_id=project_id)
        dto = resolve_request(request_from_model(api_request), snapshot['variables'])
        sent = execute_request(dto, base_url=snapshot['base_url'])
        response = sent['response']
        response_time = sent['response_time']

        # 执行断言验证
        assertions = dto['assertions']
        for assertion in assertions:
            if assertion.get('type') == 'response_time':
                assertion['actual_time'] = response_time

        assertions_results = execute_assertions(response, assertions)
        # 单接口提取结果只随本次执行返回，不写回环境变量。
        extraction_results, extracted_variables = extract_response_variables(response, dto['extractors'])

        # 保存请求历史
        history = RequestHistory.objects.create(
            request=api_request,
            environment=environment,
            request_data=sent['request'],
            response_data={
                'headers': dict(response.headers),
                'body': response.text,
                'json': response.json() if response.headers.get('content-type', '').startswith('application/json') else None
            },
            status_code=response.status_code,
            response_time=response_time,
            assertions_results=assertions_results,
            executed_by=executed_by,
            scheduled_task=scheduled_task,
        )

        # 请求发出去不等于通过：断言未通过时这次执行算失败，响应和历史照常保留。
        failed_assertions = [
            str(item['name'] or '未命名断言') for item in assertions_results if not item['passed']]
        result = {
            'success': not failed_assertions,
            'history_id': history.id,
            'status_code': response.status_code,
            'response_time': response_time,
            'assertions_results': assertions_results,
            'extraction_results': extraction_results,
            'extracted_variables': extracted_variables,
            'response_data': {
                'headers': dict(response.headers),
                'body': response.text,
                'json': response.json() if response.headers.get('content-type', '').startswith('application/json') else None
            }
        }
        if failed_assertions:
            result['error'] = '断言未通过：' + '、'.join(failed_assertions)
        return result

    except APIException as e:
        # 环境、变量或请求配置不合法时返回可读原因，而不是异常对象的文本。
        return {
            'success': False,
            'error': _validation_message(e)
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
