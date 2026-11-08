from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TestPlanViewSet, TestRunViewSet, TestRunCaseViewSet

router = DefaultRouter()
router.register(r'plans', TestPlanViewSet)
router.register(r'runs', TestRunViewSet)
router.register(r'run_cases', TestRunCaseViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
