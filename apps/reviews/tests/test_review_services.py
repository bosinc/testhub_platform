from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.projects.models import Project
from apps.reviews import services
from apps.reviews.models import (
    ReviewAssignment, ReviewTemplate, TestCaseReview, TestCaseReviewComment,
)
from apps.testcases.models import TestCase

User = get_user_model()

REVIEW_URL = '/api/reviews/reviews/'
COMMENT_URL = '/api/reviews/review-comments/'


class ReviewTestBase(APITestCase):
    def setUp(self):
        self.creator = User.objects.create(username='rv_creator', email='creator@example.com')
        self.reviewer_a = User.objects.create(username='rv_reviewer_a', email='a@example.com')
        self.reviewer_b = User.objects.create(username='rv_reviewer_b', email='b@example.com')
        self.project = Project.objects.create(name='评审项目', owner=self.creator)
        self.case = TestCase.objects.create(project=self.project, title='评审内用例', author=self.creator)
        self.outside_case = TestCase.objects.create(
            project=self.project, title='未纳入评审的用例', author=self.creator)
        self.review = services.create_review(
            creator=self.creator,
            fields={'title': '登录用例评审'},
            project_ids=[self.project.id],
            testcase_ids=[self.case.id],
            reviewer_ids=[self.reviewer_a.id],
        )
        self.client.force_authenticate(self.creator)

    def approve(self, reviewer):
        ReviewAssignment.objects.filter(review=self.review, reviewer=reviewer).update(
            status='approved', comment='LGTM', reviewed_at=timezone.now())

    def assignment(self, reviewer):
        return ReviewAssignment.objects.get(review=self.review, reviewer=reviewer)

    def reviewer_ids(self):
        return sorted(
            ReviewAssignment.objects.filter(review=self.review).values_list('reviewer_id', flat=True))


class ReviewServiceTests(ReviewTestBase):
    """services 是序列化器与 MCP 共用的唯一写入口。"""

    def test_sync_reviewers_keeps_submitted_verdicts(self):
        self.approve(self.reviewer_a)

        result = services.sync_reviewers(self.review, [self.reviewer_a.id, self.reviewer_b.id])

        self.assertEqual(result, {'added': [self.reviewer_b.id], 'removed': []})
        self.assertEqual(
            (self.assignment(self.reviewer_a).status, self.assignment(self.reviewer_a).comment),
            ('approved', 'LGTM'),
        )
        self.assertEqual(self.assignment(self.reviewer_b).status, 'pending')

    def test_sync_reviewers_dedupes_and_ignores_unknown_users(self):
        result = services.sync_reviewers(
            self.review, [self.reviewer_b.id, str(self.reviewer_b.id), 987654, 'abc'])

        self.assertEqual(result, {'added': [self.reviewer_b.id], 'removed': [self.reviewer_a.id]})
        self.assertEqual(self.reviewer_ids(), [self.reviewer_b.id])

    def test_sync_reviewers_append_only_keeps_missing(self):
        services.sync_reviewers(self.review, [self.reviewer_b.id], remove_missing=False)

        self.assertEqual(self.reviewer_ids(), sorted([self.reviewer_a.id, self.reviewer_b.id]))

    def test_update_review_only_touches_given_parts(self):
        template = ReviewTemplate.objects.create(name='模板', creator=self.creator)
        self.review.template = template
        self.review.save()

        services.update_review(self.review, fields={'title': '改名'})

        self.review.refresh_from_db()
        self.assertEqual(self.review.title, '改名')
        self.assertEqual(self.review.template, template)
        self.assertEqual(list(self.review.testcases.all()), [self.case])
        self.assertEqual(self.reviewer_ids(), [self.reviewer_a.id])

        services.update_review(self.review, template_id=None, testcase_ids=[])

        self.review.refresh_from_db()
        self.assertIsNone(self.review.template)
        self.assertFalse(self.review.testcases.exists())

    def test_accessible_reviews_cover_creator_reviewer_and_project_member(self):
        member = User.objects.create(username='rv_member', email='member@example.com')
        self.project.members.add(member)
        stranger = User.objects.create(username='rv_stranger', email='stranger@example.com')

        for user in (self.creator, self.reviewer_a, member):
            self.assertEqual(list(services.accessible_reviews(user)), [self.review])
        self.assertFalse(services.accessible_reviews(stranger).exists())


