# -*- coding: utf-8 -*-
"""MCP 用例评审工具单测：读工具 + preview → confirm 写工具。

运行: python manage.py test apps.mcp.tests.test_tools_reviews
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp import tools as mcp_tools
from apps.mcp.models import McpPendingConfirm
from apps.mcp.tests.test_tools import ToolBase
from apps.mcp.tests.utils import ctx_with_jwt
from apps.projects.models import Project
from apps.reviews import services as review_services
from apps.reviews.models import ReviewAssignment, TestCaseReview, TestCaseReviewComment
from apps.testcases.models import TestCase as FunctionalTestCase

User = get_user_model()


class ReviewToolBase(ToolBase):
    def setUp(self):
        super().setUp()
        self.reviewer_a = User.objects.create(username='mcp_reviewer_a', email='ra@a.com')
        self.reviewer_b = User.objects.create(username='mcp_reviewer_b', email='rb@a.com')
        self.reviewer_ctx, _ = ctx_with_jwt(self.reviewer_a)
        self.other_ctx, _ = ctx_with_jwt(self.other)
        self.case = FunctionalTestCase.objects.create(
            project=self.project, title='登录成功', author=self.user)
        self.outside_case = FunctionalTestCase.objects.create(
            project=self.project, title='未纳入评审', author=self.user)
        self.review = review_services.create_review(
            creator=self.user,
            fields={'title': '登录用例评审'},
            project_ids=[self.project.id],
            testcase_ids=[self.case.id],
            reviewer_ids=[self.reviewer_a.id],
        )

    def approve(self, reviewer):
        ReviewAssignment.objects.filter(review=self.review, reviewer=reviewer).update(
            status='approved', comment='LGTM', reviewed_at=timezone.now())

    def assignment(self, reviewer):
        return ReviewAssignment.objects.get(review=self.review, reviewer=reviewer)

    def reviewer_ids(self):
        return sorted(ReviewAssignment.objects.filter(
            review=self.review).values_list('reviewer_id', flat=True))

    def update_review(self, data, ctx=None):
        ctx = ctx or self.ctx
        preview = mcp_tools.preview_update_review(ctx, review_id=self.review.id, data=data)
        return preview, mcp_tools.confirm_update_review(ctx, preview['confirm_token'])

    def add_comment(self, ctx=None, **arguments):
        ctx = ctx or self.ctx
        preview = mcp_tools.preview_add_review_comment(
            ctx, review_id=self.review.id, **arguments)
        return preview, mcp_tools.confirm_add_review_comment(ctx, preview['confirm_token'])

    def update_comment(self, comment, data, ctx=None):
        ctx = ctx or self.ctx
        preview = mcp_tools.preview_update_review_comment(ctx, comment_id=comment.id, data=data)
        return mcp_tools.confirm_update_review_comment(ctx, preview['confirm_token'])


class ReadReviewToolTest(ReviewToolBase):
    def test_list_counts_are_not_affected_by_visibility_or_filter_joins(self):
        # 另一个评审：当前用户既不是创建人也不是项目成员，只因评审人身份可见
        foreign_project = Project.objects.create(name='别人项目', owner=self.other)
        foreign_review = review_services.create_review(
            creator=self.other, fields={'title': '别人发起的评审'},
            project_ids=[foreign_project.id],
            reviewer_ids=[self.user.id, self.reviewer_a.id, self.reviewer_b.id],
        )
        review_services.sync_reviewers(self.review, [self.reviewer_a.id, self.reviewer_b.id])

        items = {item['id']: item for item in mcp_tools.list_reviews(self.ctx)['items']}
        filtered = mcp_tools.list_reviews(self.ctx, reviewer_id=self.reviewer_a.id)

        self.assertEqual(items[foreign_review.id]['reviewer_count'], 3)
        self.assertEqual(items[self.review.id]['reviewer_count'], 2)
        self.assertEqual(items[self.review.id]['testcase_count'], 1)
        self.assertEqual(filtered['total'], 2)
        self.assertEqual({item['reviewer_count'] for item in filtered['items']}, {2, 3})

    def test_list_filters_status_and_rejects_unknown_status(self):
        TestCaseReview.objects.filter(id=self.review.id).update(status='approved')

        self.assertEqual(mcp_tools.list_reviews(self.ctx, status='approved')['total'], 1)
        self.assertEqual(mcp_tools.list_reviews(self.ctx, status='pending')['total'], 0)
        self.assertEqual(mcp_tools.list_reviews(self.other_ctx)['total'], 0)
        with self.assertRaises(ToolError):
            mcp_tools.list_reviews(self.ctx, status='done')

    def test_get_review_returns_detail_with_bounded_queries(self):
        cases = [FunctionalTestCase.objects.create(
            project=self.project, title=f'批量用例 {index}',
            author=User.objects.create(username=f'case_author_{index}', email=f'c{index}@a.com'))
            for index in range(10)]
        self.review.testcases.add(*cases)
        for case in cases:
            TestCaseReviewComment.objects.create(
                review=self.review, author=self.reviewer_a, testcase=case,
                comment_type='testcase', content='请补充断言')

        with CaptureQueriesContext(connection) as queries:
            detail = mcp_tools.get_review(self.ctx, review_id=self.review.id)

        self.assertEqual(detail['title'], '登录用例评审')
        self.assertEqual(len(detail['testcases']), 11)
        self.assertEqual(len(detail['comments']), 10)
        self.assertEqual(detail['assignments'][0]['reviewer']['id'], self.reviewer_a.id)
        # 查询数与用例数、意见数无关
        self.assertLessEqual(len(queries), 16)

    def test_get_review_requires_visibility(self):
        with self.assertRaises(ToolError):
            mcp_tools.get_review(self.other_ctx, review_id=self.review.id)
        # 评审人不是项目成员，也能查看
        self.assertEqual(
            mcp_tools.get_review(self.reviewer_ctx, review_id=self.review.id)['id'], self.review.id)

    def test_get_review_comments_paginates_and_filters_by_testcase(self):
        self.review.testcases.add(self.outside_case)
        for index in range(3):
            TestCaseReviewComment.objects.create(
                review=self.review, author=self.user, content=f'整体意见 {index}')
        TestCaseReviewComment.objects.create(
            review=self.review, author=self.user, testcase=self.case,
            comment_type='testcase', content='用例意见')

        page = mcp_tools.get_review_comments(self.ctx, review_id=self.review.id, limit=2)
        on_case = mcp_tools.get_review_comments(
            self.ctx, review_id=self.review.id, testcase_id=self.case.id)

        self.assertEqual((page['total'], len(page['items'])), (4, 2))
        self.assertEqual(on_case['total'], 1)
        self.assertEqual(on_case['items'][0]['testcase']['id'], self.case.id)
        with self.assertRaises(ToolError):
            mcp_tools.get_review_comments(self.other_ctx, review_id=self.review.id)


class UpdateReviewToolTest(ReviewToolBase):
    def test_adding_reviewer_keeps_submitted_verdict(self):
        self.approve(self.reviewer_a)

        _, result = self.update_review({
            'title': '登录模块用例评审', 'priority': 'high',
            'reviewers': [self.reviewer_a.id, self.reviewer_b.id, self.reviewer_b.id],
        })

        self.review.refresh_from_db()
        self.assertEqual((self.review.title, self.review.priority), ('登录模块用例评审', 'high'))
        self.assertEqual(self.reviewer_ids(), sorted([self.reviewer_a.id, self.reviewer_b.id]))
        self.assertEqual(self.assignment(self.reviewer_a).status, 'approved')
        self.assertEqual(result['reviewers_added'], [self.reviewer_b.id])
        self.assertEqual(result['reviewers_removed'], [])

    def test_removing_reviewer_with_verdict_is_stated_in_preview(self):
        self.approve(self.reviewer_a)

        preview, result = self.update_review({'reviewers': [self.reviewer_b.id]})

        self.assertIn('移除 1 位评审人', preview['preview'])
        self.assertIn('1 位已提交的评审结论将一并删除', preview['preview'])
        self.assertEqual(self.reviewer_ids(), [self.reviewer_b.id])
        self.assertEqual(result['verdicts_removed'], 1)

    def test_invalid_data_is_rejected_without_partial_write(self):
        foreign = Project.objects.create(name='别人项目', owner=self.other)
        foreign_case = FunctionalTestCase.objects.create(
            project=foreign, title='别人项目的用例', author=self.other)
        invalid_payloads = [
            {'title': 'X', 'projects': [foreign.id]},
            {'title': 'X', 'priority': 'zzz'},
            {'title': 'X', 'status': 'approved'},
            {'title': 'X', 'deadline': 'not-a-date'},
            {'title': 'X', 'reviewers': [987654]},
            {'title': 'X', 'reviewers': []},
            {'title': 'X', 'testcases': [self.case.id, foreign_case.id]},
            {'title': 'X', 'template': 424242},
            {'title': ''},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ToolError):
                mcp_tools.preview_update_review(self.ctx, review_id=self.review.id, data=payload)

        self.review.refresh_from_db()
        self.assertEqual((self.review.title, self.review.status), ('登录用例评审', 'pending'))
        self.assertEqual(self.reviewer_ids(), [self.reviewer_a.id])
        self.assertFalse(McpPendingConfirm.objects.exists())

    def test_testcases_replacement_accepts_cases_of_review_projects(self):
        _, result = self.update_review({'testcases': [self.case.id, self.outside_case.id]})

        self.assertEqual(
            sorted(self.review.testcases.values_list('id', flat=True)),
            sorted([self.case.id, self.outside_case.id]))
        self.assertEqual((result['testcases_added'], result['testcases_removed']), (1, 0))

    def test_only_creator_can_update_open_review(self):
        with self.assertRaises(ToolError):
            mcp_tools.preview_update_review(
                self.reviewer_ctx, review_id=self.review.id, data={'title': '评审人改标题'})

        TestCaseReview.objects.filter(id=self.review.id).update(status='approved')
        with self.assertRaises(ToolError):
            mcp_tools.preview_update_review(
                self.ctx, review_id=self.review.id, data={'title': '结束后改标题'})


class ReviewCommentToolTest(ReviewToolBase):
    def test_add_general_comment_after_confirm(self):
        preview = mcp_tools.preview_add_review_comment(
            self.ctx, review_id=self.review.id, content='整体覆盖完整')
        self.assertIn('整体意见', preview['preview'])
        self.assertFalse(TestCaseReviewComment.objects.exists())

        result = mcp_tools.confirm_add_review_comment(self.ctx, preview['confirm_token'])

        comment = TestCaseReviewComment.objects.get(id=result['comment_id'])
        self.assertEqual(
            (comment.review_id, comment.author_id, comment.comment_type, comment.content),
            (self.review.id, self.user.id, 'general', '整体覆盖完整'))
        self.assertIsNone(result['testcase_id'])

    def test_reviewer_can_add_testcase_comment(self):
        preview, result = self.add_comment(
            ctx=self.reviewer_ctx, comment_type='testcase',
            testcase_id=self.case.id, content='请补充异常场景')

        self.assertIn('登录成功', preview['preview'])
        self.assertEqual(result['testcase_id'], self.case.id)
        self.assertEqual(
            TestCaseReviewComment.objects.get(id=result['comment_id']).author, self.reviewer_a)

    def test_invalid_comments_are_rejected_at_preview(self):
        invalid_arguments = [
            {'content': '   '},
            {'content': '无效意见', 'comment_type': 'testcase'},
            {'content': '无效意见', 'comment_type': 'testcase', 'testcase_id': self.outside_case.id},
            {'content': '无效意见', 'testcase_id': self.case.id},
            {'content': '无效意见', 'comment_type': 'bogus'},
        ]
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ToolError):
                mcp_tools.preview_add_review_comment(
                    self.ctx, review_id=self.review.id, **arguments)
        with self.assertRaises(ToolError):
            mcp_tools.preview_add_review_comment(
                self.other_ctx, review_id=self.review.id, content='看不到的评审')

        self.assertFalse(TestCaseReviewComment.objects.exists())
        self.assertFalse(McpPendingConfirm.objects.exists())

    def test_update_comment_can_attach_testcase_by_id(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.user, content='原意见')

        result = self.update_comment(
            comment, {'comment_type': 'testcase', 'testcase': self.case.id, 'content': '改后的意见'})

        comment.refresh_from_db()
        self.assertEqual(
            (comment.comment_type, comment.testcase_id, comment.content),
            ('testcase', self.case.id, '改后的意见'))
        self.assertEqual(result['testcase_id'], self.case.id)

        self.update_comment(comment, {'comment_type': 'general'})
        comment.refresh_from_db()
        self.assertIsNone(comment.testcase_id)

    def test_update_comment_validates_like_create(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.user, content='原意见')

        for data in ({'content': '   '}, {'comment_type': 'bogus'}, {'is_resolved': 'yes'},
                     {'comment_type': 'testcase', 'testcase': self.outside_case.id}):
            with self.subTest(data=data), self.assertRaises(ToolError):
                mcp_tools.preview_update_review_comment(
                    self.ctx, comment_id=comment.id, data=data)

        comment.refresh_from_db()
        self.assertEqual((comment.content, comment.comment_type), ('原意见', 'general'))
        self.assertFalse(comment.is_resolved)

    def test_only_author_can_update_comment(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.reviewer_a, content='评审人的意见')

        with self.assertRaises(ToolError):
            mcp_tools.preview_update_review_comment(
                self.ctx, comment_id=comment.id, data={'content': '被创建人改'})
        # 创建人可以改状态，但不能借机一并改内容
        with self.assertRaises(ToolError):
            mcp_tools.preview_update_review_comment(
                self.ctx, comment_id=comment.id,
                data={'content': '被创建人改', 'is_resolved': True})

        comment.refresh_from_db()
        self.assertEqual((comment.content, comment.is_resolved), ('评审人的意见', False))

    def test_author_and_review_creator_can_change_status(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.reviewer_a, content='评审人的意见')

        preview = mcp_tools.preview_update_review_comment(
            self.ctx, comment_id=comment.id, data={'is_resolved': True})
        self.assertIn('标记为已解决', preview['preview'])
        result = mcp_tools.confirm_update_review_comment(self.ctx, preview['confirm_token'])

        comment.refresh_from_db()
        self.assertTrue(comment.is_resolved)
        self.assertEqual(result['is_resolved'], True)
        self.assertEqual(result['updated_fields'], ['is_resolved'])

        # 作者重新打开，并同时改内容
        result = self.update_comment(
            comment, {'is_resolved': False, 'content': '还没改完'}, ctx=self.reviewer_ctx)

        comment.refresh_from_db()
        self.assertEqual((comment.content, comment.is_resolved), ('还没改完', False))
        self.assertEqual(result['is_resolved'], False)

    def test_other_users_cannot_change_status(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.user, content='创建人的意见')
        review_services.sync_reviewers(self.review, [self.reviewer_a.id, self.reviewer_b.id])

        # reviewer_a 能看到评审，但既不是意见作者也不是评审创建人
        with self.assertRaises(ToolError):
            mcp_tools.preview_update_review_comment(
                self.reviewer_ctx, comment_id=comment.id, data={'is_resolved': True})

        comment.refresh_from_db()
        self.assertFalse(comment.is_resolved)

    def test_comments_are_frozen_after_review_ends(self):
        comment = TestCaseReviewComment.objects.create(
            review=self.review, author=self.user, content='结束前的意见')
        pending = mcp_tools.preview_update_review_comment(
            self.ctx, comment_id=comment.id, data={'is_resolved': True})
        TestCaseReview.objects.filter(id=self.review.id).update(status='approved')

        with self.assertRaises(ToolError):
            mcp_tools.preview_add_review_comment(
                self.ctx, review_id=self.review.id, content='结束后的新意见')
        for data in ({'content': '结束后修改'}, {'is_resolved': True}):
            with self.subTest(data=data), self.assertRaises(ToolError):
                mcp_tools.preview_update_review_comment(
                    self.ctx, comment_id=comment.id, data=data)
        # 评审结束前拿到的令牌，结束后确认同样被拒绝
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_review_comment(self.ctx, pending['confirm_token'])

        comment.refresh_from_db()
        self.assertEqual((comment.content, comment.is_resolved), ('结束前的意见', False))
        self.assertEqual(TestCaseReviewComment.objects.count(), 1)

    def test_get_review_comments_filters_by_status(self):
        TestCaseReviewComment.objects.create(
            review=self.review, author=self.user, content='未解决的意见')
        TestCaseReviewComment.objects.create(
            review=self.review, author=self.user, content='已解决的意见', is_resolved=True)

        unresolved = mcp_tools.get_review_comments(
            self.ctx, review_id=self.review.id, is_resolved=False)
        resolved = mcp_tools.get_review_comments(
            self.ctx, review_id=self.review.id, is_resolved=True)

        self.assertEqual(mcp_tools.get_review_comments(self.ctx, review_id=self.review.id)['total'], 2)
        self.assertEqual([item['content'] for item in unresolved['items']], ['未解决的意见'])
        self.assertEqual([item['content'] for item in resolved['items']], ['已解决的意见'])

    @override_settings(MCP_HUMAN_APPROVAL=True)
    def test_human_approval_holds_the_comment(self):
        _, result = self.add_comment(content='需要审批的意见')

        self.assertEqual(result['status'], 'awaiting_approval')
        self.assertFalse(TestCaseReviewComment.objects.exists())
        self.assertTrue(McpPendingConfirm.objects.filter(
            tool_name='confirm_add_review_comment', awaiting_human=True).exists())
