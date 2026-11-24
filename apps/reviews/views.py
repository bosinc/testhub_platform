from rest_framework import viewsets, status, permissions
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from . import services
from .models import TestCaseReview, ReviewAssignment, TestCaseReviewComment, ReviewTemplate
from .serializers import (
    TestCaseReviewSerializer, TestCaseReviewListSerializer, TestCaseReviewCreateSerializer,
    ReviewAssignmentSerializer, TestCaseReviewCommentSerializer, 
    TestCaseReviewCommentCreateSerializer,
    ReviewTemplateSerializer, ReviewTemplateCreateSerializer
)
from apps.testcases.models import TestCase


def _enforce(rule, *args):
    """执行 services 里的规则，把业务错误转成接口错误：无权限 403，其余 400。"""
    try:
        return rule(*args)
    except services.ReviewPermissionError as exc:
        raise PermissionDenied(str(exc))
    except services.ReviewRuleError as exc:
        raise ValidationError({'detail': str(exc)})


class ReviewPagination(PageNumberPagination):
    """评审列表分页：默认 20 条/页，允许前端用 page_size 调整，上限与页面可选的最大条数一致。"""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class TestCaseReviewViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ReviewPagination

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return TestCaseReviewCreateSerializer
        if self.action == 'list':
            return TestCaseReviewListSerializer
        return TestCaseReviewSerializer

    def get_queryset(self):
        queryset = TestCaseReview.objects.select_related('creator')
        if self.action == 'list':
            # 列表只展示摘要：用例数用子查询统计，不加载用例、评审意见和模板，
            # 子查询也不受下面 project/reviewer 过滤产生的 JOIN 影响。
            testcase_links = TestCaseReview.testcases.through.objects.filter(
                testcasereview_id=OuterRef('pk')
            ).order_by().values('testcasereview_id').annotate(total=Count('pk')).values('total')
            queryset = queryset.prefetch_related(
                'projects', 'reviewassignment_set__reviewer'
            ).annotate(
                testcase_count=Coalesce(Subquery(testcase_links, output_field=IntegerField()), 0)
            )
        elif self.action == 'retrieve':
            queryset = services.review_detail_queryset()
        # 其余写操作不序列化关联数据，无需预取。

        # 过滤参数
        project_id = self.request.query_params.get('project', None)
        status_param = self.request.query_params.get('status', None)
        reviewer_id = self.request.query_params.get('reviewer', None)
        
        if project_id:
            queryset = queryset.filter(projects__id=project_id)
        if status_param:
            queryset = queryset.filter(status=status_param)
        if reviewer_id:
            queryset = queryset.filter(reviewers__id=reviewer_id)
            
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)
    
    @action(detail=True, methods=['post'])
    def assign_reviewers(self, request, pk=None):
        """分配评审人员"""
        review = self.get_object()
        # 只追加不移除；已在名单里的评审人保持原有分配与结论
        services.sync_reviewers(
            review, request.data.get('reviewer_ids', []), remove_missing=False)

        return Response({'message': '评审人员分配成功'})
    
    @action(detail=True, methods=['post'])
    def submit_review(self, request, pk=None):
        """提交评审结果"""
        review = self.get_object()
        try:
            assignment = ReviewAssignment.objects.get(
                review=review, 
                reviewer=request.user
            )
            assignment.status = request.data.get('status', 'approved')
            assignment.comment = request.data.get('comment', '')
            assignment.checklist_results = request.data.get('checklist_results', {})
            assignment.reviewed_at = timezone.now()
            assignment.save()
            
            # 检查是否所有评审人都已完成评审
            pending_count = ReviewAssignment.objects.filter(
                review=review, 
                status='pending'
            ).count()
            
            if pending_count == 0:
                # 检查评审结果，如果所有人都通过则设为已通过
                approved_count = ReviewAssignment.objects.filter(
                    review=review, 
                    status='approved'
                ).count()
                total_count = ReviewAssignment.objects.filter(review=review).count()
                
                if approved_count == total_count:
                    review.status = 'approved'
                else:
                    review.status = 'rejected'
                    
                review.completed_at = timezone.now()
                review.save()
                
            return Response({'message': '评审提交成功'})
            
        except ReviewAssignment.DoesNotExist:
            return Response(
                {'error': '您未被分配为此评审的评审人'}, 
                status=status.HTTP_403_FORBIDDEN
            )
    
    @action(detail=False, methods=['get'])
    def my_reviews(self, request):
        """获取我的评审任务"""
        reviews = services.review_detail_queryset().filter(reviewers=request.user)

        serializer = self.get_serializer(reviews, many=True)
        return Response(serializer.data)


class TestCaseReviewCommentViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return TestCaseReviewCommentCreateSerializer
        return TestCaseReviewCommentSerializer
    
    def get_queryset(self):
        review_id = self.request.query_params.get('review', None)
        testcase_id = self.request.query_params.get('testcase', None)
        
        queryset = TestCaseReviewComment.objects.select_related('author', 'testcase', 'review')
        
        if review_id:
            queryset = queryset.filter(review_id=review_id)
        if testcase_id:
            queryset = queryset.filter(testcase_id=testcase_id)
            
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def perform_update(self, serializer):
        # 评审意见只能由作者本人修改，避免仅依赖前端按钮做权限控制。
        if not services.is_comment_author(serializer.instance, self.request.user):
            raise PermissionDenied('只能修改本人发布的评审意见')
        serializer.save()

    def perform_destroy(self, instance):
        # 评审意见只能由作者本人删除。
        if not services.is_comment_author(instance, self.request.user):
            raise PermissionDenied('只能删除本人发布的评审意见')
        # 评审结束后意见冻结，作者本人也不能再删除。
        _enforce(services.check_review_open, instance.review)
        instance.delete()

    @action(detail=True, methods=['patch'])
    def update_status(self, request, pk=None):
        """修改评审意见的解决状态：意见作者或评审创建人可改，评审结束后冻结。"""
        comment = self.get_object()
        is_resolved = request.data.get('is_resolved')
        if not isinstance(is_resolved, bool):
            raise ValidationError({'is_resolved': ['必须是布尔值 true / false']})
        _enforce(services.set_comment_resolved, comment, request.user, is_resolved)
        return Response(TestCaseReviewCommentSerializer(comment).data)


class ReviewTemplateViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return ReviewTemplateCreateSerializer
        return ReviewTemplateSerializer
    
    def get_queryset(self):
        project_id = self.request.query_params.get('project', None)
        queryset = ReviewTemplate.objects.select_related('creator').prefetch_related('project', 'default_reviewers')
        
        if project_id:
            queryset = queryset.filter(project__id=project_id)
            
        return queryset.filter(is_active=True)
    
    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)
