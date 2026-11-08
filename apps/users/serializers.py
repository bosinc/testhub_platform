import re
from rest_framework import serializers
from django.contrib.auth import authenticate
from .avatar import AVATAR_ALLOWED_CONTENT_TYPES, AVATAR_MAX_SIZE
from .models import User, UserProfile

class UserSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'avatar')

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 
                 'avatar', 'phone', 'department', 'position', 'is_active',
                 'date_joined', 'created_at', 'updated_at']
        read_only_fields = ['id', 'date_joined', 'created_at', 'updated_at']

class UserCreateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(min_length=3, max_length=20, validators=[])
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, min_length=6)
    password_confirm = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'password_confirm', 'department', 'position']

    def validate_username(self, value):
        # 保留 Django 用户名允许的字符，并额外允许用户名中间包含空格。
        if not re.fullmatch(r'[\w.@+\- ]+', value):
            raise serializers.ValidationError('用户名只能包含字母、数字、空格及 @ . + - _')
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('该用户名已被使用')
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('该邮箱已被注册')
        return value
    
    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError("密码不一致")
        return attrs
    
    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(min_length=3, max_length=20, validators=[])
    email = serializers.EmailField(read_only=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'department', 'position']

    def validate_username(self, value):
        # 与注册规则保持一致，允许用户名中间包含空格。
        if not re.fullmatch(r'[\w.@+\- ]+', value):
            raise serializers.ValidationError('用户名只能包含字母、数字、空格及 @ . + - _')
        if User.objects.filter(username=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError('该用户名已被使用')
        return value

class AvatarUploadSerializer(serializers.Serializer):
    """头像上传校验：格式、大小。

    DRF 的 ImageField 已经用 Pillow 校验过「是不是合法图片」，
    这里再补充大小与 content-type 限制。
    """

    avatar = serializers.ImageField()

    def validate_avatar(self, value):
        if value.size > AVATAR_MAX_SIZE:
            raise serializers.ValidationError('头像大小不能超过 2MB')

        content_type = (getattr(value, 'content_type', '') or '').lower()
        if content_type and content_type not in AVATAR_ALLOWED_CONTENT_TYPES:
            raise serializers.ValidationError('头像仅支持 JPG、PNG、WebP、GIF 格式的图片')

        return value


class LoginSerializer(serializers.Serializer):
    # 使用自定义校验文案，避免 DRF 的默认英文字段错误落到“登录失败”兜底提示。
    username = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(required=False, allow_blank=True, allow_null=True, trim_whitespace=False)
    
    def validate(self, attrs):
        username = (attrs.get('username') or '').strip()
        password = attrs.get('password') or ''

        if not username and not password:
            raise serializers.ValidationError('请输入用户名或邮箱和密码', code='credentials_required')
        if not username:
            raise serializers.ValidationError('请输入用户名或邮箱', code='username_required')
        if not password:
            raise serializers.ValidationError('请输入密码', code='password_required')

        # 先查用户再校验密码，这样停用用户可以得到明确提示；密码不匹配仍使用
        # 统一文案，避免暴露普通账号是否存在。
        user = User.objects.filter(username=username).first()
        if not user:
            user = User.objects.filter(email__iexact=username).first()
        if user and not user.is_active:
            raise serializers.ValidationError('用户已被禁用', code='account_disabled')
        authenticated_user = authenticate(username=user.username, password=password) if user else None
        if not authenticated_user:
            raise serializers.ValidationError('用户名或密码错误', code='invalid_credentials')
        
        attrs['user'] = authenticated_user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, min_length=6, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({'confirm_password': '两次输入的新密码不一致'})
        return attrs

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = '__all__'
