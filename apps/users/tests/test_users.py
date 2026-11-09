from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class EmailRegistrationTests(APITestCase):
    """邮箱注册相关用例。

    自助注册已关闭：/api/auth/register/ 路由已在 urls.py 中注释，
    下面依赖注册接口的用例一并注释掉，恢复注册时取消注释即可。
    """

    def setUp(self):
        # 自助注册已关闭，路由已注释，reverse('register') 会抛 NoReverseMatch
        # self.url = reverse('register')
        self.registration_data = {
            'username': 'new user',
            'email': 'newuser@example.com',
            'password': 'secret123',
            'password_confirm': 'secret123',
        }

    # 自助注册已关闭，恢复时取消注释
    # def test_registers_with_email_and_returns_jwt_tokens(self):
    #     response = self.client.post(self.url, self.registration_data, format='json')
    #
    #     self.assertEqual(response.status_code, status.HTTP_201_CREATED)
    #     self.assertTrue(response.data['access'])
    #     self.assertTrue(response.data['refresh'])
    #     user = get_user_model().objects.get(username='new user')
    #     self.assertEqual(user.email, 'newuser@example.com')
    #     self.assertFalse(user.phone)
    #     self.assertFalse(user.first_name)
    #     self.assertFalse(user.last_name)
    #
    #     login_response = self.client.post(reverse('login'), {
    #         'username': 'new user',
    #         'password': 'secret123',
    #     }, format='json')
    #     self.assertEqual(login_response.status_code, status.HTTP_200_OK)
    #
    #     email_login_response = self.client.post(reverse('login'), {
    #         'username': 'NEWUSER@example.com',
    #         'password': 'secret123',
    #     }, format='json')
    #     self.assertEqual(email_login_response.status_code, status.HTTP_200_OK)
    #
    # def test_rejects_an_email_that_is_already_registered(self):
    #     get_user_model().objects.create_user(
    #         username='existing',
    #         email='newuser@example.com',
    #         password='secret123',
    #     )
    #
    #     response = self.client.post(self.url, self.registration_data, format='json')
    #
    #     self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    #     self.assertEqual(response.data['error'], '该邮箱已被注册')
    #
    # def test_rejects_registration_without_email(self):
    #     data = {**self.registration_data, 'email': ''}
    #
    #     response = self.client.post(self.url, data, format='json')
    #
    #     self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    #     self.assertIn('email', response.data['details'])

    def test_login_validation_prompts_for_username_or_email(self):
        response = self.client.post(reverse('login'), {
            'username': '',
            'password': '',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error_code'], 'credentials_required')
        self.assertEqual(response.data['error'], '请输入用户名或邮箱和密码')


class UserProfileUpdateTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='profile user',
            email='profile@example.com',
            password='secret123',
        )
        self.client.force_authenticate(self.user)
        self.url = reverse('profile')

    def test_updates_username_and_keeps_email_unchanged(self):
        response = self.client.patch(self.url, {
            'username': 'updated profile',
            'email': 'changed@example.com',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'updated profile')
        self.assertEqual(self.user.email, 'profile@example.com')
        self.assertEqual(response.data['email'], 'profile@example.com')

    def test_rejects_username_already_in_use(self):
        get_user_model().objects.create_user(
            username='taken name',
            email='taken@example.com',
            password='secret123',
        )

        response = self.client.patch(self.url, {'username': 'taken name'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('username', response.data)


class UserAdminOnlyWriteTests(APITestCase):
    """只能管理员建号：普通登录用户可读用户列表，但增/改/删一律 403。"""

    def setUp(self):
        self.normal = get_user_model().objects.create_user(
            username='normal user',
            email='normal@example.com',
            password='secret123',
        )
        self.admin = get_user_model().objects.create_user(
            username='staff admin',
            email='staff@example.com',
            password='secret123',
            is_staff=True,
        )
        # 注意：'user-list' / 'user-detail' 这两个 URL name 与 api_testing 应用重名，
        # reverse() 会解析到 /api/api-testing/users/，所以这里直接写死 users 应用的真实路径。
        self.list_url = '/api/auth/users/'
        self.detail_url = f'/api/auth/users/{self.normal.pk}/'

    def test_authenticated_user_can_read_user_list(self):
        self.client.force_authenticate(self.normal)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_anonymous_cannot_read_user_list(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_normal_user_cannot_create_user(self):
        self.client.force_authenticate(self.normal)
        response = self.client.post(
            self.list_url,
            {'username': 'self made', 'email': 'selfmade@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(get_user_model().objects.filter(username='self made').exists())

    def test_normal_user_cannot_update_other_user(self):
        self.client.force_authenticate(self.normal)
        response = self.client.patch(
            f'/api/auth/users/{self.admin.pk}/',
            {'is_active': False},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_normal_user_cannot_delete_user(self):
        target = get_user_model().objects.create_user(
            username='delete me', email='delete@example.com', password='secret123'
        )
        self.client.force_authenticate(self.normal)
        response = self.client.delete(f'/api/auth/users/{target.pk}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(get_user_model().objects.filter(pk=target.pk).exists())

    def test_admin_can_create_user(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.list_url,
            {'username': 'created by admin', 'email': 'byadmin@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            get_user_model().objects.filter(username='created by admin').exists()
        )

    def test_admin_can_update_user(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            self.detail_url, {'department': '测试部'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.normal.refresh_from_db()
        self.assertEqual(self.normal.department, '测试部')
