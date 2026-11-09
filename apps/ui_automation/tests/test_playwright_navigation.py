from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..playwright_engine import PlaywrightTestEngine


class PlaywrightNavigationWaitTests(SimpleTestCase):
    def test_navigation_succeeds_when_network_never_becomes_idle(self):
        engine = PlaywrightTestEngine()
        engine.page = SimpleNamespace(
            goto=AsyncMock(),
            wait_for_load_state=AsyncMock(side_effect=PlaywrightTimeout('still active')),
        )

        with patch('apps.ui_automation.playwright_engine.asyncio.sleep', new_callable=AsyncMock):
            success, log = self.async_to_sync(engine.navigate('https://example.test/'))

        self.assertTrue(success)
        self.assertIn('domcontentloaded（networkidle 未达成）', log)
        engine.page.goto.assert_awaited_once_with(
            'https://example.test/', wait_until='domcontentloaded', timeout=30000
        )

    def async_to_sync(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)
