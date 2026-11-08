from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from django.contrib.auth import login, logout
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from .avatar import delete_user_avatar, save_user_avatar
from .models import User
from .serializers import (
    UserSerializer,
    UserCreateSerializer,
    LoginSerializer,
    ChangePasswordSerializer,
    UserProfileUpdateSerializer,
    AvatarUploadSerializer,
)

# JWT 相关导入
from rest_framework_simplejwt.tokens import RefreshToken

# 短信注册/登录已停用；保留依赖和视图实现注释，恢复时再启用。
# from .captcha import create_captcha
# from .sms import send_register_verify_code, validate_verify_code

from .redis_client import get_redis
import uuid

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def get_current_user(request):
    serializer = UserSerializer(request.user)
    return Response(serializer.data)

# 自助注册已关闭：对应的 /api/auth/register/ 路由已在 urls.py 中注释，
# 视图与序列化器保留，恢复注册时把 urls.py 里那一行取消注释即可。
@method_decorator(csrf_exempt, name='dispatch')
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all().order_by('username')
    serializer_class = UserCreateSerializer
    permission_classes = [permissions.AllowAny]
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            errors = serializer.errors
            message = next(
                (str(item) for value in errors.values() for item in value if isinstance(item, str)),
                '注册信息不正确',
            )
            return Response({'error': message, 'details': errors}, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        
        return Response({
            'user': UserSerializer(user).data,
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }, status=status.HTTP_201_CREATED)

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@csrf_exempt
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        errors = serializer.errors
        message = errors.get('non_field_errors') or errors.get('username') or errors.get('password')
        if isinstance(message, (list, tuple)):
            message = message[0] if message else None
        return Response({
            'error': str(message or '登录参数不正确'),
            'error_code': getattr(message, 'code', 'invalid_login_request'),
        }, status=status.HTTP_400_BAD_REQUEST)

    user = serializer.validated_data['user']
    login(request, user)

    # JWT Token (优先使用JWT)
    refresh = RefreshToken.for_user(user)
    access_token = str(refresh.access_token)
    refresh_token = str(refresh)

    return Response({
        'user': UserSerializer(user).data,
        'access': access_token,       # JWT access token
        'refresh': refresh_token,     # JWT refresh token
        'message': '登录成功'
    })

@api_view(['POST'])
@csrf_exempt
def logout_view(request):
    """用户退出登录，将refresh token加入黑名单"""
    if request.user.is_authenticated:
        # 将 refresh token 加入黑名单
        refresh_token = request.data.get('refresh')
        if refresh_token:
            try:
                from rest_framework_simplejwt.tokens import RefreshToken as JWTRefreshToken
                JWTRefreshToken(refresh_token).blacklist()
            except Exception:
                pass

        logout(request)

    return Response({'message': '退出成功'})

@api_view(['GET', 'PATCH'])
def profile_view(request):
    if not request.user.is_authenticated:
        return Response({'error': '未登录'}, status=status.HTTP_401_UNAUTHORIZED)

    if request.method == 'PATCH':
        serializer = UserProfileUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)
    
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