class ReviewApiTests(ReviewTestBase):
    """评审接口经 services 落库后的对外行为。"""

    def detail_url(self):
        return f'{REVIEW_URL}{self.review.id}/'

    def form_payload(self, **overrides):
        payload = {
            'title': '登录用例评审',
            'description': '',
            'projects': [self.project.id],
            'priority': 'medium',
            'deadline': None,
            'testcases': [self.case.id],
            'reviewers': [self.reviewer_a.id],
            'template': None,
        }
        payload.update(overrides)
        return payload

    def test_create_review_assigns_reviewers(self):
        response = self.client.post(REVIEW_URL, self.form_payload(
            title='新评审', reviewers=[self.reviewer_a.id, self.reviewer_b.id]), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        review = TestCaseReview.objects.get(title='新评审')
        self.assertEqual(review.creator, self.creator)
        self.assertEqual(list(review.projects.all()), [self.project])
        self.assertEqual(list(review.testcases.all()), [self.case])
        self.assertEqual(ReviewAssignment.objects.filter(review=review).count(), 2)

    def test_full_form_update_keeps_submitted_verdict(self):
        self.approve(self.reviewer_a)

        response = self.client.put(self.detail_url(), self.form_payload(
            title='改了标题', reviewers=[self.reviewer_a.id, self.reviewer_b.id]), format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.review.refresh_from_db()
        self.assertEqual(self.review.title, '改了标题')
        self.assertEqual(self.assignment(self.reviewer_a).status, 'approved')
        self.assertEqual(self.assignment(self.reviewer_b).status, 'pending')

    def test_duplicate_reviewers_are_deduplicated(self):
        response = self.client.put(self.detail_url(), self.form_payload(
            reviewers=[self.reviewer_b.id, self.reviewer_b.id]), format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.reviewer_ids(), [self.reviewer_b.id])

    def test_partial_update_keeps_relations(self):
        response = self.client.patch(self.detail_url(), {'title': '只改标题'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(self.review.projects.all()), [self.project])
        self.assertEqual(list(self.review.testcases.all()), [self.case])
        self.assertEqual(self.reviewer_ids(), [self.reviewer_a.id])

    def test_assign_reviewers_appends_without_touching_existing(self):
        self.approve(self.reviewer_a)

        response = self.client.post(
            f'{self.detail_url()}assign_reviewers/',
            {'reviewer_ids': [self.reviewer_a.id, self.reviewer_b.id]}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.assignment(self.reviewer_a).status, 'approved')
        self.assertEqual(self.reviewer_ids(), sorted([self.reviewer_a.id, self.reviewer_b.id]))


class ReviewCommentApiTests(ReviewTestBase):
    """评审意见与评审、用例的关联规则（页面接口与 MCP 共用同一个序列化器）。"""

    def post_comment(self, **overrides):
        payload = {'review': self.review.id, 'comment_type': 'general', 'content': '整体覆盖完整'}
        payload.update(overrides)
        return self.client.post(COMMENT_URL, payload, format='json')

    def comment_url(self, comment):
        return f'{COMMENT_URL}{comment.id}/'

    def test_general_and_testcase_comments_are_created(self):
        general = self.post_comment()
        on_case = self.post_comment(comment_type='testcase', testcase=self.case.id)

        self.assertEqual(general.status_code, status.HTTP_201_CREATED)
        self.assertEqual(on_case.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            TestCaseReviewComment.objects.get(comment_type='testcase').testcase, self.case)

    def test_testcase_comment_requires_case_of_the_review(self):
        missing = self.post_comment(comment_type='testcase')
        outside = self.post_comment(comment_type='testcase', testcase=self.outside_case.id)

        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(outside.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('testcase', outside.data)
        self.assertFalse(TestCaseReviewComment.objects.exists())

    def test_general_comment_rejects_testcase(self):
        response = self.post_comment(testcase=self.case.id)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_comment_cannot_move_to_another_review(self):
        other_review = services.create_review(creator=self.creator, fields={'title': '另一个评审'})
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.creator, content='原意见')

        moved = self.client.patch(self.comment_url(comment), {'review': other_review.id}, format='json')
        same = self.client.patch(
            self.comment_url(comment), {'review': self.review.id, 'content': '改内容'}, format='json')

        self.assertEqual(moved.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(same.status_code, status.HTTP_200_OK)
        comment.refresh_from_db()
        self.assertEqual((comment.review, comment.content), (self.review, '改内容'))

    def test_switching_to_general_clears_testcase(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.creator, content='用例意见',
            comment_type='testcase', testcase=self.case)

        response = self.client.patch(
            self.comment_url(comment), {'comment_type': 'general', 'content': '改成整体意见'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        comment.refresh_from_db()
        self.assertEqual(comment.comment_type, 'general')
        self.assertIsNone(comment.testcase)

    def test_comment_stays_editable_after_case_left_the_review(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.creator, content='用例意见',
            comment_type='testcase', testcase=self.case)
        self.review.testcases.remove(self.case)

        response = self.client.patch(self.comment_url(comment), {
            'review': self.review.id, 'comment_type': 'testcase',
            'testcase': self.case.id, 'content': '补充说明',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        comment.refresh_from_db()
        self.assertEqual(comment.content, '补充说明')

    def test_only_author_can_update_or_delete(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.reviewer_a, content='评审人的意见')

        updated = self.client.patch(self.comment_url(comment), {'content': '被别人改'}, format='json')
        deleted = self.client.delete(self.comment_url(comment))

        self.assertEqual(updated.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(deleted.status_code, status.HTTP_403_FORBIDDEN)
        comment.refresh_from_db()
        self.assertEqual(comment.content, '评审人的意见')


class ReviewCommentStatusApiTests(ReviewTestBase):
    """评审意见的解决状态：意见作者与评审创建人可改。"""

    def setUp(self):
        super().setUp()
        # 意见由评审人提出，评审创建人是另一个人
        self.comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.reviewer_a, content='请补充异常场景')
        self.url = f'{COMMENT_URL}{self.comment.id}/update_status/'

    def set_status(self, user, is_resolved):
        self.client.force_authenticate(user)
        return self.client.patch(self.url, {'is_resolved': is_resolved}, format='json')

    def test_author_and_review_creator_can_toggle_status(self):
        resolved = self.set_status(self.reviewer_a, True)

        self.assertEqual(resolved.status_code, status.HTTP_200_OK)
        self.assertTrue(resolved.data['is_resolved'])
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_resolved)

        reopened = self.set_status(self.creator, False)

        self.assertEqual(reopened.status_code, status.HTTP_200_OK)
        self.comment.refresh_from_db()
        self.assertFalse(self.comment.is_resolved)

    def test_other_reviewers_and_project_members_cannot_change_status(self):
        member = User.objects.create(username='rv_member', email='member@example.com')
        self.project.members.add(member)
        services.sync_reviewers(self.review, [self.reviewer_a.id, self.reviewer_b.id])

        for user in (self.reviewer_b, member):
            with self.subTest(user=user.username):
                response = self.set_status(user, True)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.comment.refresh_from_db()
        self.assertFalse(self.comment.is_resolved)

    def test_status_must_be_boolean(self):
        for value in ('true', 1, None):
            with self.subTest(value=value):
                response = self.set_status(self.creator, value)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        missing = self.client.patch(self.url, {}, format='json')

        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.comment.refresh_from_db()
        self.assertFalse(self.comment.is_resolved)

    def test_content_update_cannot_change_status(self):
        # 修改接口只改内容；状态只能通过 update_status 改
        self.client.force_authenticate(self.reviewer_a)

        response = self.client.patch(
            f'{COMMENT_URL}{self.comment.id}/', {'content': '改内容', 'is_resolved': True}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.comment.refresh_from_db()
        self.assertEqual((self.comment.content, self.comment.is_resolved), ('改内容', False))


class ClosedReviewCommentApiTests(ReviewTestBase):
    """评审结束后评审意见整体冻结：不能新增、修改、删除，也不能变更状态。"""

    def setUp(self):
        super().setUp()
        self.comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.creator, content='结束前的意见')
        self.url = f'{COMMENT_URL}{self.comment.id}/'

    def close_review(self, review_status='approved'):
        TestCaseReview.objects.filter(id=self.review.id).update(status=review_status)

    def test_comments_are_frozen_after_review_ends(self):
        self.close_review()

        created = self.client.post(COMMENT_URL, {
            'review': self.review.id, 'comment_type': 'general', 'content': '结束后的新意见',
        }, format='json')
        updated = self.client.patch(self.url, {'content': '结束后修改'}, format='json')
        status_changed = self.client.patch(
            f'{self.url}update_status/', {'is_resolved': True}, format='json')
        deleted = self.client.delete(self.url)

        for response in (created, updated, status_changed, deleted):
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('评审已结束', str(created.data['review']))
        self.assertIn('评审已结束', str(status_changed.data['detail']))
        self.assertIn('评审已结束', str(deleted.data['detail']))
        self.comment.refresh_from_db()
        self.assertEqual((self.comment.content, self.comment.is_resolved), ('结束前的意见', False))
        self.assertEqual(TestCaseReviewComment.objects.count(), 1)

    def test_every_closed_status_freezes_comments(self):
        for review_status in ('approved', 'rejected', 'cancelled'):
            with self.subTest(review_status=review_status):
                self.close_review(review_status)
                response = self.client.patch(
                    f'{self.url}update_status/', {'is_resolved': True}, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_in_progress_review_is_still_open(self):
        self.close_review('in_progress')

        response = self.client.patch(
            f'{self.url}update_status/', {'is_resolved': True}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_resolved)
