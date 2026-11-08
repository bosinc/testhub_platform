from django.contrib.auth.models import AbstractUser
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class UsernameAllowSpaceValidator(validators.RegexValidator):
    """
    用户名校验器：在 Django 默认规则基础上额外允许空格。
    """

    regex = r'^[\w.@+\- ]+\Z'
    message = _('请输入有效的用户名。可包含字母、数字、空格以及 @/./+/-/_ 字符。')
    flags = 0


class User(AbstractUser):
    """扩展用户模型"""

    # 覆盖 AbstractUser 的 username 字段：允许用户名中包含空格
    username = models.CharField(
        _('username'),
        max_length=150,
        unique=True,
        help_text=_('必填。150 个字符或更少。可包含字母、数字、空格以及 @/./+/-/_ 字符。'),
        validators=[UsernameAllowSpaceValidator()],
        error_messages={
            'unique': _('该用户名已存在。'),
        },
    )

    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True, verbose_name='头像')
    phone = models.CharField(max_length=11, null=True, blank=True, verbose_name='手机号')
    department = models.CharField(max_length=100, null=True, blank=True, verbose_name='部门')
    position = models.CharField(max_length=100, null=True, blank=True, verbose_name='职位')
    is_active = models.BooleanField(default=True, verbose_name='是否激活')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    
    def clean(self):
        super().clean()
        # 允许空格，但不允许用户名只由空格组成
        if self.username is not None and not self.username.strip():
            raise ValidationError({'username': _('用户名不能只包含空格。')})

    class Meta:
        db_table = 'users_user'
        verbose_name = '用户'
        verbose_name_plural = '用户'


class RegistrationStatsProxy(User):
    """用于后台展示注册统计，不新增数据表"""

    class Meta:
        proxy = True
        verbose_name = '注册统计'
        verbose_name_plural = '注册统计'

class UserProfile(models.Model):
    """用户配置"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    theme = models.CharField(max_length=20, default='light', verbose_name='主题')
    language = models.CharField(max_length=10, default='zh-cn', verbose_name='语言')
    timezone = models.CharField(max_length=50, default='Asia/Shanghai', verbose_name='时区')
    notifications = models.JSONField(default=dict, verbose_name='通知设置')
    
    class Meta:
        db_table = 'user_profiles'
        verbose_name = '用户配置'
        verbose_name_plural = '用户配置'
