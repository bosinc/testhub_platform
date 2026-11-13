import importlib.util
import sys
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import ModuleType
from unittest.mock import Mock, patch
from uuid import UUID

from ..scenario_definition import (
    NODE_FIELDS,
    ExternalStepReferenceError,
    clone_definition,
    clone_subtree,
    validate_definition,
)
from ..step_registry import (
    MAX_LOOP_ITERATIONS,
    STEP_REGISTRY,
    StepValidationError,
    get_step_type,
)


def uid(number):
    return str(UUID(int=number))


def condition(**changes):
    result = {'left': '{{vars.ready}}', 'operator': 'eq', 'right': True, 'right_type': 'boolean'}
    result.update(changes)
    return result


def node(number, kind='group', **changes):
    configs = {
        'group': {}, 'condition': condition(), 'else': {'if_step_id': uid(1)},
        'for': {'count': 0}, 'foreach': {'array_expression': '{{vars.items}}'},
        'wait': {'duration_ms': 0}, 'script': {'language': 'javascript', 'code': 'return 1;'},
        'scenario_ref': {}, 'http': {'request': {'method': 'GET', 'url': '/items'}},
    }
    result = {
        'id': uid(number), 'parent': None, 'position': number - 1,
        'type': kind, 'config': deepcopy(configs[kind]),
    }
    if kind == 'scenario_ref':
        result.update(source_type='scenario_case', source_id='42')
    result.update(changes)
    return result


def paired_tree():
    return [
        node(1, 'condition'),
        node(2, 'wait', parent=uid(1)),
        node(3, 'else'),
        node(4, 'script', parent=uid(3)),
        node(5, 'wait'),
    ]


def request_dto_mock():
    module = ModuleType('apps.api_testing.request_dto')
    module.REQUEST_FIELDS = frozenset({'method', 'url', 'params', 'request_type'})
    module.normalize_request = Mock(side_effect=deepcopy)
    return patch.dict(sys.modules, {'apps.api_testing.request_dto': module}), module


