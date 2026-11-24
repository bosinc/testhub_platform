"""用例评审的可见范围、详情预取与写入逻辑。

REST 序列化器 / 视图与 MCP 工具共用本模块：
评审的创建、更新、评审人同步，以及评审意见的权限与冻结规则只在这里实现一份。
"""
from django.db import transaction
from django.db.models import Q

from apps.projects.models import Project
from apps.users.models import User
from .models import ReviewAssignment, ReviewTemplate, TestCaseReview

#: 「未传该参数」的占位：区别于显式传 None（清空）
UNSET = object()

#: 评审仍在进行中的状态：只在这些状态下允许编辑评审、提交结论，以及新增或改动评审意见
OPEN_STATUSES = ('pending', 'in_progress')


class ReviewRuleError(Exception):
    """评审业务规则不允许当前操作；消息面向最终用户，页面接口与 MCP 原样透出。"""


class ReviewPermissionError(ReviewRuleError):
    """当前用户不是允许执行该操作的人。"""


def accessible_reviews(user):
    """用户可见的评审：本人创建、被指派为评审人，或关联项目里有本人。"""
    projects = Project.objects.filter(Q(owner=user) | Q(members=user))
    return TestCaseReview.objects.filter(
        Q(creator=user) | Q(reviewers=user) | Q(projects__in=projects)
    ).distinct()


def review_detail_queryset():
    """评审详情的一次性预取链，覆盖 TestCaseReviewSerializer 逐层读取的关联。"""
    return TestCaseReview.objects.select_related('creator', 'template__creator').prefetch_related(
        'projects',
        'testcases__author',
        'reviewassignment_set__reviewer',
        'comments__author',
        'comments__testcase__author',
        'template__project',
        'template__default_reviewers',
    )


def is_review_open(review):
    """评审是否仍在进行中（待评审 / 评审中）。"""
    return review.status in OPEN_STATUSES


def check_review_open(review):
    """评审结束后评审意见整体冻结：不能新增、修改、删除，也不能变更解决状态。"""
    if not is_review_open(review):
        raise ReviewRuleError(
            f'评审已结束（{review.get_status_display()}），评审意见不能再新增、修改、删除或变更状态')


def is_comment_author(comment, user):
    """评审意见只能由作者本人修改、删除。"""
    return comment.author_id == user.id


def check_comment_status_changeable(comment, user):
    """意见的解决状态只能由意见作者或评审创建人修改，且评审须未结束。"""
    if user.id not in (comment.author_id, comment.review.creator_id):
        raise ReviewPermissionError('只有意见作者和评审创建人可以修改意见的解决状态')
    check_review_open(comment.review)


def set_comment_resolved(comment, user, is_resolved):
    """把评审意见标记为已解决 / 未解决；不满足规则时抛 ReviewRuleError。"""
    check_comment_status_changeable(comment, user)
    comment.is_resolved = is_resolved
    comment.save(update_fields=['is_resolved', 'updated_at'])
    return comment


def _to_id_list(values):
    """归一化为去重后的整数 ID 列表（保持顺序，忽略无法转成整数的值）。"""
    ids = []
    for value in values or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(ids))


def sync_reviewers(review, reviewer_ids, *, remove_missing=True):
    """把评审人同步为 reviewer_ids，返回 {'added': [...], 'removed': [...]}。

    差量同步：已有的分配原样保留（含已提交的结论与检查清单），只新增缺少的、
    移除不在名单里的；重复 ID 自动去重，不存在的用户忽略。
    remove_missing=False 时只追加、不移除。
    """
    wanted = _to_id_list(reviewer_ids)
    existing = set(
        ReviewAssignment.objects.filter(review=review).values_list('reviewer_id', flat=True))

    removed = sorted(existing - set(wanted)) if remove_missing else []
    if removed:
        ReviewAssignment.objects.filter(review=review, reviewer_id__in=removed).delete()

    candidates = [reviewer_id for reviewer_id in wanted if reviewer_id not in existing]
    known = set(User.objects.filter(id__in=candidates).values_list('id', flat=True))
    added = [reviewer_id for reviewer_id in candidates if reviewer_id in known]
    ReviewAssignment.objects.bulk_create(
        [ReviewAssignment(review=review, reviewer_id=reviewer_id) for reviewer_id in added])
    return {'added': added, 'removed': removed}


def _resolve_template(template_id):
    """按 ID 取评审模板；未传或不存在返回 None。"""
    if not template_id:
        return None
    return ReviewTemplate.objects.filter(id=template_id).first()


@transaction.atomic
def create_review(*, creator, fields, project_ids=None, testcase_ids=None,
                  reviewer_ids=None, template_id=None):
    """创建评审及其项目、用例、评审人关联。

    fields 是 TestCaseReview 自身的普通字段（title / description / priority / deadline）。
    """
    review = TestCaseReview.objects.create(
        creator=creator, template=_resolve_template(template_id), **fields)
    if project_ids:
        review.projects.set(project_ids)
    if testcase_ids:
        review.testcases.set(testcase_ids)
    sync_reviewers(review, reviewer_ids)
    return review


@transaction.atomic
def update_review(review, *, fields=None, project_ids=None, testcase_ids=None,
                  reviewer_ids=None, template_id=UNSET):
    """更新评审，只处理显式传入的部分（None / UNSET 表示该部分不改）。

    template_id 传 None / 0 清空模板；评审人按 sync_reviewers 差量同步，
    留在名单里的评审人不会丢失已提交的结论。
    """
    for name, value in (fields or {}).items():
        setattr(review, name, value)
    if template_id is not UNSET:
        review.template = _resolve_template(template_id)
    review.save()

    if project_ids is not None:
        review.projects.set(project_ids)
    if testcase_ids is not None:
        review.testcases.set(testcase_ids)
    if reviewer_ids is not None:
        sync_reviewers(review, reviewer_ids)
    return review
