from django.apps import AppConfig


class PerfTestingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.perf_testing'
    verbose_name = '性能测试'

    def ready(self):
        """应用启动钩子（此处刻意不查库）。

        Django 不建议在 ready() 里执行查询，否则会触发
        "Accessing the database during app initialization is discouraged"
        警告；迁移未完成或数据库未就绪时还会连累整个进程起不来。

        启动自愈（回收上次进程退出时遗留的僵尸执行）改在
        backend/asgi.py 的 get_asgi_application() 之后执行 —— 那里 app
        registry 已就绪，且服务每次启动都会跑到，不依赖 migrate。

        需要手工触发时可用 cleanup_perf_data（会先回收僵尸执行）：
            python manage.py cleanup_perf_data --dry-run
        """
