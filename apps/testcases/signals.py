from django.db.models.signals import pre_delete

from apps.api_testing.models import ScenarioCase
from apps.ui_automation.models import TestCase as UiTestCase

from .models import TestCaseAutomationLink


def clear_ui_automation_links(sender, instance, **kwargs):
    """Remove formal-case links when a UI automation target is deleted."""
    TestCaseAutomationLink.objects.filter(
        case_type='ui_test_case',
        object_id=instance.pk,
    ).delete()


def clear_api_scenario_links(sender, instance, **kwargs):
    """Remove formal-case links when an API scenario target is deleted."""
    TestCaseAutomationLink.objects.filter(
        case_type='api_scenario_case',
        object_id=instance.pk,
    ).delete()


pre_delete.connect(
    clear_ui_automation_links,
    sender=UiTestCase,
    dispatch_uid='testcases.clear_ui_automation_links',
)
pre_delete.connect(
    clear_api_scenario_links,
    sender=ScenarioCase,
    dispatch_uid='testcases.clear_api_scenario_links',
)
