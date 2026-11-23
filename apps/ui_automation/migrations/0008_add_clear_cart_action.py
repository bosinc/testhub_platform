from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ui_automation', '0007_storagestate_note'),
    ]

    operations = [
        migrations.AlterField(
            model_name='testcasestep',
            name='action_type',
            field=models.CharField(choices=[('click', '点击'), ('fill', '输入文本'), ('press', '按键'), ('select', '选择选项'), ('check', '勾选'), ('uncheck', '取消勾选'), ('getText', '获取文本'), ('waitFor', '等待元素'), ('hover', '悬停'), ('scroll', '滚动'), ('screenshot', '截图'), ('assert', '断言'), ('wait', '等待'), ('switchTab', '切换标签页'), ('navigate', '跳转URL'), ('setVariable', '设置临时变量'), ('storageState', '登录态'), ('clearCart', '清空购物车')], max_length=20, verbose_name='操作类型'),
        ),
    ]
