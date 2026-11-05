from django.http import FileResponse
from rest_framework import generics, permissions, status, pagination
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from django.db import models, transaction
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.views import APIView
from rest_framework import serializers
from django.utils import timezone

from .models import TestCase, TestCaseModule, TestCaseStep, TestCaseAttachment, TestCaseComment, TestCaseImportRecord
from .serializers import (
    TestCaseSerializer, TestCaseListSerializer, TestCaseCreateSerializer, TestCaseUpdateSerializer,
    TestCaseImportRecordListSerializer, TestCaseImportRecordDetailSerializer
)
from apps.projects.models import Project
from apps.users.models import User
from .services import TEMPLATE_VERSION, TestCaseImportTemplateService, TestCaseExcelImportService
from .tasks import import_testcases_from_excel

class TestCasePagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class TestCaseImportRecordPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


def get_user_accessible_projects(user):
    return Project.objects.filter(
        models.Q(owner=user) | models.Q(members=user)
    ).distinct()


def serialize_module_tree(project_id):
    modules = list(TestCaseModule.objects.filter(project_id=project_id).order_by('sort_order', 'name', 'id'))
    counts = dict(TestCase.objects.filter(project_id=project_id, module__isnull=False)
                  .values_list('module_id').annotate(count=models.Count('id')))
    nodes = {module.id: {'id': module.id, 'name': module.name, 'project_id': module.project_id,
                         'parent_id': module.parent_id, 'count': counts.get(module.id, 0), 'children': []}
             for module in modules}
    roots = []
    for module in modules:
        (nodes[module.parent_id]['children'] if module.parent_id in nodes else roots).append(nodes[module.id])

    def add_descendant_counts(node):
        node['count'] += sum(add_descendant_counts(child) for child in node['children'])
        return node['count']

    for node in roots:
        add_descendant_counts(node)
    return roots


def validate_module_for_project(module_id, project):
    if module_id in (None, ''):
        return None
    try:
        return TestCaseModule.objects.get(pk=module_id, project=project)
    except (TestCaseModule.DoesNotExist, ValueError, TypeError):
        raise serializers.ValidationError({'module_id': '模块不存在或不属于所选项目'})


class TestCaseModuleListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        project_id = request.query_params.get('project')
        if not project_id or not project_id.isdigit() or not get_user_accessible_projects(request.user).filter(pk=project_id).exists():
            return Response({'detail': '项目不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_module_tree(int(project_id)))

    def post(self, request):
        project_id = request.data.get('project_id')
        project = get_user_accessible_projects(request.user).filter(pk=project_id).first()
        if not project:
            return Response({'project_id': '项目不存在或无权访问'}, status=status.HTTP_400_BAD_REQUEST)
        parent_id = request.data.get('parent_id')
        parent = validate_module_for_project(parent_id, project) if parent_id else None
        name = str(request.data.get('name', '')).strip()
        if not name:
            return Response({'name': '模块名称不能为空'}, status=status.HTTP_400_BAD_REQUEST)
        if parent and module_depth(parent) >= 8:
            return Response({'parent_id': '模块最多支持8层'}, status=status.HTTP_400_BAD_REQUEST)
        if TestCaseModule.objects.filter(project=project, parent=parent, name=name).exists():
            return Response({'name': '同一父模块下不能有重名模块'}, status=status.HTTP_400_BAD_REQUEST)
        module = TestCaseModule.objects.create(
            project=project,
            parent=parent,
            name=name,
            sort_order=TestCaseModule.next_sort_order(project, parent),
        )
        return Response({'id': module.id, 'name': module.name, 'project_id': project.id,
                         'parent_id': module.parent_id, 'count': 0, 'children': []}, status=status.HTTP_201_CREATED)


class TestCaseModuleReorderSerializer(serializers.Serializer):
    project_id = serializers.IntegerField(min_value=1)
    parent_id = serializers.IntegerField(min_value=1, allow_null=True)
    module_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=True)

    def validate_module_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError('目录列表不能包含重复项')
        return value


class TestCaseModuleReorderView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = TestCaseModuleReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project_id = serializer.validated_data['project_id']
        parent_id = serializer.validated_data['parent_id']
        module_ids = serializer.validated_data['module_ids']
        project = get_user_accessible_projects(request.user).filter(pk=project_id).first()
        if not project:
            return Response({'detail': '项目不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        parent = validate_module_for_project(parent_id, project) if parent_id is not None else None

        with transaction.atomic():
            siblings = list(TestCaseModule.objects.select_for_update().filter(
                project=project, parent=parent
            ).order_by('id'))
            sibling_ids = {module.id for module in siblings}
            if len(module_ids) != len(siblings) or set(module_ids) != sibling_ids:
                return Response({'module_ids': '必须提交该父目录下完整且有效的目录列表'}, status=status.HTTP_400_BAD_REQUEST)

            modules_by_id = {module.id: module for module in siblings}
            now = timezone.now()
            ordered_modules = []
            for sort_order, module_id in enumerate(module_ids):
                module = modules_by_id[module_id]
                module.sort_order = sort_order
                module.updated_at = now
                ordered_modules.append(module)
            TestCaseModule.objects.bulk_update(ordered_modules, ['sort_order', 'updated_at'])

        return Response({'updated': len(module_ids)})


class TestCaseModuleDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, request, pk):
        return TestCaseModule.objects.filter(pk=pk, project__in=get_user_accessible_projects(request.user)).first()

    def patch(self, request, pk):
        module = self.get_object(request, pk)
        if not module:
            return Response({'detail': '模块不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        name = str(request.data.get('name', '')).strip()
        if not name:
            return Response({'name': '模块名称不能为空'}, status=status.HTTP_400_BAD_REQUEST)
        if TestCaseModule.objects.filter(project=module.project, parent=module.parent, name=name).exclude(pk=module.pk).exists():
            return Response({'name': '同一父模块下不能有重名模块'}, status=status.HTTP_400_BAD_REQUEST)
        module.name = name
        module.save(update_fields=['name', 'updated_at'])
        return Response({'id': module.id, 'name': module.name, 'project_id': module.project_id, 'parent_id': module.parent_id})

    def delete(self, request, pk):
        module = self.get_object(request, pk)
        if not module:
            return Response({'detail': '模块不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        if module.children.exists() or module.testcases.exists():
            return Response({'detail': '请先移动模块下的用例和子模块'}, status=status.HTTP_400_BAD_REQUEST)
        module.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def put(self, request, pk):
        """移动模块到同项目的另一个父模块，parent_id 为空表示移到根级。"""
        module = self.get_object(request, pk)
        if not module:
            return Response({'detail': '模块不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        parent_id = request.data.get('parent_id')
        parent = validate_module_for_project(parent_id, module.project) if parent_id else None
        if parent and (parent.pk == module.pk or is_module_descendant(parent, module)):
            return Response({'parent_id': '不能移动到自身或其子模块下'}, status=status.HTTP_400_BAD_REQUEST)
        if parent and module_depth(parent) + module_subtree_height(module) > 8:
            return Response({'parent_id': '移动后模块层级不能超过8层'}, status=status.HTTP_400_BAD_REQUEST)
        if TestCaseModule.objects.filter(project=module.project, parent=parent, name=module.name).exclude(pk=module.pk).exists():
            return Response({'name': '目标父模块下已有同名模块'}, status=status.HTTP_400_BAD_REQUEST)
        module.parent = parent
        module.sort_order = TestCaseModule.next_sort_order(module.project, parent)
        module.save(update_fields=['parent', 'sort_order', 'updated_at'])
        return Response({'id': module.id, 'parent_id': module.parent_id})


class TestCaseMoveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = request.data.get('testcase_ids', [])
        module_id = request.data.get('module_id')
        if not isinstance(ids, list) or not ids:
            return Response({'testcase_ids': '请选择至少一个用例'}, status=status.HTTP_400_BAD_REQUEST)
        cases = TestCase.objects.filter(pk__in=ids, project__in=get_user_accessible_projects(request.user))
        case_list = list(cases)
        if len(case_list) != len(set(ids)):
            return Response({'testcase_ids': '包含不存在或无权访问的用例'}, status=status.HTTP_404_NOT_FOUND)
        projects = {case.project_id for case in case_list}
        if len(projects) != 1:
            return Response({'testcase_ids': '批量移动的用例必须属于同一项目'}, status=status.HTTP_400_BAD_REQUEST)
        module = validate_module_for_project(module_id, case_list[0].project) if module_id is not None else None
        with transaction.atomic():
            cases.update(module=module)
        return Response({'updated': len(case_list)})


def module_depth(module):
    depth = 1
    while module.parent_id:
        depth += 1
        module = module.parent
    return depth


def is_module_descendant(candidate, module):
    parent_id = candidate.parent_id
    while parent_id:
        if parent_id == module.pk:
            return True
        parent_id = TestCaseModule.objects.filter(pk=parent_id).values_list('parent_id', flat=True).first()
    return False


def module_subtree_height(module):
    return 1 + max((module_subtree_height(child) for child in module.children.all()), default=0)

class TestCaseListCreateView(generics.ListCreateAPIView):
    queryset = TestCase.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = TestCasePagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['priority', 'test_type']
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'updated_at', 'priority']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return TestCaseCreateSerializer
        return TestCaseListSerializer

    def get_queryset(self):
        user = self.request.user
        accessible_projects = get_user_accessible_projects(user)
        queryset = TestCase.objects.filter(
            project__in=accessible_projects
        ).select_related(
            'author', 'assignee', 'project'
        ).prefetch_related(
            'versions', 'module'
        ).distinct()
        # 支持多项目过滤：project=1,2,3（单值 project=1 同样兼容）
        project_param = self.request.query_params.get('project')
        if project_param:
            project_ids = [int(pid) for pid in project_param.split(',') if pid.strip().isdigit()]
            if project_ids:
                queryset = queryset.filter(project_id__in=project_ids)
        module_param = self.request.query_params.get('module')
        if module_param == 'none':
            queryset = queryset.filter(module__isnull=True)
        elif module_param and module_param.isdigit():
            module_ids = [int(module_param)]
            if self.request.query_params.get('include_children') == 'true':
                descendant_ids = []
                frontier = list(TestCaseModule.objects.filter(parent_id=int(module_param)).values_list('id', flat=True))
                while frontier:
                    descendant_ids.extend(frontier)
                    frontier = list(TestCaseModule.objects.filter(parent_id__in=frontier).values_list('id', flat=True))
                module_ids.extend(descendant_ids)
            queryset = queryset.filter(module_id__in=module_ids)
        maintainer_param = self.request.query_params.get('maintainer')
        if maintainer_param and maintainer_param.isdigit():
            queryset = queryset.filter(author_id=int(maintainer_param))
        return queryset
    
    def get_user_accessible_projects(self, user):
        """获取用户有权限访问的项目"""
        return get_user_accessible_projects(user)
    
    def perform_create(self, serializer):
        user = self.request.user
        project_id = self.request.data.get('project_id')
        
        # 获取用户有权限的项目
        accessible_projects = self.get_user_accessible_projects(user)
        
        if project_id:
            # 检查指定的项目是否存在且用户有权限
            try:
                project = accessible_projects.get(id=project_id)
            except Project.DoesNotExist:
                # 如果指定项目不存在或无权限，使用第一个可访问的项目
                project = accessible_projects.first()
                if not project:
                    # 如果用户没有任何项目，创建默认项目
                    project = Project.objects.create(
                        name="默认项目",
                        owner=user,
                        description='系统自动创建的默认项目'
                    )
        else:
            # 没有指定项目，使用第一个可访问的项目
            project = accessible_projects.first()
            if not project:
                # 如果用户没有任何项目，创建默认项目
                project = Project.objects.create(
                    name="默认项目",
                    owner=user,
                    description='系统自动创建的默认项目'
                )
        
        maintainer_id = serializer.validated_data.pop('maintainer_id', None)
        module_id = serializer.validated_data.pop('module_id', None)
        module = validate_module_for_project(module_id, project) if module_id else None
        maintainer = user
        if maintainer_id:
            from apps.projects.models import ProjectMember
            if maintainer_id != project.owner_id and not ProjectMember.objects.filter(project=project, user_id=maintainer_id).exists():
                raise serializers.ValidationError({'maintainer_id': '维护人必须是项目成员'})
            maintainer = User.objects.filter(id=maintainer_id).first()
            if not maintainer:
                raise serializers.ValidationError({'maintainer_id': '维护人不存在'})
        serializer.save(author=maintainer, project=project, module=module)

class TestCaseDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = TestCase.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return TestCaseUpdateSerializer
        return TestCaseSerializer
    
    def get_queryset(self):
        user = self.request.user
        accessible_projects = get_user_accessible_projects(user)
        return TestCase.objects.filter(
            project__in=accessible_projects
        ).select_related(
            'author', 'assignee', 'project'
        ).prefetch_related(
            'versions', 'step_details', 'attachments', 'comments'
        )
    
    def get_user_accessible_projects(self, user):
        """获取用户有权限访问的项目"""
        return get_user_accessible_projects(user)
    
    def perform_update(self, serializer):
        user = self.request.user
        project_id = self.request.data.get('project_id')
        
        maintainer_id = serializer.validated_data.pop('maintainer_id', None)
        module_id = serializer.validated_data.pop('module_id', None)
        project = None
        if project_id:
            # 检查指定的项目是否存在且用户有权限
            accessible_projects = self.get_user_accessible_projects(user)
            try:
                project = accessible_projects.get(id=project_id)
            except Project.DoesNotExist:
                raise serializers.ValidationError({'project_id': '项目不存在或无权访问'})
        project = project or serializer.instance.project
        module = validate_module_for_project(module_id, project) if module_id is not None else serializer.instance.module
        if module and module.project_id != project.id:
            if project_id:
                module = None
            else:
                raise serializers.ValidationError({'module_id': '模块不属于当前项目'})
        save_kwargs = {}
        if project_id and project is not None:
            save_kwargs['project'] = project
            save_kwargs['module'] = module
        elif module_id is not None:
            save_kwargs['module'] = module
        if maintainer_id is not None:
            from apps.projects.models import ProjectMember
            if maintainer_id != project.owner_id and not ProjectMember.objects.filter(project=project, user_id=maintainer_id).exists():
                raise serializers.ValidationError({'maintainer_id': '维护人必须是项目成员'})
            maintainer = User.objects.filter(id=maintainer_id).first()
            if not maintainer:
                raise serializers.ValidationError({'maintainer_id': '维护人不存在'})
            save_kwargs['author'] = maintainer
        serializer.save(**save_kwargs)


class TestCaseNotesImageUploadView(APIView):
    """上传备注图片，备注正文只保存附件 URL，避免把图片 Base64 放进更新请求。"""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        testcase = TestCase.objects.filter(
            pk=pk,
            project__in=get_user_accessible_projects(request.user),
        ).first()
        if not testcase:
            return Response({'detail': '用例不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)

        upload = request.FILES.get('file')
        if not upload or upload.content_type not in {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}:
            return Response({'detail': '只支持 PNG、JPEG、GIF 和 WebP 图片'}, status=status.HTTP_400_BAD_REQUEST)
        if upload.size > 10 * 1024 * 1024:
            return Response({'detail': '图片大小不能超过 10MB'}, status=status.HTTP_400_BAD_REQUEST)

        attachment = TestCaseAttachment.objects.create(
            testcase=testcase,
            name=upload.name,
            file=upload,
            uploaded_by=request.user,
        )
        return Response({'url': attachment.file.url}, status=status.HTTP_201_CREATED)


class TestCaseImportTemplateDownloadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        template_stream = TestCaseImportTemplateService.build_template()
        return FileResponse(
            template_stream,
            as_attachment=True,
            filename='testcase_import_template_v2.xlsx',
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )


class TestCaseImportRecordListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = TestCaseImportRecordPagination
    parser_classes = [MultiPartParser, FormParser]

    def get_serializer_class(self):
        return TestCaseImportRecordListSerializer

    def get_queryset(self):
        return TestCaseImportRecord.objects.filter(
            project__in=get_user_accessible_projects(self.request.user)
        ).select_related('project', 'created_by')

    def create(self, request, *args, **kwargs):
        project_id = request.data.get('project_id')
        import_file = request.FILES.get('file')

        if not project_id:
            return Response({'error': '请选择导入项目'}, status=status.HTTP_400_BAD_REQUEST)
        if not import_file:
            return Response({'error': '请上传 Excel 文件'}, status=status.HTTP_400_BAD_REQUEST)
        if not import_file.name.lower().endswith('.xlsx'):
            return Response({'error': '仅支持 .xlsx 格式文件'}, status=status.HTTP_400_BAD_REQUEST)

        accessible_projects = get_user_accessible_projects(request.user)
        try:
            project = accessible_projects.get(id=project_id)
        except Project.DoesNotExist:
            return Response({'error': '项目不存在或无权限访问'}, status=status.HTTP_403_FORBIDDEN)

        record = TestCaseImportRecord.objects.create(
            import_no=TestCaseExcelImportService.generate_import_no(),
            project=project,
            import_file=import_file,
            created_by=request.user,
            template_version=TEMPLATE_VERSION
        )

        from apps.core.task_queue import enqueue_task
        task_id = enqueue_task(import_testcases_from_excel, record.id)
        record.celery_task_id = task_id
        record.save(update_fields=['celery_task_id', 'updated_at'])

        serializer = self.get_serializer(record)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class TestCaseImportRecordDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TestCaseImportRecordDetailSerializer
    queryset = TestCaseImportRecord.objects.select_related('project', 'created_by')

    def get_queryset(self):
        return super().get_queryset().filter(
            project__in=get_user_accessible_projects(self.request.user)
        )


class TestCaseImportFailureReportDownloadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        record = TestCaseImportRecord.objects.filter(
            pk=pk,
            project__in=get_user_accessible_projects(request.user)
        ).first()

        if not record:
            return Response({'error': '导入记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        if not record.failure_report_file:
            return Response({'error': '当前记录没有失败明细文件'}, status=status.HTTP_404_NOT_FOUND)

        return FileResponse(
            record.failure_report_file.open('rb'),
            as_attachment=True,
            filename=record.failure_report_file.name.split('/')[-1],
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
