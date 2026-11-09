import unittest

from ..path_params import resolve_path_parameters


class ResolvePathParametersTests(unittest.TestCase):
    def test_replaces_single_brace_path_parameter_and_removes_it_from_query(self):
        url, params = resolve_path_parameters('/promotions/{id}', {'id': 'a/b', 'page': '2'})

        self.assertEqual(url, '/promotions/a%2Fb')
        self.assertEqual(params, {'page': '2'})

    def test_keeps_unfilled_path_parameter_out_of_query(self):
        url, params = resolve_path_parameters('/promotions/{id}', {'id': ''})

        self.assertEqual(url, '/promotions/{id}')
        self.assertEqual(params, {})

    def test_does_not_treat_double_braces_as_path_parameters(self):
        url, params = resolve_path_parameters('/promotions/{{id}}', {'id': '123'})

        self.assertEqual(url, '/promotions/{{id}}')
        self.assertEqual(params, {'id': '123'})


if __name__ == '__main__':
    unittest.main()