class StepRegistryTests(unittest.TestCase):
    def test_fixed_registry_is_immutable_and_has_no_execution_handlers(self):
        self.assertEqual(set(STEP_REGISTRY), {
            'http', 'group', 'condition', 'else', 'for', 'foreach', 'wait', 'script', 'scenario_ref',
        })
        with self.assertRaises(TypeError):
            STEP_REGISTRY['custom'] = object()
        with self.assertRaises(FrozenInstanceError):
            get_step_type('script').system_name = 'changed'
        for capability in STEP_REGISTRY.values():
            self.assertFalse(hasattr(capability, 'execute'))
            self.assertFalse(hasattr(capability, 'handler'))

    def test_capabilities_match_container_name_and_source_rules(self):
        for key in STEP_REGISTRY:
            with self.subTest(key=key):
                capability = get_step_type(key)
                self.assertEqual(capability.allows_children, key in {'group', 'condition', 'else', 'for', 'foreach'})
                self.assertEqual(capability.name_editable, key == 'http')
                self.assertEqual(capability.system_name, key)
        self.assertFalse(get_step_type('else').independently_creatable)
        self.assertTrue(get_step_type('scenario_ref').source_required)

    def test_registry_rejects_unknown_or_unhashable_types(self):
        for key in ('database', 'branch', '', None, [], {}):
            with self.subTest(key=key), self.assertRaises(StepValidationError):
                get_step_type(key)

    def test_group_only_accepts_empty_object(self):
        for config in ([], None, {'branch': 'then'}, {'source_meta': {}}):
            with self.subTest(config=config), self.assertRaises(StepValidationError):
                get_step_type('group').validate_config(config)

    def test_typed_conditions_do_not_coerce_values(self):
        for right, right_type in ((True, 'boolean'), ('true', 'string'), (1, 'number'), (1.5, 'number'), (None, 'null')):
            with self.subTest(right=right, right_type=right_type):
                config = condition(right=right, right_type=right_type)
                self.assertEqual(get_step_type('condition').validate_config(config), config)
        for right, right_type in ((True, 'number'), (1, 'boolean'), ('1', 'number'), ('false', 'boolean'), (0, 'null')):
            with self.subTest(right=right, right_type=right_type), self.assertRaises(StepValidationError):
                get_step_type('condition').validate_config(condition(right=right, right_type=right_type))

    def test_condition_requires_fixed_fields_and_known_operators(self):
        invalid = [
            condition(operator='eval'), condition(operator=[]), condition(right_type='object'),
            condition(right_type={}), condition(left=[]), condition(expression='1 + 1'),
        ]
        missing = condition()
        del missing['right_type']
        invalid.append(missing)
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(StepValidationError):
                get_step_type('condition').validate_config(config)

    def test_fixed_unary_and_ordered_operators(self):
        for operator in ('exists', 'not_exists', 'is_empty', 'not_empty'):
            config = condition(operator=operator, right=None, right_type='null')
            self.assertEqual(get_step_type('condition').validate_config(config), config)
            with self.assertRaises(StepValidationError):
                get_step_type('condition').validate_config(condition(operator=operator))
        for operator in ('gt', 'gte', 'lt', 'lte'):
            get_step_type('condition').validate_config(condition(operator=operator, right=2, right_type='number'))
            with self.assertRaises(StepValidationError):
                get_step_type('condition').validate_config(condition(operator=operator))
        for operator in ('contains', 'not_contains'):
            get_step_type('condition').validate_config(condition(operator=operator, right='x', right_type='string'))

    def test_for_count_and_expression_limits(self):
        for count in (0, 1000, '{{vars.count}}', ' {{steps.' + uid(1) + '.response.body.count}} '):
            result = get_step_type('for').validate_config({'count': count})
            self.assertEqual(result['max_iterations'], MAX_LOOP_ITERATIONS)
            self.assertEqual(result['on_error'], 'abort_scenario')
        for count in (-1, 1001, True, 1.5, '1000', 'vars.count', '1 + 1', '{{vars.x + 1}}', None):
            with self.subTest(count=count), self.assertRaises(StepValidationError):
                get_step_type('for').validate_config({'count': count})

    def test_loop_options_use_typed_break_and_fixed_policies(self):
        for kind in ('for', 'foreach'):
            base = node(1, kind)['config']
            for policy in ('continue', 'break_loop', 'abort_scenario'):
                config = dict(base, break_if=condition(), on_error=policy, max_iterations=10)
                result = get_step_type(kind).validate_config(config)
                self.assertEqual(result['break_if'], condition())
                self.assertEqual(result['on_error'], policy)
            for change in ({'break_if': 'eval(1)'}, {'on_error': 'success'}, {'on_error': []},
                           {'max_iterations': 1001}, {'max_iterations': True}, {'runtime': 'python'}):
                with self.subTest(kind=kind, change=change), self.assertRaises(StepValidationError):
                    get_step_type(kind).validate_config(dict(base, **change))
        with self.assertRaises(StepValidationError):
            get_step_type('for').validate_config({'count': 11, 'max_iterations': 10})

    def test_foreach_requires_full_array_expression(self):
        result = get_step_type('foreach').validate_config({'array_expression': '{{vars.items}}'})
        self.assertEqual(result['array_expression'], '{{vars.items}}')
        for expression in ([], {}, '[]', '', 'prefix {{vars.items}}', '{{items}}', True):
            with self.subTest(expression=expression), self.assertRaises(StepValidationError):
                get_step_type('foreach').validate_config({'array_expression': expression})

    def test_wait_limits_reject_bool_float_and_unknown_fields(self):
        for duration in (0, 300000):
            self.assertEqual(get_step_type('wait').validate_config({'duration_ms': duration}), {'duration_ms': duration})
        for config in ({'duration_ms': -1}, {'duration_ms': 300001}, {'duration_ms': True},
                       {'duration_ms': 0.5}, {'duration_ms': '1'}, {}, {'duration_ms': 0, 'seconds': 0}):
            with self.subTest(config=config), self.assertRaises(StepValidationError):
                get_step_type('wait').validate_config(config)

    def test_script_schema_stores_code_without_running_it(self):
        config = {'language': 'javascript', 'code': 'throw new Error("not executed");'}
        self.assertEqual(get_step_type('script').validate_config(config), config)
        for config in ({'language': 'python', 'code': 'pass'}, {'language': 'javascript', 'code': ' '},
                       {'language': 'javascript', 'code': 1}, {'language': 'javascript', 'code': 'x', 'runtime': 'node'}):
            with self.subTest(config=config), self.assertRaises(StepValidationError):
                get_step_type('script').validate_config(config)

    def test_reference_mappings_and_controlled_source_meta(self):
        config = {
            'input_mapping': {'value': {'nested': [True, None, 1]}},
            'output_mapping': {'result': '{{vars.result}}'},
            'source_meta': {'name': 'source', 'imported_at': '2026-11-13', 'content_hash': 'abc'},
        }
        result = get_step_type('scenario_ref').validate_config(config)
        self.assertEqual(result, config)
        self.assertIsNot(result['input_mapping']['value'], config['input_mapping']['value'])
        for config in ({'input_mapping': []}, {'output_mapping': {'': 'x'}}, {'input_mapping': {1: 'x'}},
                       {'source_id': '42'}, {'source_meta': {'source_id': '42'}},
                       {'source_meta': {'source_type': 'scenario_case'}}, {'source_meta': {'name': ''}}):
            with self.subTest(config=config), self.assertRaises(StepValidationError):
                get_step_type('scenario_ref').validate_config(config)

    def test_config_rejects_non_json_nonfinite_and_cycles(self):
        circular = []
        circular.append(circular)
        for value in (float('nan'), float('inf'), object(), (1, 2), circular):
            with self.subTest(value=type(value)), self.assertRaises(StepValidationError):
                get_step_type('scenario_ref').validate_config({'input_mapping': {'bad': value}})

    def test_http_delegates_complete_request_to_shared_normalizer(self):
        context, dto = request_dto_mock()
        request = {'method': 'GET', 'url': '/items', 'params': [{'key': 'x', 'value': '1'}]}
        with context:
            result = get_step_type('http').validate_config({'request': request})
        dto.normalize_request.assert_called_once_with(request)
        self.assertIsNot(dto.normalize_request.call_args.args[0], request)
        self.assertEqual(result['request'], request)
        self.assertIsNot(result['request']['params'], request['params'])

    def test_http_rejects_unknown_request_config_and_source_meta_fields(self):
        context, dto = request_dto_mock()
        with context:
            for config in ({'request': {'branch': 'then'}}, {'request': [], 'source_meta': {}},
                           {'request': {}, 'environment_id': 1}, {'request': {}, 'source_meta': {'source_id': '1'}}):
                with self.subTest(config=config), self.assertRaises(StepValidationError):
                    get_step_type('http').validate_config(config)
        self.assertEqual(dto.normalize_request.call_count, 1)

    def test_http_rejects_websocket_even_when_shared_dto_supports_it(self):
        context, dto = request_dto_mock()
        with context, self.assertRaisesRegex(StepValidationError, 'WebSocket'):
            get_step_type('http').validate_config({'request': {'url': '/socket', 'request_type': 'WEBSOCKET'}})
        dto.normalize_request.assert_called_once()

    def test_http_propagates_shared_normalizer_validation_errors(self):
        context, dto = request_dto_mock()
        error = ValueError('DTO 校验拒绝请求')
        dto.normalize_request.side_effect = error
        with context, self.assertRaises(ValueError) as raised:
            get_step_type('http').validate_config({'request': {'url': 'invalid'}})
        self.assertIs(raised.exception, error)

    def test_http_with_real_request_dto_when_available(self):
        if importlib.util.find_spec('apps.api_testing.request_dto') is None:
            self.skipTest('并行实现的 request_dto 尚未落盘；委派契约由独立测试覆盖')
        if importlib.util.find_spec('django') is None or importlib.util.find_spec('rest_framework') is None:
            self.skipTest('当前 Python 缺少既有 Django/DRF 依赖，未执行真实 DTO 联测')
        from django.apps import apps
        if not apps.ready:
            self.skipTest('真实 DTO 联测需要已初始化的项目 Django 测试环境')
        from rest_framework.exceptions import ValidationError
        from ..request_dto import normalize_request
        request = {'method': 'GET', 'url': '/items'}
        result = validate_definition([node(1, 'http', config={'request': request})])
        self.assertEqual(result[0]['config']['request'], normalize_request(request))
        for bad in ({'url': '/../escape'}, {'url': '/items', 'headers': {}}):
            with self.subTest(request=bad), self.assertRaises(ValidationError):
                validate_definition([node(1, 'http', config={'request': bad})])
        with self.assertRaises(StepValidationError):
            validate_definition([node(1, 'http', config={'request': {'url': '/socket', 'request_type': 'WEBSOCKET'}})])


