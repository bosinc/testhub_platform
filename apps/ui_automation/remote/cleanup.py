"""Docker 中的常驻清理器，回收应用 worker 异常退出后遗留的桌面。"""
import argparse
import os
import time


def run_cleanup(watch=False):
    """执行一次回收，或在 Docker 中每分钟检查一次。"""
    # 独立模块由 main 初始化 Django 后再加载 ORM 相关依赖。
    from django.conf import settings
    from django.db import close_old_connections
    from .sessions import reap_expired_sessions

    # 开关关闭时立即退出，不查询会话表、不启动后台任务。
    if not settings.UI_REMOTE_ENABLED:
        return
    while True:
        try:
            reap_expired_sessions()
        finally:
            close_old_connections()
        if not watch:
            return
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description='回收过期或失联的 UI 远程桌面')
    parser.add_argument('--watch', action='store_true', help='每分钟检查一次')
    options = parser.parse_args()

    # 支持 python -m 直接启动，并保留外部指定的 Django settings。
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
    import django
    django.setup()
    try:
        run_cleanup(watch=options.watch)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
