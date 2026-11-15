"""Namespaced variable resolution tests."""

from django.test import SimpleTestCase

from ..scenario_variables import (
    MISSING,
    VariableResolutionError,
    VariableScope,
    navigate,
    resolve_value,
)


class NavigateTests(SimpleTestCase):
    def test_walks_dicts_and_list_indexes(self):
        data = {'data': {'items': [{'id': 7}, {'id': 9}]}}
        self.assertEqual(navigate(data, 'data.items[1].id'), 9)
        self.assertEqual(navigate(data, ''), data)

    def test_returns_missing_instead_of_raising(self):
        self.assertIs(navigate({'a': 1}, 'b'), MISSING)
        self.assertIs(navigate({'a': [1]}, 'a[4]'), MISSING)
        self.assertIs(navigate(None, 'a'), MISSING)


class ScopeLookupTests(SimpleTestCase):
    def setUp(self):
        self.scope = VariableScope(
            environment={'tenant': 'acme', 'nested': {'flag': True}},
            variables={'token': 'abc'},
        )

    def test_namespaced_lookup(self):
        self.assertEqual(self.scope.require('env.tenant'), 'acme')
        self.assertEqual(self.scope.require('env.nested.flag'), True)
        self.assertEqual(self.scope.require('vars.token'), 'abc')

    def test_legacy_bare_name_prefers_environment_then_variables(self):
        self.assertEqual(self.scope.require('tenant'), 'acme')
        self.assertEqual(self.scope.require('token'), 'abc')

    def test_missing_placeholder_raises(self):
        with self.assertRaises(VariableResolutionError):
            self.scope.require('env.absent')
        with self.assertRaises(VariableResolutionError):
            self.scope.resolve_string('prefix-{{ env.absent }}')

    def test_whole_placeholder_keeps_native_type(self):
        self.scope.variables['list'] = [1, 2, 3]
        self.scope.variables['number'] = 42
        self.assertEqual(self.scope.resolve_string('{{ vars.list }}'), [1, 2, 3])
        self.assertEqual(self.scope.resolve_string('{{vars.number}}'), 42)

    def test_embedded_placeholder_renders_text(self):
        self.scope.variables['list'] = [1, 2]
        self.assertEqual(self.scope.resolve_string('v={{ vars.list }}'),
                         'v=[1, 2]')
        self.assertEqual(self.scope.resolve_string('{{ env.tenant }}/x'), 'acme/x')

    def test_resolve_value_walks_nested_structures(self):
        draft = {'url': '/{{ env.tenant }}/users', 'headers': [{'value': '{{ vars.token }}'}],
                 'body': {'type': 'json', 'data': {'keep': 1, 'name': '{{ vars.token }}'}}}
        resolved = resolve_value(draft, self.scope)
        self.assertEqual(resolved['url'], '/acme/users')
        self.assertEqual(resolved['headers'][0]['value'], 'abc')
        self.assertEqual(resolved['body']['data'], {'keep': 1, 'name': 'abc'})


class StepOutputTests(SimpleTestCase):
    def test_step_output_is_addressable_by_uuid(self):
        scope = VariableScope()
        scope.record_output('step-1', {'response': {'body': {'data': {'id': 5}}}})
        self.assertEqual(scope.require('steps.step-1.response.body.data.id'), 5)

    def test_missing_step_output_raises(self):
        with self.assertRaises(VariableResolutionError):
            VariableScope().require('steps.absent.response.body')

    def test_nested_call_sees_parent_outputs_but_not_siblings(self):
        root = VariableScope()
        root.record_output('outer', {'value': 1})
        child = root.spawn('ref')
        child.record_output('inner', {'value': 2})
        # The referenced scenario sees the caller's output through the ancestor path.
        self.assertEqual(child.require('steps.outer.value'), 1)
        self.assertEqual(child.require('steps.inner.value'), 2)
        # The caller must not read an output that belongs to the nested call:
        # a referenced scenario exports values through output_mapping only.
        with self.assertRaises(VariableResolutionError):
            root.require('steps.inner.value')

    def test_loop_frame_exposes_item_and_index(self):
        scope = VariableScope()
        scope.push_loop({'index': 2, 'item': {'id': 'x'}})
        try:
            self.assertEqual(scope.require('loop.index'), 2)
            self.assertEqual(scope.require('loop.item.id'), 'x')
        finally:
            scope.pop_loop()
        with self.assertRaises(VariableResolutionError):
            scope.require('loop.index')

    def test_spawn_isolates_variables_and_output_addressing(self):
        root = VariableScope(variables={'token': 'outer'})
        child = root.spawn('ref', variables={'token': 'inner'})
        self.assertEqual(child.require('vars.token'), 'inner')
        child.record_output('s', {'value': 1})
        self.assertEqual(child.require('steps.s.value'), 1)
        # Outputs live in one store but are addressed per call path, so the same
        # step id used twice does not leak across invocations.
        with self.assertRaises(VariableResolutionError):
            root.require('steps.s.value')
        self.assertEqual(root.require('vars.token'), 'outer')
