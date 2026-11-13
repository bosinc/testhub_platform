from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.ui_automation.models import Element, ElementGroup, LocatorStrategy, UiProject
from apps.ui_automation.serializers import ElementGroupSerializer
from apps.ui_automation.views import ElementGroupViewSet


class ElementGroupApiPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='group-api-user')
        cls.other_user = get_user_model().objects.create_user(username='group-api-other')
        cls.project = UiProject.objects.create(
            name='Group API project', base_url='https://example.test', owner=cls.user,
        )
        cls.project.members.add(cls.user, cls.other_user)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.list_url = reverse('elementgroup-list')
        self.tree_url = reverse('elementgroup-tree')

    def _create_groups(self, count):
        root = ElementGroup.objects.create(project=self.project, name='Root', order=-1)
        groups = [root]
        groups.extend(
            ElementGroup.objects.create(project=self.project, name=f'Page {index:03}', parent_group=root)
            for index in range(count - 1)
        )
        return root, groups

    def _get_with_query_count(self, url, **params):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, {'project': self.project.pk, **params})
        self.assertEqual(response.status_code, 200)
        return response, len(queries)

    def test_query_count_stays_bounded_as_tree_grows(self):
        root, groups = self._create_groups(5)
        _, small_list_queries = self._get_with_query_count(self.list_url)
        _, small_tree_queries = self._get_with_query_count(self.tree_url)

        # 扩充到 100 个分组并增加层级，确保任意深度的序列化都不再逐节点查询。
        branch = ElementGroup.objects.create(project=self.project, name='Branch', parent_group=groups[1])
        ElementGroup.objects.bulk_create([
            ElementGroup(project=self.project, name=f'Extra {index:03}', parent_group=branch)
            for index in range(94)
        ])
        list_response, list_queries = self._get_with_query_count(self.list_url)
        tree_response, tree_queries = self._get_with_query_count(self.tree_url)
        self.assertEqual(list_response.data['count'], 100)
        self.assertEqual(len(list_response.data['results']), 20)
        self.assertIsNotNone(list_response.data['next'])
        self.assertEqual(tree_response.data[0]['id'], root.pk)
        self.assertLessEqual(list_queries, 5)
        self.assertLessEqual(tree_queries, 2)
        self.assertEqual(list_queries, small_list_queries)
        self.assertEqual(tree_queries, small_tree_queries)

    def test_response_matches_existing_serializer_with_direct_element_counts(self):
        root, groups = self._create_groups(3)
        grandchild = ElementGroup.objects.create(
            project=self.project, name='Grandchild', parent_group=groups[1],
        )
        strategy = LocatorStrategy.objects.create(name='CSS')
        for group, count in ((root, 1), (groups[1], 2), (grandchild, 3)):
            Element.objects.bulk_create([
                Element(project=self.project, group=group, name=f'Submit {index}',
                        locator_strategy=strategy, locator_value='#submit')
                for index in range(count)
            ])

        list_response, _ = self._get_with_query_count(self.list_url)
        tree_response, _ = self._get_with_query_count(self.tree_url)
        queryset = ElementGroup.objects.filter(project=self.project).order_by('order', 'name')
        self.assertEqual(list_response.data['results'], ElementGroupSerializer(queryset, many=True).data)
        self.assertEqual(tree_response.data, ElementGroupSerializer(queryset.filter(parent_group=None), many=True).data)
        root_data = tree_response.data[0]
        self.assertEqual(root_data['elements_count'], 1)
        self.assertEqual(root_data['children'][0]['elements_count'], 2)
        self.assertEqual(root_data['children'][0]['children'][0]['elements_count'], 3)
        self.assertEqual(root_data['children'][1]['elements_count'], 0)
        self.assertEqual(root_data['children'][1]['children'], [])
        self.assertEqual(len(root_data['project']['members']), 2)

    def test_filters_do_not_truncate_descendants(self):
        root, groups = self._create_groups(3)
        grandchild = ElementGroup.objects.create(
            project=self.project, name='Nested', parent_group=groups[1],
        )
        searched, _ = self._get_with_query_count(self.list_url, search='Root')
        self.assertEqual(searched.data['count'], 1)
        self.assertEqual(len(searched.data['results'][0]['children']), 2)

        filtered, _ = self._get_with_query_count(self.list_url, parent_group=root.pk)
        self.assertEqual([group['id'] for group in filtered.data['results']], [group.pk for group in groups[1:]])
        self.assertEqual(filtered.data['results'][0]['children'][0]['id'], grandchild.pk)

    def test_pagination_keeps_order_links_and_complete_subtrees(self):
        root, _ = self._create_groups(30)
        first, _ = self._get_with_query_count(self.list_url)
        second, _ = self._get_with_query_count(self.list_url, page=2)
        expected_ids = list(ElementGroup.objects.filter(project=self.project).values_list('id', flat=True))
        self.assertEqual(first.data['count'], 30)
        self.assertEqual([group['id'] for group in first.data['results']], expected_ids[:20])
        self.assertEqual([group['id'] for group in second.data['results']], expected_ids[20:])
        self.assertEqual(first.data['results'][0]['id'], root.pk)
        self.assertEqual(len(first.data['results'][0]['children']), 29)
        self.assertIsNone(first.data['previous'])
        self.assertIn('page=2', first.data['next'])
        self.assertIsNotNone(second.data['previous'])
        self.assertIsNone(second.data['next'])

    def test_owner_member_and_deleted_project_permissions(self):
        own_group, _ = self._create_groups(2)
        member_project = UiProject.objects.create(
            name='Member project', base_url='https://example.test', owner=self.other_user,
        )
        member_project.members.add(self.user)
        member_group = ElementGroup.objects.create(project=member_project, name='Member root')
        private_project = UiProject.objects.create(
            name='Private project', base_url='https://example.test', owner=self.other_user,
        )
        ElementGroup.objects.create(project=private_project, name='Private root')
        deleted_project = UiProject.objects.create(
            name='Deleted project', base_url='https://example.test', owner=self.user, is_deleted=True,
        )
        ElementGroup.objects.create(project=deleted_project, name='Deleted root')

        # 未指定项目的列表仍按原权限返回多个项目，项目缓存不得串用。
        visible = self.client.get(self.list_url)
        self.assertEqual(visible.data['count'], 3)
        projects = {group['id']: group['project']['id'] for group in visible.data['results']}
        self.assertEqual(projects[own_group.pk], self.project.pk)
        self.assertEqual(projects[member_group.pk], member_project.pk)
        member_tree = self.client.get(self.tree_url, {'project': member_project.pk})
        self.assertEqual(member_tree.data[0]['id'], member_group.pk)
        for project in (private_project, deleted_project):
            with self.subTest(project=project.name):
                response = self.client.get(self.list_url, {'project': project.pk})
                # 保留过滤器对软删除项目的原有校验行为。
                if project.is_deleted:
                    self.assertEqual(response.status_code, 400)
                else:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data['results'], [])
                tree = self.client.get(self.tree_url, {'project': project.pk})
                self.assertEqual(tree.status_code, 200)
                self.assertEqual(tree.data, [])

    def test_empty_tree_missing_project_and_anonymous_access(self):
        response, _ = self._get_with_query_count(self.tree_url)
        self.assertEqual(response.data, [])
        listing, _ = self._get_with_query_count(self.list_url)
        self.assertEqual(listing.data['count'], 0)
        self.assertEqual(listing.data['results'], [])
        self.assertEqual(self.client.get(self.tree_url).status_code, 400)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.list_url).status_code, 401)
        self.assertEqual(self.client.get(self.tree_url, {'project': self.project.pk}).status_code, 401)

    def test_project_cache_is_scoped_to_one_response(self):
        self._create_groups(2)
        before, _ = self._get_with_query_count(self.tree_url)
        self.project.name = 'Renamed project'
        self.project.save(update_fields=['name'])
        after, _ = self._get_with_query_count(self.tree_url)
        self.assertEqual(before.data[0]['project']['name'], 'Group API project')
        self.assertEqual(after.data[0]['project']['name'], 'Renamed project')
        self.assertEqual(after.data[0]['children'][0]['project']['name'], 'Renamed project')

    def test_list_without_pagination_preserves_filtered_subtrees(self):
        self._create_groups(3)
        request = APIRequestFactory().get(self.list_url, {'project': self.project.pk, 'search': 'Root'})
        force_authenticate(request, user=self.user)
        response = ElementGroupViewSet.as_view({'get': 'list'}, pagination_class=None)(request)
        response.render()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(len(response.data[0]['children']), 2)
