import json
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.utils import timezone
from .models import RequestHistory
from .variable_resolver import VariableResolver
from .path_params import resolve_path_parameters


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


def execute_assertions(response, assertions):
    """执行断言验证"""
    results = []
    
    for assertion in assertions:
        result = {
            'name': assertion.get('name', '未命名断言'),
            'type': assertion.get('type'),
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
                    actual = matches[0].value if matches else None
                    passed = str(actual) == str(expected_value)
                    
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


def execute_scenario_case(scenario_case, environment, executed_by):
    """执行单个场景用例并返回结果。"""
    from .models import TestExecution, RequestHistory
    
    try:
        # 创建变量解析器
        resolver = VariableResolver()
        
        # 创建执行记录
        execution = TestExecution.objects.create(
            scenario_case=scenario_case,
            status='RUNNING',
            start_time=timezone.now(),
            executed_by=executed_by
        )
        
        # 获取场景用例中的请求
        scenario_case_requests = scenario_case.scenariocaserequest_set.filter(enabled=True).order_by('order')
        
        execution.total_requests = scenario_case_requests.count()
        execution.save()
        
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
                
                results.append({
                    'name': api_request.name,
                    'method': api_request.method,
                    'url': url,
                    'status_code': response.status_code,
                    'response_time': response_time,
                    'passed': passed,
                    'error': error_message,
                    'assertions_results': assertions_results
                })
                
                # 保存请求历史
                RequestHistory.objects.create(
                    request=api_request,
                    environment=environment,
                    request_data={
                        'url': url,
                        'method': api_request.method,
                        'headers': headers,
                        '极速版params': params,
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
        return {
            'success': False,
            'error': str(e)
        }


def execute_test_suite(test_suite, environment, executed_by):
    """依次运行测试套件中的场景用例。"""
    from .models import TestSuiteItem

    results = []
    execution_environment = environment or test_suite.environment
    for item in TestSuiteItem.objects.filter(test_suite=test_suite, enabled=True).select_related(
        'scenario_case', 'scenario_case__environment'
    ).order_by('order'):
        scenario_case = item.scenario_case
        result = execute_scenario_case(
            scenario_case,
            execution_environment or scenario_case.environment,
            executed_by,
        )
        failed = result.get('failed_count', 0)
        succeeded = result.get('success', False) and failed == 0
        results.append({
            'scenario_case_id': scenario_case.id,
            'scenario_case_name': scenario_case.name,
            'status': 'COMPLETED' if succeeded else 'FAILED',
            'passed': result.get('passed_count', 0),
            'failed': failed,
            'total': result.get('total_count', 0),
            'error': result.get('error'),
        })

    return {
        'success': all(result['status'] == 'COMPLETED' for result in results),
        'test_suite': test_suite.name,
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