@api_view(['POST', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def avatar_view(request):
    """当前登录用户的头像：POST 上传/替换，DELETE 移除。"""
    if request.method == 'DELETE':
        delete_user_avatar(request.user)
        return Response(UserSerializer(request.user).data)

    serializer = AvatarUploadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    save_user_avatar(request.user, serializer.validated_data['avatar'])
    return Response(UserSerializer(request.user).data)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def change_password_view(request):
    """修改当前登录用户密码。"""
    serializer = ChangePasswordSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    if not request.user.check_password(serializer.validated_data['current_password']):
        return Response({'current_password': '当前密码错误'}, status=status.HTTP_400_BAD_REQUEST)

    request.user.set_password(serializer.validated_data['new_password'])
    request.user.save(update_fields=['password', 'updated_at'])
    return Response({'message': '密码修改成功'})

class IsAdminOrReadOnly(permissions.BasePermission):
    """用户资源的权限：读接口对所有登录用户开放，写接口仅管理员。

    - GET/HEAD/OPTIONS：任意已登录用户（前端大量页面需要拉用户列表做「指派/负责人」下拉）
    - POST/PUT/PATCH/DELETE：仅 is_staff / is_superuser，用于「只能管理员建号」
    """

    message = '仅管理员可执行该操作'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(user.is_staff or user.is_superuser)


class UserListView(generics.ListCreateAPIView):
    queryset = User.objects.all().order_by('username')
    serializer_class = UserSerializer
    permission_classes = [IsAdminOrReadOnly]

class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all().order_by('username')
    serializer_class = UserSerializer
    permission_classes = [IsAdminOrReadOnly]


# 短信注册/登录接口暂时停用，避免短信逻辑参与当前邮箱注册和密码登录流程。
# @api_view(['GET'])
# @permission_classes([permissions.AllowAny])
# def get_captcha(request):
#     """获取图形验证码"""
#     return Response(create_captcha())
#
#
# @api_view(['POST'])
# @permission_classes([permissions.AllowAny])
# @csrf_exempt
# def send_register_code(request):
#     """发送短信验证码（注册/登录通用）"""
#     phone = request.data.get('phone', '').strip()
#     captcha_token = request.data.get('captcha_token', '')
#     captcha_code = request.data.get('captcha_code', '')
#     mode = request.data.get('mode', 'register')
#
#     if not phone:
#         return Response({'success': False, 'error': '请输入手机号', 'error_code': 'phone_required'}, status=status.HTTP_400_BAD_REQUEST)
#     if not captcha_token or not captcha_code:
#         return Response({'success': False, 'error': '请输入图形验证码', 'error_code': 'captcha_required'}, status=status.HTTP_400_BAD_REQUEST)
#     if mode == 'login' and not User.objects.filter(phone=phone).exists():
#         return Response({'success': False, 'error': '该手机号未注册，请先注册', 'error_code': 'phone_not_registered'}, status=status.HTTP_400_BAD_REQUEST)
#     if mode == 'register' and User.objects.filter(phone=phone).exists():
#         return Response({'success': False, 'error': '该手机号已被注册', 'error_code': 'phone_registered'}, status=status.HTTP_400_BAD_REQUEST)
#
#     result = send_register_verify_code(phone, captcha_token, captcha_code)
#     return Response(result, status=status.HTTP_200_OK if result.get('success') else status.HTTP_400_BAD_REQUEST)
#
#
# @api_view(['POST'])
# @permission_classes([permissions.AllowAny])
# @csrf_exempt
# def sms_login_view(request):
#     """短信验证码登录：校验短信码后返回 JWT。"""
#     phone = request.data.get('phone', '').strip()
#     verify_code = request.data.get('verify_code', '').strip()
#     verify_code_token = request.data.get('verify_code_token', '').strip()
#     if not phone:
#         return Response({'error': '请输入手机号', 'error_code': 'phone_required'}, status=status.HTTP_400_BAD_REQUEST)
#     if not re.match(r'^1[3-9]\d{9}$', phone):
#         return Response({'error': '手机号格式不正确', 'error_code': 'phone_invalid'}, status=status.HTTP_400_BAD_REQUEST)
#     if not verify_code or not verify_code_token:
#         return Response({'error': '请输入短信验证码', 'error_code': 'sms_code_required'}, status=status.HTTP_400_BAD_REQUEST)
#
#     valid, error = validate_verify_code(phone, verify_code_token, verify_code)
#     if not valid:
#         error_codes = {
#             '验证码已过期，请重新获取': 'sms_code_expired',
#             '验证码错误': 'sms_code_invalid',
#             '手机号与验证码不匹配': 'sms_code_phone_mismatch',
#         }
#         return Response({'error': error, 'error_code': error_codes.get(error, 'sms_code_invalid')}, status=status.HTTP_400_BAD_REQUEST)
#
#     user = User.objects.filter(phone=phone).first()
#     if not user:
#         return Response({'error': '该手机号未注册，请先注册', 'error_code': 'phone_not_registered'}, status=status.HTTP_400_BAD_REQUEST)
#     if not user.is_active:
#         return Response({'error': '用户已被禁用', 'error_code': 'account_disabled'}, status=status.HTTP_400_BAD_REQUEST)
#
#     refresh = RefreshToken.for_user(user)
#     return Response({
#         'user': UserSerializer(user).data,
#         'access': str(refresh.access_token),
#         'refresh': str(refresh),
#         'message': '登录成功',
#     })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@csrf_exempt
def exchange_token_view(request):
    """
    一次性授权码交换
    action=create:  用当前 JWT 换取一次性 code（需登录），60 秒有效
    action=redeem:  用 code 换取 JWT（无需登录），一次性使用
    """
    action = request.data.get('action', '')
    redis_client = get_redis()

    if action == 'create':
        if not request.user.is_authenticated:
            return Response({'error': '未登录'}, status=status.HTTP_401_UNAUTHORIZED)
        code = str(uuid.uuid4()).replace('-', '')[:8]
        redis_client.setex(f"exchange:{code}", 60, request.user.id)
        return Response({'code': code})

    elif action == 'redeem':
        code = request.data.get('code', '').strip()
        if not code:
            return Response({'error': '缺少授权码'}, status=status.HTTP_400_BAD_REQUEST)

        user_id = redis_client.get(f"exchange:{code}")
        if user_id is None:
            return Response({'error': '授权码无效或已过期'}, status=status.HTTP_400_BAD_REQUEST)

        # 立即删除，保证一次性使用
        redis_client.delete(f"exchange:{code}")

        try:
            user = User.objects.get(id=int(user_id))
        except User.DoesNotExist:
            return Response({'error': '用户不存在'}, status=status.HTTP_400_BAD_REQUEST)

        if not user.is_active:
            return Response({'error': '用户已被禁用'}, status=status.HTTP_400_BAD_REQUEST)

        refresh = RefreshToken.for_user(user)
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
        })

    else:
        return Response({'error': '无效的 action'}, status=status.HTTP_400_BAD_REQUEST)
