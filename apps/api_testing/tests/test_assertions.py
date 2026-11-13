"""断言执行器（execute_assertions）的单元测试。

覆盖 `json_path` 断言的操作符扩展：
  - 缺省 operator 时行为与历史一致（字符串化 equals），保证旧数据不回归；
  - 新增 operator：not_equals / exists / not_exists / is_not_empty / is_empty /
    类型判定 / includes / contains / 数值比较 / length / matches / one_of；
  - status_code 的 one_of（状态码集合）。
"""
import json
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from ..utils import (
    REGEX_ASSERTION_TIMEOUT_SECONDS,
    _match_assertion_regex,
    execute_assertions,
)


class _FakeResponse:
    """最小响应替身，execute_assertions 只用到 status_code / headers / text。"""

    def __init__(self, payload, status_code=200, content_type='application/json'):
        self.status_code = status_code
        self.headers = {'content-type': content_type}
        self.text = payload if isinstance(payload, str) else json.dumps(payload)


def run_one(assertion, payload, status_code=200):
    """执行单条断言，返回 (passed, actual, error)。"""
    response = _FakeResponse(payload, status_code=status_code)
    [result] = execute_assertions(response, [assertion])
    return result['passed'], result['actual'], result['error']


class JsonPathAssertionOperatorTests(SimpleTestCase):
    PAYLOAD = {
        'code': '200',
        'message': 'success',
        'data': {
            'id': 'abc-123',
            'count': 3,
            'enabled': True,
            'empty_list': [],
            'tags': ['Posts', 'Links'],
            'items': [{'id': 'i-1'}],
            'nested': {'a': {'b': 1}},
        },
    }

    # --- 向后兼容 -------------------------------------------------------
    def test_missing_operator_behaves_like_equals(self):
        # 历史数据只有 type=json_path + json_path + expected，没有 operator
        passed, actual, error = run_one(
            {'type': 'json_path', 'json_path': '$.code', 'expected': '200'}, self.PAYLOAD
        )
        self.assertTrue(passed)
        self.assertIsNone(error)
        self.assertEqual(actual, '200')

    def test_equals_stringifies_like_js(self):
        # 数值 3 与字符串 '3' 视为相等；2.0 归一成 '2'
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.count', 'expected': '3'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    def test_equals_fails_on_mismatch(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.code', 'expected': '500'}, self.PAYLOAD
        )
        self.assertFalse(passed)

    # --- 相等/存在 -------------------------------------------------------
    def test_not_equals(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.code', 'operator': 'not_equals', 'expected': '500'},
            self.PAYLOAD,
        )
        self.assertTrue(passed)

    def test_exists(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.nested.a.b', 'operator': 'exists'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    def test_exists_fails_when_path_missing(self):
        passed, actual, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.missing', 'operator': 'exists'}, self.PAYLOAD
        )
        self.assertFalse(passed)
        self.assertIsNone(actual)

    def test_not_exists(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.missing', 'operator': 'not_exists'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    # --- 空 / 非空 -------------------------------------------------------
    def test_is_not_empty(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.tags', 'operator': 'is_not_empty'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    def test_is_not_empty_fails_on_empty_list(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.empty_list', 'operator': 'is_not_empty'},
            self.PAYLOAD,
        )
        self.assertFalse(passed)

    def test_is_empty(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.empty_list', 'operator': 'is_empty'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    def test_zero_is_not_empty(self):
        # 对齐源框架语义：0 / False 视为有值，不算空
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.count', 'operator': 'is_not_empty'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    # --- 类型 -----------------------------------------------------------
    def test_is_string(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.id', 'operator': 'is_string'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    def test_is_number_rejects_bool(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.enabled', 'operator': 'is_number'}, self.PAYLOAD
        )
        self.assertFalse(passed)
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.count', 'operator': 'is_number'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    def test_is_boolean(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.enabled', 'operator': 'is_boolean'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    def test_is_array_and_is_object(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.tags', 'operator': 'is_array'}, self.PAYLOAD
        )
        self.assertTrue(passed)
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.nested', 'operator': 'is_object'}, self.PAYLOAD
        )
        self.assertTrue(passed)

    # --- 包含 -----------------------------------------------------------
    def test_includes_array_element(self):
        # 取值是数组时按元素匹配
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.tags', 'operator': 'includes', 'expected': 'Links'},
            self.PAYLOAD,
        )
        self.assertTrue(passed)

    def test_includes_fails_when_absent(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.tags', 'operator': 'includes', 'expected': 'Forms'},
            self.PAYLOAD,
        )
        self.assertFalse(passed)

    def test_includes_recursive_path(self):
        payload = {'data': {'storeTabs': [{'name': 'Posts'}, {'name': 'Links'}]}}
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data..storeTabs..name', 'operator': 'includes',
             'expected': 'Links'},
            payload,
        )
        self.assertTrue(passed)

    def test_includes_and_not_includes_use_all_matched_arrays(self):
        # 审核复现：目标只出现在第二个数组；顺序、空数组均不改变结论。
        cases = [
            ([{'tags': ['Posts']}, {'tags': ['Links']}], 'Links', True),
            ([{'tags': ['Links']}, {'tags': ['Posts']}], 'Links', True),
            ([{'tags': []}, {'tags': ['Links']}], 'Links', True),
            ([{'tags': ['Posts']}, {'tags': ['Links']}], 'Forms', False),
            ([{'tags': []}, {'tags': []}], 'Links', False),
            ([], 'Links', False),
            # 不将数组本身的字符串表示当成元素。
            ([{'tags': ['Posts']}, {'tags': ['Links']}], "['Links']", False),
            ([{'tags': ['Posts']}, {'tags': 'Links'}], 'Links', True),
            ([{'tags': 'Posts'}, {'tags': ['Links']}], 'Links', True),
            ([{'tags': []}, {'tags': [True, None, 2.0]}], 'true', True),
            ([{'tags': []}, {'tags': [True, None, 2.0]}], 'null', True),
            ([{'tags': []}, {'tags': [True, None, 2.0]}], '2', True),
            # 嵌套数组只展开一层，不递归匹配孙元素。
            ([{'tags': [['Links']]}], 'Links', False),
            ([{'tags': [['Links']]}], ['Links'], True),
        ]
        for groups, expected, included in cases:
            for operator in ('includes', 'not_includes'):
                with self.subTest(groups=groups, expected=expected, operator=operator):
                    passed, _, error = run_one(
                        {'type': 'json_path', 'json_path': '$.groups[*].tags',
                         'operator': operator, 'expected': expected},
                        {'groups': groups},
                    )
                    self.assertEqual(passed, included if operator == 'includes' else not included)
                    self.assertIsNone(error)

    def test_contains_substring(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.message', 'operator': 'contains', 'expected': 'succ'},
            self.PAYLOAD,
        )
        self.assertTrue(passed)

    # --- 数值 -----------------------------------------------------------
    def test_at_least_and_greater_than(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.count', 'operator': 'at_least', 'expected': 3},
            self.PAYLOAD,
        )
        self.assertTrue(passed)
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.count', 'operator': 'greater_than', 'expected': 3},
            self.PAYLOAD,
        )
        self.assertFalse(passed)

    def test_numeric_operator_on_non_number_reports_error(self):
        passed, _, error = run_one(
            {'type': 'json_path', 'json_path': '$.data.id', 'operator': 'at_least', 'expected': 1},
            self.PAYLOAD,
        )
        self.assertFalse(passed)
        self.assertIn('不可比较', error)

    def test_close_to_tolerance(self):
        payload = {'data': {'amount': 129102.44}}
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.amount', 'operator': 'close_to',
             'expected': 129102.43},
            payload,
        )
        self.assertTrue(passed)
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.amount', 'operator': 'close_to',
             'expected': 129102.43, 'tolerance': 0.001},
            payload,
        )
        self.assertFalse(passed)

    # --- 其它 -----------------------------------------------------------
    def test_length(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.tags', 'operator': 'length', 'expected': 2},
            self.PAYLOAD,
        )
        self.assertTrue(passed)

    def test_matches_regex(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.data.id', 'operator': 'matches',
             'expected': r'^abc-\d+$'},
            self.PAYLOAD,
        )
        self.assertTrue(passed)

    def test_one_of(self):
        passed, _, _ = run_one(
            {'type': 'json_path', 'json_path': '$.code', 'operator': 'one_of',
             'expected': ['200', '201']},
            self.PAYLOAD,
        )
        self.assertTrue(passed)

    def test_unknown_operator_fails_with_error(self):
        # 未知操作符必须明确失败并给出原因，不能静默通过
        passed, _, error = run_one(
            {'type': 'json_path', 'json_path': '$.code', 'operator': 'no_such_op', 'expected': '200'},
            self.PAYLOAD,
        )
        self.assertFalse(passed)
        self.assertIn('不支持的断言操作符', error)

    def test_non_json_response_reports_error(self):
        response = _FakeResponse('<html>oops</html>', content_type='text/html')
        [result] = execute_assertions(
            response, [{'type': 'json_path', 'json_path': '$.code', 'expected': '200'}]
        )
        self.assertFalse(result['passed'])
        self.assertIn('不是JSON格式', result['error'])


