from django.apps import AppConfig


class TestcasesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.testcases'
    verbose_name = '测试用例'

    def ready(self):
        from . import signals  # noqa: F401
