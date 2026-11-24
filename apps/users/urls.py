from django.urls import path
from . import views
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('me/', views.get_current_user, name='get_current_user'),
    # 自助注册已关闭：路由下线，RegisterView 与 UserCreateSerializer 均保留，恢复时取消下面注释即可。
    # path('register/', views.RegisterView.as_view(), name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),
    path('avatar/', views.avatar_view, name='avatar'),
    path('change-password/', views.change_password_view, name='change-password'),
    path('users/', views.UserListView.as_view(), name='user-list'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user-detail'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),  # JWT token刷新
    # 短信注册/登录已停用，保留路由定义以便后续恢复。
    # path('captcha/', views.get_captcha, name='get_captcha'),
    # path('send-register-code/', views.send_register_code, name='send_register_code'),
    # path('sms-login/', views.sms_login_view, name='sms_login'),
    path('exchange-token/', views.exchange_token_view, name='exchange_token'),  # SSO 授权码交换
]