class ScenarioDefinitionTests(unittest.TestCase):
    def test_empty_schema_is_valid_but_not_an_execution_claim(self):
        self.assertEqual(validate_definition([]), [])

    def test_normalizes_uuid_defaults_exact_fields_and_deep_copy(self):
        original = [node(255, 'scenario_ref', id=uid(255).upper(), name='client name',
                         config={'input_mapping': {'x': [1, {'y': True}]}})]
        before = deepcopy(original)
        result = validate_definition(original)
        self.assertEqual(original, before)
        self.assertEqual(set(result[0]), NODE_FIELDS)
        self.assertEqual(result[0]['id'], uid(255))
        self.assertEqual(result[0]['name'], 'scenario_ref')
        self.assertTrue(result[0]['enabled'])
        self.assertIsNone(result[0]['parent'])
        result[0]['config']['input_mapping']['x'][1]['y'] = False
        self.assertEqual(original, before)

    def test_stable_sibling_sort_and_preorder_ignore_input_order(self):
        steps = [node(3, 'wait', parent=uid(1), position=9), node(2, position=4),
                 node(4, 'wait', parent=uid(1), position=9), node(1, position=4)]
        before = deepcopy(steps)
        result = validate_definition(steps)
        self.assertEqual([item['id'] for item in result], [uid(1), uid(3), uid(4), uid(2)])
        self.assertEqual([item['position'] for item in result], [0, 0, 1, 1])
        self.assertEqual(steps, before)
        self.assertEqual(validate_definition(list(reversed(steps))), result)
        self.assertEqual(validate_definition(result), result)

    def test_parent_uuid_is_canonical(self):
        result = validate_definition([node(255), node(256, 'wait', parent=uid(255).upper())])
        self.assertEqual(result[1]['parent'], uid(255))

    def test_rejects_unknown_node_fields_including_branch(self):
        for field in ('branch', 'children', 'scenario_case', 'scenario_id', 'handler'):
            with self.subTest(field=field), self.assertRaises(StepValidationError):
                validate_definition([node(1, **{field: None})])

    def test_rejects_missing_identity_invalid_uuid_and_duplicate_canonical_ids(self):
        for field in ('id', 'type', 'position'):
            bad = node(1)
            del bad[field]
            with self.subTest(field=field), self.assertRaises(StepValidationError):
                validate_definition([bad])
        for value in ('not-uuid', '', 1, UUID(int=1), None):
            with self.subTest(value=value), self.assertRaises(StepValidationError):
                validate_definition([node(1, id=value)])
        with self.assertRaises(StepValidationError):
            validate_definition([node(255), node(256, id=uid(255).upper())])

    def test_rejects_nonlist_nodes_and_unknown_types(self):
        for steps in ({}, (), None, [None], [1], [dict(node(1), type='database')], [dict(node(1), type=[])], [{1: 'bad'}]):
            with self.subTest(steps=steps), self.assertRaises(StepValidationError):
                validate_definition(steps)

    def test_position_and_enabled_do_not_accept_bool_or_coercion(self):
        for value in (-1, True, 1.0, '0', None):
            with self.subTest(position=value), self.assertRaises(StepValidationError):
                validate_definition([node(1, position=value)])
        for value in (0, 1, 'true', None):
            with self.subTest(enabled=value), self.assertRaises(StepValidationError):
                validate_definition([node(1, enabled=value)])

    def test_rejects_foreign_parent_invalid_parent_self_reference_and_cycles(self):
        cases = [
            [node(1, parent=uid(99))], [node(1, parent='')], [node(1, parent=1)],
            [node(1, parent=uid(1))], [node(1, parent=uid(2)), node(2, parent=uid(1))],
            [node(3), node(1, parent=uid(2)), node(2, parent=uid(1))],
        ]
        for steps in cases:
            with self.subTest(steps=steps), self.assertRaises(StepValidationError):
                validate_definition(steps)

    def test_every_leaf_rejects_children(self):
        context, _ = request_dto_mock()
        with context:
            for kind in ('http', 'script', 'wait', 'scenario_ref'):
                with self.subTest(kind=kind), self.assertRaisesRegex(StepValidationError, '叶子'):
                    validate_definition([node(1, kind), node(2, parent=uid(1))])

    def test_depth_counts_root_as_one_and_enforces_default_limit(self):
        steps = [node(index, parent=uid(index - 1) if index > 1 else None) for index in range(1, 18)]
        self.assertEqual(len(validate_definition(steps[:16])), 16)
        with self.assertRaisesRegex(StepValidationError, '深度'):
            validate_definition(steps)
        self.assertEqual(len(validate_definition(list(reversed(steps)), max_depth=17)), 17)
        with self.assertRaises(StepValidationError):
            validate_definition(steps[:2], max_depth=1)
        for limit in (0, -1, True, 1.5, '16'):
            with self.subTest(limit=limit), self.assertRaises(StepValidationError):
                validate_definition([], max_depth=limit)

    def test_existing_identity_type_is_immutable_even_when_config_valid(self):
        previous = validate_definition([node(1)])
        with self.assertRaisesRegex(StepValidationError, '不能更换 type'):
            validate_definition([node(1, 'wait')], previous_steps=previous)
        self.assertEqual(validate_definition([node(1, position=42)], previous_steps=previous)[0]['id'], uid(1))
        self.assertEqual(validate_definition([node(2, 'wait')], previous_steps=previous)[0]['type'], 'wait')

    def test_new_nonhttp_names_are_generated_and_updates_reject_tampering(self):
        for kind in ('group', 'for', 'foreach', 'script', 'wait', 'scenario_ref', 'condition'):
            with self.subTest(kind=kind):
                previous = validate_definition([node(1, kind, name='client override')])
                self.assertEqual(previous[0]['name'], kind)
                self.assertEqual(validate_definition([node(1, kind)], previous_steps=previous), previous)
                with self.assertRaisesRegex(StepValidationError, 'name'):
                    validate_definition([node(1, kind, name='changed')], previous_steps=previous)
        previous = validate_definition(paired_tree())
        changed = deepcopy(previous)
        next(item for item in changed if item['type'] == 'else')['name'] = 'changed'
        with self.assertRaises(StepValidationError):
            validate_definition(changed, previous_steps=previous)

    def test_http_name_can_be_renamed_without_changing_id(self):
        context, _ = request_dto_mock()
        with context:
            previous = validate_definition([node(1, 'http', name='original')])
            result = validate_definition([node(1, 'http', name=' renamed ')], previous_steps=previous)
            self.assertEqual(result[0]['name'], 'renamed')
            self.assertEqual(result[0]['id'], previous[0]['id'])
            self.assertEqual(validate_definition([node(1, 'http')], previous_steps=result)[0]['name'], 'renamed')
            for name in ('', ' ', 'a' * 256, True, None, 'line\nbreak', 'bad\x00'):
                with self.subTest(name=name), self.assertRaises(StepValidationError):
                    validate_definition([node(1, 'http', name=name)])

    def test_source_fields_are_paired_strings_and_controlled_by_type(self):
        context, _ = request_dto_mock()
        with context:
            for changes in ({'source_type': 'api_request'}, {'source_id': '1'},
                            {'source_type': 'api_request', 'source_id': 1},
                            {'source_type': 1, 'source_id': '1'},
                            {'source_type': 'api_request', 'source_id': True},
                            {'source_type': 'api_request', 'source_id': ' '},
                            {'source_type': 'apps.users.User', 'source_id': '1'},
                            {'source_type': 'scenario_case', 'source_id': '1'}):
                with self.subTest(changes=changes), self.assertRaises(StepValidationError):
                    validate_definition([node(1, 'http', **changes)])
            for kind in ('group', 'condition', 'for', 'foreach', 'wait', 'script'):
                with self.subTest(kind=kind), self.assertRaises(StepValidationError):
                    validate_definition([node(1, kind, source_type='api_request', source_id='1')])
            for source_id in ('0', '123', uid(2)):
                result = validate_definition([node(1, 'http', source_type='api_request', source_id=source_id)])
                self.assertEqual(result[0]['source_id'], source_id)
            result = validate_definition([node(1, 'http', source_type='', source_id='')])
            self.assertIsNone(result[0]['source_type'])
            self.assertIsNone(result[0]['source_id'])

    def test_scenario_reference_requires_scenario_source_and_else_has_no_source(self):
        for changes in ({'source_type': None, 'source_id': None}, {'source_type': 'api_request'}, {'source_id': 42}):
            with self.subTest(changes=changes), self.assertRaises(StepValidationError):
                validate_definition([node(1, 'scenario_ref', **changes)])
        steps = paired_tree()
        steps[2].update(source_type='scenario_case', source_id='42')
        with self.assertRaises(StepValidationError):
            validate_definition(steps)

    def test_schema_without_resolver_does_not_resolve_or_assert_target_permissions(self):
        result = validate_definition([node(1, 'scenario_ref', source_id='unverified-target')])
        self.assertEqual(result[0]['source_id'], 'unverified-target')
        self.assertEqual(set(result[0]), NODE_FIELDS)
        with self.assertRaises(StepValidationError):
            validate_definition([], source_resolver=True)

    def test_resolver_receives_only_controlled_type_and_id_and_propagates_denial(self):
        resolver = Mock(return_value=object())
        validate_definition([node(1, 'scenario_ref')], source_resolver=resolver)
        resolver.assert_called_once_with('scenario_case', '42')
        denial = PermissionError('跨项目或无权限')
        resolver.side_effect = denial
        with self.assertRaises(PermissionError) as raised:
            validate_definition([node(1, 'scenario_ref')], source_resolver=resolver)
        self.assertIs(raised.exception, denial)
        for target in (None, False):
            with self.subTest(target=target), self.assertRaises(StepValidationError):
                validate_definition([node(1, 'scenario_ref')], source_resolver=lambda *args: target)

    def test_resolver_runs_after_structure_and_allows_repeated_sources(self):
        resolver = Mock(return_value=object())
        with self.assertRaises(StepValidationError):
            validate_definition([node(1, 'scenario_ref', parent=uid(99))], source_resolver=resolver)
        resolver.assert_not_called()
        context, _ = request_dto_mock()
        with context:
            steps = [node(1, 'http', source_type='api_request', source_id='7'),
                     node(2, 'http', source_type='api_request', source_id='7')]
            result = validate_definition(steps, source_resolver=resolver)
        self.assertEqual(len(result), 2)
        self.assertEqual(resolver.call_count, 2)

    def test_if_can_stand_alone_and_else_can_have_its_own_children(self):
        self.assertEqual(len(validate_definition([node(1, 'condition')])), 1)
        result = validate_definition(paired_tree())
        self.assertEqual(result[1]['parent'], result[0]['id'])
        self.assertEqual(result[3]['parent'], result[2]['id'])
        self.assertEqual(result[2]['config']['if_step_id'], result[0]['id'])

    def test_else_rejects_standalone_missing_nonif_and_unknown_config(self):
        cases = [
            [node(2, 'else')], [node(1), node(2, 'else')],
            [node(1, 'condition'), node(2, 'else', config={})],
            [node(1, 'condition'), node(2, 'else', config={'if_step_id': 'bad'})],
            [node(1, 'condition'), node(2, 'else', config={'if_step_id': uid(1), 'branch': 'else'})],
        ]
        for steps in cases:
            with self.subTest(steps=steps), self.assertRaises(StepValidationError):
                validate_definition(steps)

    def test_else_rejects_cross_parent_nonadjacent_reversed_and_duplicate_pairs(self):
        cases = [
            [node(1, 'condition'), node(2), node(3, 'else', parent=uid(2))],
            [node(1, 'condition'), node(2, 'wait'), node(3, 'else')],
            [node(1, 'condition', position=2), node(2, 'else', position=0)],
            [node(1, 'condition'), node(2, 'else'), node(3, 'else')],
        ]
        for steps in cases:
            with self.subTest(steps=steps), self.assertRaises(StepValidationError):
                validate_definition(steps)

    def test_else_enabled_matches_if_and_uuid_reference_is_canonical(self):
        for enabled in (False, True):
            steps = [node(255, 'condition', enabled=enabled),
                     node(256, 'else', enabled=enabled, config={'if_step_id': uid(255).upper()})]
            result = validate_definition(steps)
            self.assertEqual(result[1]['config']['if_step_id'], uid(255))
            steps[1]['enabled'] = not enabled
            with self.assertRaisesRegex(StepValidationError, 'enabled'):
                validate_definition(steps)


