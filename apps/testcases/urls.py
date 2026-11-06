from django.urls import path
from . import views

urlpatterns = [
    # 测试用例相关
    path('modules/', views.TestCaseModuleListCreateView.as_view(), name='testcase-module-list'),
    path('modules/reorder/', views.TestCaseModuleReorderView.as_view(), name='testcase-module-reorder'),
    path('modules/<int:pk>/', views.TestCaseModuleDetailView.as_view(), name='testcase-module-detail'),
    path('move/', views.TestCaseMoveView.as_view(), name='testcase-move'),
    path('', views.TestCaseListCreateView.as_view(), name='testcase-list'),
    path('<int:pk>/automation-options/', views.TestCaseAutomationOptionsView.as_view(), name='testcase-automation-options'),
    path('<int:pk>/automation-links/', views.TestCaseAutomationLinkView.as_view(), name='testcase-automation-links'),
    path('<int:pk>/automation-links/<int:link_id>/', views.TestCaseAutomationLinkView.as_view(), name='testcase-automation-link-detail'),
    path('import/template/', views.TestCaseImportTemplateDownloadView.as_view(), name='testcase-import-template'),
    path('import-records/', views.TestCaseImportRecordListCreateView.as_view(), name='testcase-import-record-list'),
    path('import-records/<int:pk>/', views.TestCaseImportRecordDetailView.as_view(), name='testcase-import-record-detail'),
    path('import-records/<int:pk>/failure-report/', views.TestCaseImportFailureReportDownloadView.as_view(), name='testcase-import-record-failure-report'),
    path('<int:pk>/notes-image/', views.TestCaseNotesImageUploadView.as_view(), name='testcase-notes-image-upload'),
    path('<int:pk>/', views.TestCaseDetailView.as_view(), name='testcase-detail'),
]
