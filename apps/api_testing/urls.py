from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from .views import (
    ApiProjectViewSet, ApiCollectionViewSet, ApiRequestViewSet,
    EnvironmentViewSet, RequestHistoryViewSet, ScenarioCaseViewSet,
    ScenarioCaseRequestViewSet, TestExecutionViewSet, UserViewSet,
    ScheduledTaskViewSet, TaskExecutionLogViewSet, NotificationLogViewSet,
    TaskNotificationSettingViewSet, OperationLogViewSet,
    ApiDashboardViewSet, AIServiceConfigViewSet,
    TestSuiteViewSet, ScenarioCaseFolderViewSet
)

router = DefaultRouter()
router.register(r'dashboard', ApiDashboardViewSet, basename='dashboard')
router.register(r'projects', ApiProjectViewSet)
router.register(r'collections', ApiCollectionViewSet)
router.register(r'requests', ApiRequestViewSet)
router.register(r'environments', EnvironmentViewSet)
router.register(r'histories', RequestHistoryViewSet)
router.register(r'scenario-cases', ScenarioCaseViewSet, basename='scenario-case')
router.register(r'scenario-case-folders', ScenarioCaseFolderViewSet, basename='scenario-case-folder')
router.register(r'test-suites', TestSuiteViewSet, basename='test-suite')
router.register(r'scenario-case-requests', ScenarioCaseRequestViewSet, basename='scenario-case-request')
router.register(r'test-executions', TestExecutionViewSet)
# The plan exposes step polling under /executions/; keep the historical
# /test-executions/ route so existing pages keep working.
router.register(r'executions', TestExecutionViewSet, basename='execution')
router.register(r'users', UserViewSet)
router.register(r'scheduled-tasks', ScheduledTaskViewSet, basename='scheduledtask')
router.register(r'task-execution-logs', TaskExecutionLogViewSet, basename='taskexecutionlog')
router.register(r'notification-logs', NotificationLogViewSet)
router.register(r'task-notification-settings', TaskNotificationSettingViewSet)
router.register(r'operation-logs', OperationLogViewSet)
router.register(r'ai-service-configs', AIServiceConfigViewSet, basename='aiserviceconfig')

urlpatterns = [
    path('api-testing/', include(router.urls)),
]

# 添加媒体文件路由
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
