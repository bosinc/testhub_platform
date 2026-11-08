from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .forms import AdminUserAddForm, AdminUserChangeForm
from .models import User, UserProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = AdminUserChangeForm
    add_form = AdminUserAddForm

    list_display = ['username', 'email', 'first_name', 'last_name', 'department', 'position', 'is_active', 'date_joined']
    list_filter = ['is_active', 'department', 'position']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    readonly_fields = ['created_at', 'updated_at']

    # 修改用户：用户名允许空格；邮箱可直接编辑
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'email')}),
        ('扩展信息', {'fields': ('phone', 'department', 'position', 'avatar')}),
        (
            _('Permissions'),
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                ),
            },
        ),
        (_('Important dates'), {'fields': ('last_login', 'date_joined', 'created_at', 'updated_at')}),
    )

    # 新增用户：在用户名、密码之外，支持直接填写邮箱
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('username', 'email', 'usable_password', 'password1', 'password2'),
            },
        ),
        (
            '扩展信息',
            {
                'classes': ('wide',),
                'fields': ('phone', 'department', 'position', 'is_active'),
            },
        ),
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'theme', 'language', 'timezone']
