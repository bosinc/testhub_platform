import unittest

from ..recording_sync import UnsupportedRecordingCode, generate_code, parse_recording


class RecordingSyncTests(unittest.TestCase):
    def test_parses_codegen_navigation_locators_assertions_and_waits(self):
        source = '''from playwright.sync_api import sync_playwright, expect

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context()
    page = context.new_page()
    page.goto("http://localhost:3000")
    page.get_by_role("textbox", name="用户名", exact=True).fill("demo")
    page.get_by_role("button", name="登录").click()
    expect(page.get_by_text("欢迎")).to_be_visible()
    page.wait_for_timeout(500)
'''
        steps = parse_recording(source)
        self.assertEqual([step['action_type'] for step in steps], ['navigate', 'fill', 'click', 'assert', 'wait'])
        self.assertEqual(steps[1]['action_params']['locator']['options'], {'name': '用户名', 'exact': True})
        self.assertEqual(steps[-1]['wait_time'], 500)

    def test_generates_code_from_structured_steps(self):
        code = generate_code([{
            'action_type': 'fill',
            'input_value': 'demo',
            'action_params': {'locator': {'strategy': 'role', 'value': 'textbox', 'options': {'name': '用户名', 'exact': True}}},
        }])
        self.assertIn('page.get_by_role(\'textbox\', name=\'用户名\', exact=True).fill(\'demo\')', code)

    def test_step_comments_round_trip_without_metadata(self):
        source = '''def run(page):
    # 点击登录按钮
    page.get_by_role("button", name="登录").click()
'''
        step = parse_recording(source)[0]
        self.assertEqual(step['action_type'], 'click')
        self.assertEqual(step['description'], '点击登录按钮')

        generated = generate_code([step])
        self.assertIn('# 点击登录按钮', generated)
        self.assertNotIn('testhub-step', generated)

    def test_empty_description_generates_plain_step_marker(self):
        generated = generate_code([{
            'action_type': 'click',
            'action_params': {'locator': {'strategy': 'role', 'value': 'button'}},
        }])
        self.assertIn('    #\n    page.get_by_role', generated)

    def test_non_locator_actions_include_their_values_in_descriptions(self):
        source = '''def run(page, context):
    page.goto("https://example.test/login")
    page.wait_for_timeout(1000)
    page = context.pages[1]
'''
        steps = parse_recording(source)
        self.assertEqual(steps[0]['description'], '跳转URL“https://example.test/login”')
        self.assertEqual(steps[1]['description'], '等待1000ms')
        self.assertEqual(steps[2]['description'], '切换到标签页“1”')

    def test_reports_unsupported_statement_line(self):
        with self.assertRaises(UnsupportedRecordingCode) as error:
            parse_recording('def run(playwright):\n    page.evaluate("alert(1)")\n')
        self.assertEqual(error.exception.line, 2)

    def test_ignores_storage_state_export_from_recording(self):
        source = '''def run(playwright):
    context = playwright.chromium.launch().new_context()
    page = context.new_page()
    page.goto("https://example.test")
    context.storage_state(path="storage_state.json")
    context.close()
'''

        steps = parse_recording(source)

        self.assertEqual([step['action_type'] for step in steps], ['navigate', 'storageState'])
        self.assertEqual(steps[-1]['action_params'], {
            'operation': 'save', 'mode': 'save_as', 'name': 'storage_state', 'storage_state_id': None,
        })

    def test_generates_storage_state_code_from_manual_action_params(self):
        code = generate_code([{
            'action_type': 'storageState',
            'action_params': {
                'operation': 'save', 'mode': 'save_as', 'name': 'logged-in', 'storage_state_id': None,
            },
        }])

        self.assertIn("context.storage_state(path='logged-in.json')", code)
