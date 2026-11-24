from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.projects.models import Project
from apps.reviews.models import (
    ReviewAssignment, ReviewTemplate, TestCaseReview, TestCaseReviewComment,
)
from apps.testcases.models import TestCase as TCModel
from apps.users.models import User

REVIEW_LIST_URL = '/api/reviews/reviews/'


def review_detail_url(pk):
    return f'/api/reviews/reviews/{pk}/'


class ReviewApiPerformanceTests(TestCase):
    """Reviews 模块 - 评审列表/详情接口性能与返回结构"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='review_creator', password='pass123456')
        cls.reviewer_a = User.objects.create_user(username='review_reviewer_a', password='pass123456')
        cls.reviewer_b = User.objects.create_user(username='review_reviewer_b', password='pass123456')
        cls.project = Project.objects.create(name='评审项目', owner=cls.user)
        cls.other_project = Project.objects.create(name='其他评审项目', owner=cls.user)
        cls.template = ReviewTemplate.objects.create(name='评审模板', creator=cls.user, checklist=['步骤完整'])
        cls.template.project.add(cls.project)
        cls.template.default_reviewers.add(cls.reviewer_a)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create_review(self, title, testcase_count=0, comment_count=0, reviewers=(), projects=None):
        review = TestCaseReview.objects.create(title=title, creator=self.user, template=self.template)
        review.projects.set(projects if projects is not None else [self.project])
        testcases = [
            # 每条用例使用不同作者，放大逐用例查询作者的 N+1 问题。
            TCModel.objects.create(
                project=self.project, title=f'{title}-用例{index}',
                author=User.objects.create_user(username=f'{title}-author-{index}'),
            )
            for index in range(testcase_count)
        ]
        review.testcases.set(testcases)
        for reviewer in reviewers:
            ReviewAssignment.objects.create(review=review, reviewer=reviewer)
        for index in range(comment_count):
            TestCaseReviewComment.objects.create(
                review=review, author=self.reviewer_a, content=f'意见{index}',
                testcase=testcases[index % len(testcases)] if testcases else None,
            )
        return review

    def _get_with_query_count(self, url, **params):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response, len(queries)

    def test_list_query_count_does_not_grow_with_data(self):
        """查询数不随评审数、用例数、评审意见数增长"""
        self._create_review('small-0', testcase_count=1, reviewers=[self.reviewer_a])
        _, small_queries = self._get_with_query_count(REVIEW_LIST_URL)

        for index in range(10):
            self._create_review(
                f'large-{index}', testcase_count=8, comment_count=5,
                reviewers=[self.reviewer_a, self.reviewer_b],
            )
        response, large_queries = self._get_with_query_count(REVIEW_LIST_URL)

        self.assertEqual(response.data['count'], 11)
        self.assertEqual(large_queries, small_queries)
        # count + 评审 + 项目 + 评审分配 + 评审人
        self.assertLessEqual(large_queries, 5)

    def test_list_returns_summary_fields(self):
        """列表只返回摘要字段，用例以数量形式返回"""
        review = self._create_review(
            'summary', testcase_count=3, comment_count=2,
            reviewers=[self.reviewer_a, self.reviewer_b],
        )
        ReviewAssignment.objects.filter(review=review, reviewer=self.reviewer_b).update(status='approved')

        response, _ = self._get_with_query_count(REVIEW_LIST_URL)
        row = response.data['results'][0]

        self.assertEqual(row['id'], review.id)
        self.assertEqual(row['testcase_count'], 3)
        self.assertEqual(row['creator']['id'], self.user.id)
        self.assertEqual([p['name'] for p in row['projects']], ['评审项目'])
        self.assertEqual(
            {(a['reviewer']['id'], a['reviewer']['username'], a['status']) for a in row['assignments']},
            {
                (self.reviewer_a.id, 'review_reviewer_a', 'pending'),
                (self.reviewer_b.id, 'review_reviewer_b', 'approved'),
            },
        )
        for heavy_field in ('testcases', 'comments', 'template'):
            self.assertNotIn(heavy_field, row)

    def test_testcase_count_is_correct_with_filters(self):
        """项目/评审人/状态过滤不会让用例数被 JOIN 放大，也不会产生重复行"""
        multi = self._create_review(
            'multi', testcase_count=4, reviewers=[self.reviewer_a, self.reviewer_b],
            projects=[self.project, self.other_project],
        )
        empty = self._create_review('empty', reviewers=[self.reviewer_a])
        self._create_review('other', testcase_count=2, projects=[self.other_project])

        def counts(**params):
            response, _ = self._get_with_query_count(REVIEW_LIST_URL, **params)
            return {row['id']: row['testcase_count'] for row in response.data['results']}

        self.assertEqual(counts(project=self.project.id), {multi.id: 4, empty.id: 0})
        self.assertEqual(counts(reviewer=self.reviewer_a.id), {multi.id: 4, empty.id: 0})
        self.assertEqual(counts(reviewer=self.reviewer_b.id, status='pending'), {multi.id: 4})

    def test_detail_still_returns_full_payload(self):
        """详情接口保持原有完整结构"""
        review = self._create_review('detail', testcase_count=2, comment_count=1, reviewers=[self.reviewer_a])

        response = self.client.get(review_detail_url(review.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['testcases']), 2)
        self.assertEqual(len(response.data['comments']), 1)
        self.assertEqual(response.data['template']['id'], self.template.id)
        self.assertEqual(len(response.data['assignments']), 1)

    def test_list_honors_page_size(self):
        """page_size 参数生效，并受上限约束"""
        TestCaseReview.objects.bulk_create([
            TestCaseReview(title=f'paged-{index}', creator=self.user) for index in range(101)
        ])

        response, _ = self._get_with_query_count(REVIEW_LIST_URL, page_size=10)
        self.assertEqual(response.data['count'], 101)
        self.assertEqual(len(response.data['results']), 10)
        self.assertIsNotNone(response.data['next'])

        response, _ = self._get_with_query_count(REVIEW_LIST_URL)
        self.assertEqual(len(response.data['results']), 20)

        response, _ = self._get_with_query_count(REVIEW_LIST_URL, page_size=1000)
        self.assertEqual(len(response.data['results']), 100)

    def test_detail_query_count_does_not_grow_with_data(self):
        """详情查询数不随用例数、评审意见数、评审人数增长"""
        small = self._create_review('detail-small', testcase_count=1, comment_count=1, reviewers=[self.reviewer_a])
        large = self._create_review(
            'detail-large', testcase_count=30, comment_count=20,
            reviewers=[self.reviewer_a, self.reviewer_b],
        )

        _, small_queries = self._get_with_query_count(review_detail_url(small.id))
        response, large_queries = self._get_with_query_count(review_detail_url(large.id))

        self.assertEqual(len(response.data['testcases']), 30)
        self.assertEqual(len(response.data['comments']), 20)
        self.assertEqual(large_queries, small_queries)
        self.assertLessEqual(large_queries, 12)

    def test_my_reviews_query_count_does_not_grow_with_data(self):
        """我的评审任务查询数不随评审数增长，返回结构与详情一致"""
        self.client.force_authenticate(self.reviewer_a)
        url = f'{REVIEW_LIST_URL}my_reviews/'
        self._create_review('mine-0', testcase_count=1, comment_count=1, reviewers=[self.reviewer_a])
        _, small_queries = self._get_with_query_count(url)

        for index in range(1, 6):
            self._create_review(
                f'mine-{index}', testcase_count=6, comment_count=4,
                reviewers=[self.reviewer_a, self.reviewer_b],
            )
        self._create_review('not-mine', testcase_count=2, reviewers=[self.reviewer_b])
        response, large_queries = self._get_with_query_count(url)

        self.assertEqual(len(response.data), 6)
        self.assertEqual(len(response.data[0]['testcases']), 6)
        self.assertEqual(large_queries, small_queries)

    def test_write_actions_still_work(self):
        """更新、提交评审、删除不依赖详情预取"""
        review = self._create_review('writable', testcase_count=2, reviewers=[self.user])

        response = self.client.patch(review_detail_url(review.id), {'title': 'renamed'}, format='json')
        self.assertEqual(response.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.title, 'renamed')

        response = self.client.post(
            f'{review_detail_url(review.id)}submit_review/', {'status': 'approved'}, format='json')
        self.assertEqual(response.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.status, 'approved')

        response = self.client.delete(review_detail_url(review.id))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(TestCaseReview.objects.filter(id=review.id).exists())
