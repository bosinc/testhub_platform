import json
import logging
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.utils import timezone
from .models import RequestHistory
from .variable_resolver import VariableResolver
from .path_params import resolve_path_parameters

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


def send_api_request(*, method, url, headers=None, params=None, body_type='none', body=None,
                     timeout=(5, 30)):
    """统一发送 API 请求，连接阶段短暂失败时仅对幂等方法有限重试。

    场景用例执行与单接口执行使用同一套 HTTP 策略；写请求不自动重试，避免重复提交。
    """
    request_kwargs = {
        'method': method,
        'url': url,
        'headers': headers or {},
        'params': params or {},
        'timeout': timeout,
    }
    if body_type == 'raw':
        request_kwargs['data'] = body
    elif body_type in {'json', 'form-data', 'x-www-form-urlencoded'}:
        request_kwargs['json'] = body
    with requests.Session() as session:
        adapter = HTTPAdapter(max_retries=_api_request_retry(method))
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session.request(**request_kwargs)


def prepare_request_body(request_body, method, variables, resolver):
    """按接口配置解析请求体，并返回发送类型与内容。"""
    if not request_body or method.upper() not in {'POST', 'PUT', 'PATCH'}:
        return 'none', None
    body_type = request_body.get('type', 'none')
    body_content = request_body.get('data')
    if body_type == 'json' and isinstance(body_content, dict):
        body_content = _resolve_variables_in_dict(_replace_variables_in_dict(body_content, variables), resolver)
    elif body_type == 'raw' and isinstance(body_content, str):
        body_content = resolver.resolve(_replace_variables(body_content, variables))
    elif body_type in {'form-data', 'x-www-form-urlencoded'} and isinstance(body_content, list):
        body_content = _resolve_variables_in_dict(_replace_variables_in_dict(body_content, variables), resolver)
    return body_type, body_content


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
        # 两种命中语义：取值本身是数组时按元素匹配；递归路径时任一命中即可。
        hit = False
        if isinstance(actual, list):
            hit = any(_assertion_to_str(v) == _assertion_to_str(expected) for v in actual)
        if not hit:
            hit = any(_assertion_to_str(v) == _assertion_to_str(expected) for v in hits)
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
        import re
        try:
            return re.search(str(expected), _assertion_to_str(actual)) is not None, None
        except re.error as error:
            return False, f'正则表达式无效: {error}'
    if operator == 'one_of':
        options = expected if isinstance(expected, (list, tuple)) else [expected]
        return any(_assertion_to_str(actual) == _assertion_to_str(x) for x in options), None

    return False, f'不支持的断言操作符: {operator}'


