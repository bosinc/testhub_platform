"""用户头像处理：格式/大小校验、裁剪压缩、替换与删除。"""

import io
import uuid

from django.core.files.base import ContentFile

# 允许的头像大小与格式
AVATAR_MAX_SIZE = 2 * 1024 * 1024  # 2MB
AVATAR_ALLOWED_CONTENT_TYPES = {
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/webp',
    'image/gif',
}
# 头像统一裁成正方形并压缩到该尺寸，避免大图拖慢列表/评论区
AVATAR_TARGET_SIZE = 256


def _build_avatar_file(uploaded_file):
    """把上传图片裁成正方形并压缩，返回 (文件内容, 文件名)。

    处理失败（例如缺少 Pillow 或图片损坏）时返回 (uploaded_file, None)，
    由调用方回退到原文件，保证上传本身不被阻塞。
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow 是 DRF ImageField 的依赖
        return uploaded_file, None

    try:
        image = Image.open(uploaded_file)
        image.load()
        image = ImageOps.exif_transpose(image)
        image = ImageOps.fit(
            image,
            (AVATAR_TARGET_SIZE, AVATAR_TARGET_SIZE),
            Image.LANCZOS,
        )

        has_alpha = image.mode in ('RGBA', 'LA') or (
            image.mode == 'P' and 'transparency' in image.info
        )
        buffer = io.BytesIO()
        if has_alpha:
            image.convert('RGBA').save(buffer, format='PNG', optimize=True)
            extension = 'png'
        else:
            image.convert('RGB').save(buffer, format='JPEG', quality=85, optimize=True)
            extension = 'jpg'

        filename = f'avatar_{uuid.uuid4().hex}.{extension}'
        return ContentFile(buffer.getvalue(), name=filename), filename
    except Exception:
        return uploaded_file, None


def _delete_stored_file(storage, name):
    if not name:
        return
    try:
        storage.delete(name)
    except Exception:
        # 文件已不存在或存储不可用时不影响主流程
        pass


def save_user_avatar(user, uploaded_file):
    """保存用户头像，并清理旧文件。返回保存后的 avatar 字段值。"""
    old_name = user.avatar.name if user.avatar else ''
    content, filename = _build_avatar_file(uploaded_file)

    if filename:
        user.avatar.save(filename, content, save=False)
    else:
        user.avatar = uploaded_file

    user.save(update_fields=['avatar', 'updated_at'])

    new_name = user.avatar.name if user.avatar else ''
    if old_name and old_name != new_name:
        _delete_stored_file(user.avatar.storage, old_name)

    return user.avatar


def delete_user_avatar(user):
    """删除用户头像文件并清空字段。"""
    if not user.avatar:
        return
    storage = user.avatar.storage
    old_name = user.avatar.name
    user.avatar = None
    user.save(update_fields=['avatar', 'updated_at'])
    _delete_stored_file(storage, old_name)