class CloneDefinitionTests(unittest.TestCase):
    def test_clone_definition_changes_all_ids_preserves_sources_names_and_original(self):
        steps = paired_tree() + [node(6, 'scenario_ref', config={'input_mapping': {'nested': [1, 2]}})]
        before = deepcopy(steps)
        normalized = validate_definition(steps)
        result = clone_definition(steps)
        self.assertEqual(steps, before)
        self.assertEqual(len(result), len(steps))
        self.assertTrue({item['id'] for item in normalized}.isdisjoint({item['id'] for item in result}))
        self.assertEqual(len({item['id'] for item in result}), len(steps))
        for old, new in zip(normalized, result):
            self.assertEqual(old['name'], new['name'])
            self.assertEqual(old['source_id'], new['source_id'])
        result[-1]['config']['input_mapping']['nested'].append(3)
        self.assertEqual(steps, before)

    def test_clone_if_carries_else_and_both_subtrees_and_rewrites_pair(self):
        steps = paired_tree()
        result = clone_subtree(steps, uid(1))
        self.assertEqual([item['type'] for item in result], ['condition', 'wait', 'else', 'script'])
        self.assertIsNone(result[0]['parent'])
        self.assertIsNone(result[2]['parent'])
        self.assertEqual(result[1]['parent'], result[0]['id'])
        self.assertEqual(result[3]['parent'], result[2]['id'])
        self.assertEqual(result[2]['config']['if_step_id'], result[0]['id'])
        self.assertEqual([result[0]['position'], result[2]['position']], [0, 1])

    def test_clone_else_also_carries_if_and_both_subtrees(self):
        result = clone_subtree(paired_tree(), uid(3))
        self.assertEqual([item['type'] for item in result], ['condition', 'wait', 'else', 'script'])
        self.assertEqual(result[2]['config']['if_step_id'], result[0]['id'])

    def test_clone_nested_container_includes_nested_pairs_and_detaches_external_parent(self):
        steps = [node(10)] + [dict(item, parent=uid(10)) if item['parent'] is None else item for item in paired_tree()]
        result = clone_subtree(steps, uid(10))
        self.assertEqual(len(result), len(steps))
        self.assertIsNone(result[0]['parent'])
        cloned_if = next(item for item in result if item['type'] == 'condition')
        cloned_else = next(item for item in result if item['type'] == 'else')
        self.assertEqual(cloned_else['config']['if_step_id'], cloned_if['id'])
        self.assertEqual(cloned_if['parent'], result[0]['id'])
        result = clone_subtree(steps, uid(1))
        self.assertEqual(len(result), 4)
        self.assertIsNone(result[0]['parent'])

    def test_clone_rewrites_stable_uuid_references_in_nested_values_keys_and_code(self):
        ref = f'{{{{steps.{uid(2).upper()}.response.body}}}}'
        steps = [node(1), node(2, 'wait', parent=uid(1)),
                 node(3, 'scenario_ref', parent=uid(1),
                      config={'input_mapping': {ref: [{'x': f'prefix {ref} suffix'}]},
                              'output_mapping': {'x': f'{{{{steps.{UUID(uid(2)).hex}.response}}}}'}}),
                 node(4, 'script', parent=uid(1),
                      config={'language': 'javascript', 'code': f'const x = steps.{uid(2)}.response;'})]
        before = deepcopy(steps)
        result = clone_subtree(steps, uid(1))
        new_wait = result[1]['id']
        new_ref = f'{{{{steps.{new_wait}.response.body}}}}'
        mapping = result[2]['config']['input_mapping']
        self.assertEqual(mapping, {new_ref: [{'x': f'prefix {new_ref} suffix'}]})
        self.assertEqual(result[2]['config']['output_mapping']['x'], f'{{{{steps.{new_wait}.response}}}}')
        self.assertEqual(result[3]['config']['code'], f'const x = steps.{new_wait}.response;')
        self.assertEqual(steps, before)

    def test_clone_rewrites_bare_and_quoted_uuid_references(self):
        steps = [node(255, 'wait'), node(256, 'script',
                 config={'language': 'javascript', 'code': f'"steps.{uid(255).upper()}"; steps.{uid(255)};'})]
        result = clone_definition(steps)
        new_id = result[0]['id']
        self.assertEqual(result[1]['config']['code'], f'"steps.{new_id}"; steps.{new_id};')

    def test_clone_rejects_ordinal_or_malformed_step_references(self):
        for reference in ('{{steps.1.response}}', '{{steps.not_a_uuid.response}}'):
            steps = [node(1, 'scenario_ref', config={'input_mapping': {'x': reference}})]
            with self.subTest(reference=reference), self.assertRaisesRegex(StepValidationError, '稳定 UUID'):
                clone_definition(steps)

    def test_clone_does_not_rewrite_source_uuid_or_unrelated_uuid_literals(self):
        steps = [node(1), node(2, 'scenario_ref', parent=uid(1), source_id=uid(1),
                               config={'input_mapping': {'literal': uid(1), 'var': '{{vars.token}}'}})]
        result = clone_subtree(steps, uid(1))
        self.assertEqual(result[1]['source_id'], uid(1))
        self.assertEqual(result[1]['config']['input_mapping']['literal'], uid(1))
        self.assertEqual(result[1]['config']['input_mapping']['var'], '{{vars.token}}')

    def test_clone_rejects_external_references_with_explicit_sorted_ids(self):
        steps = [node(1), node(2, 'script', parent=uid(1),
                 config={'language': 'javascript', 'code': f'steps.{uid(5)}.response; steps.{uid(4)}.response;'}),
                 node(4, 'wait'), node(5, 'wait')]
        before = deepcopy(steps)
        with self.assertRaises(ExternalStepReferenceError) as raised:
            clone_subtree(steps, uid(1))
        self.assertEqual(raised.exception.references, (uid(4), uid(5)))
        self.assertIn(uid(4), str(raised.exception))
        self.assertEqual(steps, before)
        self.assertEqual(len(clone_definition(steps)), len(steps))

    def test_clone_whole_definition_rejects_dangling_references(self):
        steps = [node(1, 'scenario_ref', config={'input_mapping': {'x': f'{{{{steps.{uid(99)}.response}}}}'}})]
        with self.assertRaises(ExternalStepReferenceError) as raised:
            clone_definition(steps)
        self.assertEqual(raised.exception.references, (uid(99),))

    def test_clone_missing_step_empty_definition_and_lonely_if(self):
        self.assertEqual(clone_definition([]), [])
        with self.assertRaises(StepValidationError):
            clone_subtree([node(1)], uid(99))
        with self.assertRaises(StepValidationError):
            clone_subtree([], 'not-uuid')
        result = clone_subtree([node(1, 'condition')], uid(1))
        self.assertEqual(len(result), 1)
        self.assertNotEqual(result[0]['id'], uid(1))

    def test_clone_validates_sources_only_for_selected_clones_and_propagates_denial(self):
        resolver = Mock(return_value=object())
        steps = [node(1), node(2, 'scenario_ref', parent=uid(1)), node(3, 'scenario_ref', source_id='other')]
        clone_subtree(steps, uid(1), source_resolver=resolver)
        resolver.assert_called_once_with('scenario_case', '42')
        resolver.side_effect = PermissionError('来源无权限')
        with self.assertRaises(PermissionError):
            clone_definition(steps, source_resolver=resolver)

    def test_clone_http_rewrites_request_variable_without_touching_name_or_source(self):
        context, _ = request_dto_mock()
        with context:
            steps = [node(1, 'wait'), node(2, 'http', name='custom', source_type='api_request', source_id='7',
                                         config={'request': {'method': 'GET', 'url': f'/items/{{{{steps.{uid(1)}.response.body.id}}}}'}})]
            result = clone_definition(steps)
        self.assertEqual(result[1]['config']['request']['url'], f'/items/{{{{steps.{result[0]["id"]}.response.body.id}}}}')
        self.assertEqual(result[1]['name'], 'custom')
        self.assertEqual(result[1]['source_id'], '7')


if __name__ == '__main__':
    unittest.main()
