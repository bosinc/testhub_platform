"""后台（Django admin / SimpleUI）使用的用户表单。

后台需求：
1. 新增、修改用户时，用户名允许包含空格（模型层已放宽校验器，这里保证表单沿用模型校验）；
2. 新增用户时可以直接填写邮箱，修改用户时邮箱可编辑，并校验邮箱不与他人重复。
"""

from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import User


class EmailUniqueMixin:
    """邮箱唯一性校验。

    仅在邮箱被修改时校验，避免历史数据中已存在的重复邮箱导致无法编辑其他字段。
    """

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if not email:
            return email

        if self.instance.pk:
            old_email = (
                User.objects.filter(pk=self.instance.pk)
                .values_list('email', flat=True)
                .first()
            ) or ''
            # 邮箱没有变化时不校验，方便维护历史数据
            if old_email.strip().lower() == email.lower():
                return email
            queryset = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        else:
            queryset = User.objects.filter(email__iexact=email)

        if queryset.exists():
            raise ValidationError(_('该邮箱已被其他用户使用，请更换邮箱。'))
        return email


class AdminUserAddForm(EmailUniqueMixin, AdminUserCreationForm):
    """后台新增用户表单：在用户名、密码之外补充邮箱字段。"""

    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ('username', 'email')


class AdminUserChangeForm(EmailUniqueMixin, UserChangeForm):
    """后台修改用户表单：邮箱可编辑，密码仍为只读哈希。"""

    class Meta(UserChangeForm.Meta):
        model = User