def execute_assertions(response, assertions):
    """执行断言验证"""
    results = []
    
    for assertion in assertions:
        result = {
            'name': assertion.get('name', '未命名断言'),
            'type': assertion.get('type'),
            'operator': assertion.get('operator') or None,
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
                json_path = assertion.get('json_path', '')
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


def execute_scenario_case(scenario_case, environment, executed_by, execution=None):
    """执行单个场景用例并返回结果。

    execution 为空时新建 TestExecution；异步执行场景下由调用方预建记录
    （便于立即返回执行 ID 供轮询），此处直接复用并更新同一条记录。
    """
    from .models import TestExecution, RequestHistory
    
    try:
        # 创建变量解析器
        resolver = VariableResolver()
        
        # 创建执行记录（异步执行时复用调用方预建的记录）
        if execution is None:
            execution = TestExecution.objects.create(
                scenario_case=scenario_case,
                status='RUNNING',
                start_time=timezone.now(),
                executed_by=executed_by
            )
        else:
            execution.status = 'RUNNING'
            execution.start_time = execution.start_time or timezone.now()
        
        # 获取场景用例中的请求
        scenario_case_requests = scenario_case.scenariocaserequest_set.filter(enabled=True).order_by('order')
        
        execution.total_requests = scenario_case_requests.count()
        execution.save()

        # 没有启用请求的场景视为配置错误：不能因为「0 通过 0 失败」被当成通过。
        # 所有调用方（MCP 套件执行器、execute_test_suite）都走这里，
        # 统一落 FAILED 并给出明确原因。
        if execution.total_requests == 0:
            empty_message = '场景用例未包含启用的请求，无法执行'
            execution.end_time = timezone.now()
            execution.passed_requests = 0
            execution.failed_requests = 0
            execution.status = 'FAILED'
            execution.results = [{'passed': False, 'error': empty_message}]
            execution.save()
            logger.warning('[ApiExecutor] 场景用例无启用请求 id=%s execution_id=%s',
                           scenario_case.id, execution.id)
            return {
                'success': False,
                'execution_id': execution.id,
                'passed_count': 0,
                'failed_count': 0,
                'total_count': 0,
                'results': [{'passed': False, 'error': empty_message}],
                'error': empty_message,
            }

        results = []
        passed_count = 0
        failed_count = 0
        
        # 执行每个请求
        scenario_case_variables = dict(environment.variables or {}) if environment else {}
        for scenario_case_request in scenario_case_requests:
            api_request = scenario_case_request.request
            
            try:
                # 解析环境变量
                variables = dict(scenario_case_variables)
                
                # 替换URL中的变量（先解析动态函数，再替换环境变量）
                url = _replace_variables(api_request.url, variables)
                url = resolver.resolve(url)
                
                # 准备请求头
                headers = {}
                if isinstance(api_request.headers, list):
                    for header_item in api_request.headers:
                        if header_item.get('enabled', True) and header_item.get('key'):
                            key = header_item['key']
                            value = _replace_variables(str(header_item.get('value', '')), variables)
                            value = resolver.resolve(value)
                            headers[key] = value
                else:
                    headers = api_request.headers.copy()
                    for key, value in headers.items():
                        headers[key] = _replace_variables(str(value), variables)
                        headers[key] = resolver.resolve(headers[key])
                
                # 准备请求参数
                params = api_request.params.copy() if api_request.params else {}
                for key, value in params.items():
                    params[key] = _replace_variables(str(value), variables)
                    params[key] = resolver.resolve(params[key])
                url, params = resolve_path_parameters(url, params)
                
                # 准备请求体
                body_type, body_data = prepare_request_body(
                    api_request.body, api_request.method, variables, resolver
                )
                
                # 执行请求
                start_time = time.time()
                response = send_api_request(
                    method=api_request.method,
                    url=url,
                    headers=headers,
                    params=params,
                    body_type=body_type,
                    body=body_data,
                )
                end_time = time.time()
                response_time = (end_time - start_time) * 1000

                _, extracted_variables = extract_response_variables(
                    response,
                    scenario_case_request.extractors,
                )
                variables.update(extracted_variables)
                scenario_case_variables.update(extracted_variables)
                
                # 场景用例请求配置是执行时的唯一断言来源，允许在场景用例内独立编辑。
                assertions = normalize_scenario_case_assertions(scenario_case_request.assertions, response_time)
                for assertion in assertions:
                    if assertion.get('type') == 'response_time':
                        assertion['actual_time'] = response_time
                
                assertions_results = execute_assertions(response, assertions)
                
                # 检查所有断言是否通过
                passed = True
                error_message = ''
                
                for assertion_result in assertions_results:
                    if not assertion_result.get('passed', True):
                        passed = False
                        error_message = (
                            f"断言失败: {assertion_result.get('name', '未命名断言')} - "
                            f"{assertion_result.get('error') or '断言不通过'}"
                        )
                        break
                
                if passed:
                    passed_count += 1
                else:
                    failed_count += 1
                
                request_result = {
                    'name': api_request.name,
                    'method': api_request.method,
                    'url': url,
                    'status_code': response.status_code,
                    'response_time': response_time,
                    'passed': passed,
                    'error': error_message,
                    'assertions_results': assertions_results
                }
                
                # 保存请求历史：历史落库失败只记日志，不改变本次请求的执行结论，
                try:
                    RequestHistory.objects.create(
                        request=api_request,
                        environment=environment,
                        request_data={
                            'url': url,
                            'method': api_request.method,
                            'headers': headers,
                            'params': params,
                            'body': body_data
                        },
                        response_data={
                            'headers': dict(response.headers),
                            'body': response.text,
                            'json': response.json() if response.headers.get('content-type', '').startswith('application/json') else None
                        },
                        status_code=response.status_code,
                        response_time=response_time,
                        assertions_results=assertions_results,
                        executed_by=executed_by
                    )
                except Exception:  # noqa: BLE001 - 历史记录失败不影响执行结论
                    logger.warning('[ApiExecutor] 请求历史保存失败 request_id=%s',
                                   api_request.id, exc_info=True)

                results.append(request_result)
                
            except Exception as e:
                failed_count += 1
                results.append({
                    'name': api_request.name,
                    'method': api_request.method,
                    'url': api_request.url,
                    'passed': False,
                    'error': str(e)
                })
        
        # 更新执行结果
        execution.end_time = timezone.now()
        execution.passed_requests = passed_count
        execution.failed_requests = failed_count
        execution.status = 'COMPLETED' if failed_count == 0 else 'FAILED'
        execution.results = results
        execution.save()
        
        return {
            'success': True,
            'execution_id': execution.id,
            'passed_count': passed_count,
            'failed_count': failed_count,
            'total_count': execution.total_requests,
            'results': results
        }
        
    except Exception as e:
        # 预建记录的异步执行路径必须落终态，避免轮询一直停留在 RUNNING。
        if execution is not None:
            try:
                execution.status = 'FAILED'
                execution.end_time = timezone.now()
                execution.results = [{'passed': False, 'error': str(e)}]
                execution.save(update_fields=['status', 'end_time', 'results'])
            except Exception:  # noqa: BLE001 - 落库失败不掩盖原始异常
                pass
        return {
            'success': False,
            'error': str(e)
        }


def execute_test_suite(test_suite, environment, executed_by, suite_execution=None):
    """依次运行测试套件中的场景用例，并落套件维度执行记录。

    同步调用方（页面 / 定时任务）不传 ``suite_execution``，由本函数创建记录；
    MCP 异步执行器需要「立即返回执行 ID 再后台跑」，会预建记录并传入复用。
    两条路径共用同一套循环、汇总与成功判定，避免判定口径漂移。

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
            )
            failed_count = result.get('failed_count', 0)
            # 0 请求的场景不算通过（与 MCP 套件执行器保持同一判定）。
            succeeded = (bool(result.get('success')) and failed_count == 0
                         and bool(result.get('total_count')))
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


def execute_api_request(api_request, environment, executed_by):
    """执行单个API请求并返回结果"""
    
    try:
        # 创建变量解析器
        resolver = VariableResolver()
        
        # 解析环境变量
        variables = {}
        if environment:
            variables.update(environment.variables)
        
        # 替换URL中的变量（先解析动态函数，再替换环境变量）
        url = _replace_variables(api_request.url, variables)
        url = resolver.resolve(url)
        
        # 准备请求头
        headers = {}
        if isinstance(api_request.headers, list):
            for header_item in api_request.headers:
                if header_item.get('enabled', True) and header_item.get('key'):
                    key = header_item['key']
                    value = _replace_variables(str(header_item.get('value', '')), variables)
                    value = resolver.resolve(value)
                    headers[key] = value
        else:
            headers = api_request.headers.copy()
            for key, value in headers.items():
                headers[key] = _replace_variables(str(value), variables)
                headers[key] = resolver.resolve(headers[key])
        
        # 准备请求参数
        params = api_request.params.copy() if api_request.params else {}
        for key, value in params.items():
            params[key] = _replace_variables(str(value), variables)
            params[key] = resolver.resolve(params[key])
        url, params = resolve_path_parameters(url, params)
        
        # 准备请求体
        body_type, body_data = prepare_request_body(
            api_request.body, api_request.method, variables, resolver
        )
        
        # 执行请求
        start_time = time.time()
        response = send_api_request(
            method=api_request.method,
            url=url,
            headers=headers,
            params=params,
            body_type=body_type,
            body=body_data,
        )
        end_time = time.time()
        response_time = (end_time - start_time) * 1000
        
        # 执行断言验证
        assertions = api_request.assertions or []
        for assertion in assertions:
            if assertion.get('type') == 'response_time':
                assertion['actual_time'] = response_time
        
        assertions_results = execute_assertions(response, assertions)
        # 单接口提取结果只随本次执行返回，不写回环境变量。
        extraction_results, extracted_variables = extract_response_variables(response, api_request.extractors)
        
        # 保存请求历史
        history = RequestHistory.objects.create(
            request=api_request,
            environment=environment,
            request_data={
                'url': url,
                'method': api_request.method,
                'headers': headers,
                'params': params,
                'body': body_data
            },
            response_data={
                'headers': dict(response.headers),
                'body': response.text,
                'json': response.json() if response.headers.get('content-type', '').startswith('application/json') else None
            },
            status_code=response.status_code,
            response_time=response_time,
            assertions_results=assertions_results,
            executed_by=executed_by
        )
        
        return {
            'success': True,
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
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def _replace_variables(text, variables):
    """替换文本中的变量"""
    if not isinstance(text, str):
        return text
    
    result = text
    for key, value in (variables or {}).items():
        if isinstance(value, dict):
            replacement = str(value.get('currentValue', '') or value.get('initialValue', ''))
        else:
            replacement = str(value) if value is not None else ''
        result = result.replace(f'{{{{{key}}}}}', replacement)
    return result

def _replace_variables_in_dict(data, variables):
    """递归替换字典中的变量"""
    if isinstance(data, dict):
        return {k: _replace_variables_in_dict(v, variables) for k, v in data.items()}
    elif isinstance(data, list):
        return [_replace_variables_in_dict(item, variables) for item in data]
    elif isinstance(data, str):
        return _replace_variables(data, variables)
    else:
        return data

def _resolve_variables_in_dict(data, resolver):
    """递归解析字典中的动态函数占位符"""
    if isinstance(data, dict):
        return {k: _resolve_variables_in_dict(v, resolver) for k, v in data.items()}
    elif isinstance(data, list):
        return [_resolve_variables_in_dict(item, resolver) for item in data]
    elif isinstance(data, str):
        return resolver.resolve(data)
    else:
        return data
