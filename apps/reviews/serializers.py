from rest_framework import serializers
from . import services
from .models import TestCaseReview, ReviewAssignment, TestCaseReviewComment, ReviewTemplate


# 简化的序列化器，避免循环导入
class SimpleUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    # 评审意见列表需要使用作者头像，保持与用户接口相同的 URL 序列化行为。
    avatar = serializers.ImageField(read_only=True, allow_null=True)


class SimpleProjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()


class SimpleTestCaseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    test_type = serializers.CharField()
    priority = serializers.CharField()
    status = serializers.CharField()
    author = SimpleUserSerializer()


class ReviewAssignmentSerializer(serializers.ModelSerializer):
    reviewer = SimpleUserSerializer(read_only=True)
    
    class Meta:
        model = ReviewAssignment
        fields = ['id', 'reviewer', 'status', 'comment', 'checklist_results', 'reviewed_at', 'assigned_at']


class TestCaseReviewCommentSerializer(serializers.ModelSerializer):
    author = SimpleUserSerializer(read_only=True)
    testcase = SimpleTestCaseSerializer(read_only=True)
    
    class Meta:
        model = TestCaseReviewComment
        fields = ['id', 'testcase', 'author', 'comment_type', 'content', 'step_number', 
                 'is_resolved', 'created_at', 'updated_at']


class TestCaseReviewCommentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCaseReviewComment
        fields = ['review', 'testcase', 'comment_type', 'content', 'step_number']
        extra_kwargs = {
            'testcase': {'required': False, 'allow_null': True}
        }

    def validate(self, attrs):
        # 意见与评审、用例的关联规则；页面接口与 MCP 工具都经本序列化器写入
        instance = self.instance
        review = attrs.get('review') or (instance.review if instance else None)
        if instance is not None and review.pk != instance.review_id:
            raise serializers.ValidationError({'review': '评审意见不能移动到其他评审'})
        # 评审结束后意见冻结：不能再新增，也不能再修改
        try:
            services.check_review_open(review)
        except services.ReviewRuleError as exc:
            raise serializers.ValidationError({'review': str(exc)})

        comment_type = attrs.get('comment_type') or (instance.comment_type if instance else 'general')
        if comment_type == 'general':
            if attrs.get('testcase') is not None:
                raise serializers.ValidationError({'testcase': '整体意见不能关联用例'})
            # 从用例意见改成整体意见时，一并解除原来的用例关联
            attrs['testcase'] = None
            return attrs

        if 'testcase' in attrs:
            testcase = attrs['testcase']
        else:
            testcase = instance.testcase if instance else None
        if testcase is None:
            raise serializers.ValidationError({'testcase': '用例意见、步骤意见必须关联用例'})
        # 关联用例没变时不再校验归属：用例事后被移出评审，不应导致原意见无法修改
        unchanged = instance is not None and instance.testcase_id == testcase.pk
        if not unchanged and not review.testcases.filter(pk=testcase.pk).exists():
            raise serializers.ValidationError({'testcase': '该用例不属于当前评审'})
        return attrs


class ReviewTemplateSerializer(serializers.ModelSerializer):
    creator = SimpleUserSerializer(read_only=True)
    project = SimpleProjectSerializer(many=True, read_only=True)
    default_reviewers = SimpleUserSerializer(many=True, read_only=True)
    
    class Meta:
        model = ReviewTemplate
        fields = ['id', 'name', 'description', 'project', 'creator', 'checklist',
                 'default_reviewers', 'is_active', 'created_at', 'updated_at']


class TestCaseReviewSerializer(serializers.ModelSerializer):
    creator = SimpleUserSerializer(read_only=True)
    projects = SimpleProjectSerializer(many=True, read_only=True)
    testcases = SimpleTestCaseSerializer(many=True, read_only=True)
    template = ReviewTemplateSerializer(read_only=True)
    assignments = ReviewAssignmentSerializer(source='reviewassignment_set', many=True, read_only=True)
    comments = TestCaseReviewCommentSerializer(many=True, read_only=True)
    
    class Meta:
        model = TestCaseReview
        fields = ['id', 'title', 'description', 'projects', 'testcases', 'creator', 
                 'template', 'status', 'priority', 'deadline', 'created_at', 'updated_at', 
                 'completed_at', 'assignments', 'comments']


class TestCaseReviewListSerializer(serializers.ModelSerializer):
    """评审列表专用：不返回用例明细、评审意见和模板，用例只返回数量。"""
    creator = SimpleUserSerializer(read_only=True)
    projects = SimpleProjectSerializer(many=True, read_only=True)
    assignments = ReviewAssignmentSerializer(source='reviewassignment_set', many=True, read_only=True)
    # 由视图 queryset 注解提供，避免为统计数量加载全部用例。
    testcase_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TestCaseReview
        fields = ['id', 'title', 'description', 'projects', 'testcase_count', 'creator',
                 'status', 'priority', 'deadline', 'created_at', 'updated_at',
                 'completed_at', 'assignments']


# 创建和更新用的简化序列化器
class TestCaseReviewCreateSerializer(serializers.ModelSerializer):
    testcases = serializers.ListField(child=serializers.IntegerField(), write_only=True)
    reviewers = serializers.ListField(child=serializers.IntegerField(), write_only=True)
    projects = serializers.ListField(child=serializers.IntegerField(), write_only=True)
    template = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    
    class Meta:
        model = TestCaseReview
        fields = ['title', 'description', 'projects', 'priority', 'deadline', 'testcases', 'reviewers', 'template']

    # 落库统一走 services（与 MCP 工具共用）：关系字段是 write_only 的 ID 列表，
    # 不能交给默认的 create / update 当普通属性处理
    def create(self, validated_data):
        project_ids = validated_data.pop('projects', [])
        testcase_ids = validated_data.pop('testcases', [])
        reviewer_ids = validated_data.pop('reviewers', [])
        template_id = validated_data.pop('template', None)
        creator = validated_data.pop('creator')
        return services.create_review(
            creator=creator,
            fields=validated_data,
            project_ids=project_ids,
            testcase_ids=testcase_ids,
            reviewer_ids=reviewer_ids,
            template_id=template_id,
        )

    def update(self, instance, validated_data):
        # 请求里没带的关系字段保持原样；template 显式传 null / 0 时清空模板
        project_ids = validated_data.pop('projects', None)
        testcase_ids = validated_data.pop('testcases', None)
        reviewer_ids = validated_data.pop('reviewers', None)
        template_id = validated_data.pop('template', services.UNSET)
        return services.update_review(
            instance,
            fields=validated_data,
            project_ids=project_ids,
            testcase_ids=testcase_ids,
            reviewer_ids=reviewer_ids,
            template_id=template_id,
        )


class ReviewTemplateCreateSerializer(serializers.ModelSerializer):
    default_reviewers = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)
    project = serializers.ListField(child=serializers.IntegerField(), write_only=True)
    
    class Meta:
        model = ReviewTemplate
        fields = ['name', 'description', 'project', 'checklist', 'default_reviewers']
    
    def create(self, validated_data):
        default_reviewers_ids = validated_data.pop('default_reviewers', [])
        project_ids = validated_data.pop('project', [])
        template = ReviewTemplate.objects.create(**validated_data)
        
        # 添加项目关联
        if project_ids:
            template.project.set(project_ids)
        
        if default_reviewers_ids:
            template.default_reviewers.set(default_reviewers_ids)
        
        return template