class RegexAssertionIsolationTests(SimpleTestCase):
    def test_regex_semantics_and_stringified_values(self):
        for value, pattern, expected_passed in [
            ('abc-123', r'(?<=abc-)\d+$', True),
            ('abc-abc', r'^(\w+)-\1$', True),
            ('测试通过', '^测试', True),
            (True, '^true$', True),
            (None, '^null$', True),
            (2.0, '^2$', True),
            ('abc-123', '^def', False),
        ]:
            with self.subTest(value=value, pattern=pattern):
                passed, actual, error = run_one(
                    {'type': 'json_path', 'json_path': '$.value',
                     'operator': 'matches', 'expected': pattern},
                    {'value': value},
                )
                self.assertEqual(passed, expected_passed)
                self.assertEqual(actual, value)
                self.assertIsNone(error)

    def test_invalid_regex_keeps_actual_and_reports_error(self):
        passed, actual, error = run_one(
            {'type': 'json_path', 'json_path': '$.value', 'operator': 'matches', 'expected': '['},
            {'value': 'abc'},
        )
        self.assertFalse(passed)
        self.assertEqual(actual, 'abc')
        self.assertIn('正则表达式无效', error)

    def test_process_start_failure_fails_closed(self):
        with patch('apps.api_testing.utils.subprocess.run', side_effect=OSError('unavailable')):
            passed, error = _match_assertion_regex('abc', 'abc')
        self.assertFalse(passed)
        self.assertIn('正则匹配执行失败', error)

    def test_timeout_budget_cannot_be_overridden_by_assertion(self):
        with patch('apps.api_testing.utils.subprocess.run') as run:
            run.side_effect = subprocess.TimeoutExpired('regex', REGEX_ASSERTION_TIMEOUT_SECONDS)
            passed, _, error = run_one(
                {'type': 'json_path', 'json_path': '$.value', 'operator': 'matches',
                 'expected': 'abc', 'timeout': 999},
                {'value': 'abc'},
            )
        self.assertFalse(passed)
        self.assertIn('超时', error)
        self.assertEqual(run.call_args.kwargs['timeout'], REGEX_ASSERTION_TIMEOUT_SECONDS)

    def test_matching_from_non_main_thread(self):
        # 执行器也用于后台线程，不能依赖只在主线程可用的 signal 超时。
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(pool.submit(_match_assertion_regex, '^abc$', 'abc').result(5),
                             (True, None))

    def test_catastrophic_backtracking_is_killed_and_next_assertion_runs(self):
        # 外层进程再加硬上限，防止将来隔离退化为直接 re.search 时挂死整个测试。
        probe = textwrap.dedent('''
            import json
            import subprocess
            import time
            from types import SimpleNamespace
            from unittest.mock import patch
            import django
            django.setup()
            from apps.api_testing.utils import execute_assertions

            children = []
            original_popen = subprocess.Popen
            def tracked_popen(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                children.append(process)
                return process

            text = 'a' * 30 + '!'
            response = SimpleNamespace(status_code=200,
                headers={'content-type': 'application/json'}, text=json.dumps({'value': text}))
            started = time.monotonic()
            with patch('apps.api_testing.utils.subprocess.Popen', side_effect=tracked_popen):
                results = execute_assertions(response, [
                    {'type': 'json_path', 'json_path': '$.value', 'operator': 'matches',
                     'expected': '(a+)+$'},
                    {'type': 'status_code', 'expected': 200},
                ])
            print(json.dumps({'results': results, 'elapsed': time.monotonic() - started,
                'children_reaped': bool(children) and all(p.returncode is not None for p in children)}))
        ''')
        completed = subprocess.run(
            [sys.executable, '-c', probe],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        observed = json.loads(completed.stdout)
        timed_out, following = observed['results']
        self.assertFalse(timed_out['passed'])
        self.assertIn('超时', timed_out['error'])
        self.assertEqual(timed_out['actual'], 'a' * 30 + '!')
        self.assertTrue(following['passed'])
        self.assertTrue(observed['children_reaped'])
        self.assertLess(observed['elapsed'], REGEX_ASSERTION_TIMEOUT_SECONDS + 1)


class StatusCodeAssertionOperatorTests(SimpleTestCase):
    def test_default_equals(self):
        response = _FakeResponse({}, status_code=200)
        [result] = execute_assertions(response, [{'type': 'status_code', 'expected': 200}])
        self.assertTrue(result['passed'])

    def test_one_of_accepts_any_listed_code(self):
        response = _FakeResponse({}, status_code=201)
        [result] = execute_assertions(
            response, [{'type': 'status_code', 'operator': 'one_of', 'expected': [200, 201]}]
        )
        self.assertTrue(result['passed'])

    def test_one_of_rejects_unlisted_code(self):
        response = _FakeResponse({}, status_code=500)
        [result] = execute_assertions(
            response, [{'type': 'status_code', 'operator': 'one_of', 'expected': [200, 201]}]
        )
        self.assertFalse(result['passed'])
