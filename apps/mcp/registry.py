"""MCP 工具注册表：工具元数据的唯一事实源。

协议 annotations（server.py）、目录 API（views.py）、前端工具目录页
均由本注册表派生，新增/修改工具时只需在此登记一次。
tests/test_registry 会校验 TOOL_REGISTRY 与 tools.ALL_TOOLS 的一致性。
"""
from dataclasses import dataclass

# --------------------------------------------------------------------- #
# 分类常量（前端筛选与汇总共用）
# --------------------------------------------------------------------- #
CATEGORY_READ = 'read'        # 只读工具：即时返回
CATEGORY_PREVIEW = 'preview'  # 危险操作第一步：返回预览 + confirm_token
CATEGORY_CONFIRM = 'confirm'  # 危险操作第二步：校验令牌后执行
CATEGORY_APPROVAL = 'approval'  # 人工审批状态查询

#: 分类显示顺序
CATEGORY_ORDER = [CATEGORY_READ, CATEGORY_PREVIEW, CATEGORY_CONFIRM, CATEGORY_APPROVAL]


@dataclass(frozen=True)
class ToolMeta:
    """单个工具的声明式元数据。"""
    name: str                    # 工具名（与 tools.py 函数名一致）
    title: str                   # 中文显示名
    category: str                # read / preview / confirm / approval
    domain: str                  # 业务域：project / testcases / api-testing 等
    summary: str                 # 一句话用途（列表展示）
    description: str             # 详细说明（详情展示，与 docstring 对齐）
    read_only: bool = True       # MCP annotations: readOnlyHint
    destructive: bool = False    # MCP annotations: destructiveHint
    idempotent: bool = True      # MCP annotations: idempotentHint
    open_world: bool = False     # MCP annotations: openWorldHint
    paired_with: str = ''        # preview <-> confirm 配对工具名
    examples: tuple = ()         # 调用参数示例（dict 元组）

    def to_mcp_annotations(self):
        """转换为 MCP 规范的 ToolAnnotations（供协议 tools/list 返回）。"""
        from mcp.types import ToolAnnotations
        return ToolAnnotations(
            title=self.title,
            readOnlyHint=self.read_only,
            destructiveHint=self.destructive,
            idempotentHint=self.idempotent,
            openWorldHint=self.open_world,
        )

    @property
    def annotations_dict(self):
        """普通 dict 形式（目录 API 序列化用）。"""
        return {
            'title': self.title,
            'readOnlyHint': self.read_only,
            'destructiveHint': self.destructive,
            'idempotentHint': self.idempotent,
            'openWorldHint': self.open_world,
        }


# --------------------------------------------------------------------- #
# 工具登记（与 tools.ALL_TOOLS 一一对应）
# --------------------------------------------------------------------- #

_TOOLS = [
    # ------------------------- 只读：项目与功能用例 ------------------------- #
    ToolMeta(
        name='list_projects', title='项目列表', category=CATEGORY_READ,
        domain='project',
        summary='列出当前用户可访问的测试项目',
        description='列出当前用户拥有或参与的测试项目（测试用例域）。'
                    '返回 id/name/status/created_at/ui_project_id/api_project_id 轻量字段：'
                    'ui_project_id、api_project_id 是该统一项目对应的 UI、API 自动化项目 ID'
                    '（未创建为 null），可直接用于 list_ui_*/list_api_* 与 preview_create_ui_case 等'
                    '需要自动化项目 ID 的工具。支持 limit/offset 分页（上限 100），结果带 60 秒缓存。',
        examples=({'limit': 20, 'offset': 0},),
    ),
    ToolMeta(
        name='list_testcases', title='功能用例列表', category=CATEGORY_READ,
        domain='testcases',
        summary='列出项目/模块下的功能用例及自动化关联状态',
        description='列出指定项目下的功能测试用例，并带回自动化关联状态。'
                    'items 字段：id/title/priority/status/test_type/module_id/created_at/'
                    'automation_links（[{type, object_id, target_name, '
                    'target_exists（目标仍存在且归属同一统一项目）, '
                    'accessible（当前用户可见）}]）。'
                    'module_id 传 0 不限模块，include_children=True 含子模块；'
                    'keyword 模糊匹配标题/描述；linked 过滤关联状态：'
                    'any / none / ui / api（按有效关联）/ '
                    'ui_missing / api_missing / all_missing（无有效关联，含悬空）/ '
                    'ui_stale / api_stale（有记录但目标已失效，需要修复）。'
                    '需对该项目有访问权限，支持 limit/offset 分页（上限 100），结果带 60 秒缓存。',
        examples=({'project_id': 1, 'module_id': 3, 'linked': 'ui_missing', 'limit': 20},),
    ),
    ToolMeta(
        name='list_testcase_modules', title='用例模块目录', category=CATEGORY_READ,
        domain='testcases',
        summary='列出项目下的用例模块目录（含完整路径与用例数）',
        description='列出项目下的用例模块目录，扁平返回便于自行组装树：'
                    'items 为 [{id, name, parent_id, parent_name, path, level, sort_order, '
                    'case_count}]，path 形如「登录/子模块」，case_count 只统计直接挂在该模块的用例；'
                    '另返回 unassigned_case_count（未归模块用例数）。'
                    '拿到的 id 可直接传给 list_testcases 的 module_id。需对该项目有访问权限。',
        examples=({'project_id': 1},),
    ),
    ToolMeta(
        name='get_testcase', title='功能用例详情', category=CATEGORY_READ,
        domain='testcases',
        summary='获取单个功能用例的完整内容与自动化关联',
        description='获取单个功能测试用例的完整内容：标题、描述、备注、前置条件、'
                    '操作步骤（文本 steps 与结构化 step_details）、预期结果、优先级、状态、'
                    '测试类型、标签、模块路径、作者/指派人、关联版本，'
                    '以及 automation_links（含 target_name/target_exists（结构有效性）'
                    '/accessible（权限可见性））与 has_ui_automation / has_api_automation'
                    '（仅统计 target_exists 为真的关联）。'
                    '生成 UI/接口自动化用例前用它读取步骤与预期结果。',
        examples=({'testcase_id': 101},),
    ),

    # ------------------------- 只读：接口测试 ------------------------- #
    ToolMeta(
        name='list_api_requests', title='接口请求列表', category=CATEGORY_READ,
        domain='api-testing',
        summary='列出 API 项目下的接口请求（支持关键词/方法/集合过滤）',
        description='列出指定 API 测试项目下的接口请求（id/name/method/url/request_type/'
                    'collection_id/collection_name），用于组装场景用例时获取 request_id。'
                    'keyword 模糊匹配名称/URL，method 精确匹配（GET/POST/...），'
                    'collection_id 限定集合；需对该 API 项目有访问权限，按集合与请求顺序排序，'
                    '支持 limit/offset 分页（上限 100），结果带 60 秒缓存。',
        examples=({'project_id': 1, 'keyword': 'login', 'method': 'POST', 'limit': 20},),
    ),
    ToolMeta(
        name='get_api_request', title='接口请求详情', category=CATEGORY_READ,
        domain='api-testing',
        summary='获取单个接口请求的完整配置（认证/断言/提取/脚本）',
        description='获取单个接口请求的完整配置，用于判断能否直接复用到场景用例。'
                    'list_api_requests 只返回摘要，无法据此判断断言、变量提取、认证与脚本'
                    '是否可复用；返回 headers/params/body/auth/assertions/extractors/'
                    'pre_request_script/post_request_script、集合路径（collection_path）'
                    '与 project_id，以及 is_write（方法为 POST/PUT/PATCH/DELETE，'
                    '执行会产生副作用）与 referenced_by_scenarios（被多少个场景引用）。'
                    '需对该 API 项目有访问权限。',
        examples=({'request_id': 12},),
    ),
    ToolMeta(
        name='list_api_collections', title='接口集合目录', category=CATEGORY_READ,
        domain='api-testing',
        summary='列出 API 项目下的接口集合（目录）与请求数',
        description='列出 API 项目下的接口集合（接口目录），扁平返回：'
                    'items 为 [{id, name, parent_id, parent_name, path, level, order, '
                    'request_count}]。新建接口请求时用 id 作为 collection_id，'
                    '或直接传集合名给 preview_create_api_request 的 collection_name。'
                    '需对该 API 项目有访问权限，结果带 60 秒缓存。',
        examples=({'project_id': 1},),
    ),
    ToolMeta(
        name='list_api_scenarios', title='场景用例列表', category=CATEGORY_READ,
        domain='api-testing',
        summary='列出 API 项目下的场景用例（支持目录/关键词过滤）',
        description='列出指定 API 测试项目下的场景用例（id/name/description/folder_id/'
                    'folder_name/folder_path/environment_id/request_count/created_at）。'
                    'folder_id 传 0 不限目录，include_children=True 时含子目录下的场景，'
                    'keyword 模糊匹配名称/描述；目录 ID 可从 list_scenario_case_folders 获取。'
                    '场景用例是接口执行的最小单元，执行请用 preview_run_api_scenario；'
                    '需对该 API 项目有访问权限，支持 limit/offset 分页（上限 100），'
                    '结果带 60 秒缓存。',
        examples=({'project_id': 1, 'folder_id': 5, 'keyword': '下单', 'limit': 20},),
    ),
    ToolMeta(
        name='list_scenario_case_folders', title='场景用例目录', category=CATEGORY_READ,
        domain='api-testing',
        summary='列出 API 项目的场景用例目录（含完整路径与场景数）',
        description='列出 API 项目的场景用例目录，扁平返回便于自行组装树：'
                    'items 为 [{id, name, parent_id, parent_name, path, level, case_count}]，'
                    'path 形如「下单/正向流程」，case_count 只统计直接挂在该目录的场景用例；'
                    '另返回 unassigned_case_count（未归目录场景数）。'
                    '拿到的 id 可直接传给 list_api_scenarios 的 folder_id。',
        examples=({'project_id': 1},),
    ),
    ToolMeta(
        name='list_api_suites', title='接口套件列表', category=CATEGORY_READ,
        domain='api-testing',
        summary='列出 API 项目下的测试套件',
        description='列出指定 API 测试项目下的测试套件（id/name/description/environment_id/'
                    'scenario_count/created_at）。套件由多个场景用例按顺序组成，'
                    '执行请用 preview_run_api_suite；需对该 API 项目有访问权限，'
                    '支持 limit/offset 分页（上限 100），结果带 60 秒缓存。',
        examples=({'project_id': 1, 'limit': 20, 'offset': 0},),
    ),
    ToolMeta(
        name='get_api_scenario_execution', title='接口场景执行结果', category=CATEGORY_READ,
        domain='api-testing',
        summary='查询接口场景用例执行结果',
        description='查询接口场景用例执行结果：preview_run_api_scenario 确认执行后返回的执行 ID，'
                    '用本工具轮询。返回状态（PENDING/RUNNING/COMPLETED/FAILED）、请求总数/通过数/'
                    '失败数、逐请求结果（名称/方法/URL/状态码/响应耗时/断言明细）与耗时。'
                    '状态为 PENDING/RUNNING 时请间隔数秒后再次调用。',
        examples=({'execution_id': 42},),
    ),
    ToolMeta(
        name='get_api_suite_execution', title='接口套件执行结果', category=CATEGORY_READ,
        domain='api-testing',
        summary='查询接口测试套件执行结果',
        description='查询接口测试套件执行结果：preview_run_api_suite 确认执行后返回的执行 ID，'
                    '用本工具轮询。返回套件执行状态、场景用例总数/通过数/失败数、每个场景用例的'
                    '结果（含各自 execution_id，可再用 get_api_scenario_execution 查请求级明细）、'
                    '耗时与错误信息。状态为 PENDING/RUNNING 时请间隔数秒后再次调用。',
        examples=({'execution_id': 42},),
    ),

    # ------------------------- 只读：UI 自动化 ------------------------- #
    ToolMeta(
        name='list_ui_cases', title='UI 用例列表', category=CATEGORY_READ,
        domain='ui-automation',
        summary='列出 UI 自动化项目下的测试用例（支持目录/关键词过滤）',
        description='列出指定 UI 自动化项目下的测试用例（id/name/status/priority/'
                    'folder_id/folder_name/folder_path/created_at）。'
                    'folder_id 传 0 不限目录，include_children=True 时含子目录下的用例，'
                    'keyword 模糊匹配名称/描述；目录 ID 可从 list_ui_case_folders 获取。'
                    '需对该 UI 项目有访问权限，支持 limit/offset 分页（上限 100），'
                    '结果带 60 秒缓存。',
        examples=({'project_id': 1, 'folder_id': 7, 'keyword': '登录', 'limit': 20},),
    ),
    ToolMeta(
        name='list_ui_case_folders', title='UI 用例目录', category=CATEGORY_READ,
        domain='ui-automation',
        summary='列出 UI 项目的用例目录（含完整路径与用例数）',
        description='列出 UI 项目的用例目录，扁平返回便于自行组装树：'
                    'items 为 [{id, name, parent_id, parent_name, path, level, case_count}]，'
                    'path 形如「登录/短信登录」，case_count 只统计直接挂在该目录的用例；'
                    '另返回 unassigned_case_count（未归目录用例数）。'
                    '拿到的 id 可直接传给 list_ui_cases 的 folder_id。',
        examples=({'project_id': 1},),
    ),
    ToolMeta(
        name='list_ui_suites', title='UI 套件列表', category=CATEGORY_READ,
        domain='ui-automation',
        summary='列出 UI 自动化项目下的测试套件',
        description='列出指定 UI 自动化项目下的测试套件（id/name/description/execution_status/'
                    'passed_count/failed_count/case_count/created_at）。执行套件请用 '
                    'preview_run_ui_suite；需对该 UI 项目有访问权限，支持 limit/offset 分页'
                    '（上限 100），结果带 60 秒缓存。',
        examples=({'project_id': 1, 'limit': 20, 'offset': 0},),
    ),
    ToolMeta(
        name='list_ui_elements', title='元素库', category=CATEGORY_READ,
        domain='ui-automation',
        summary='获取元素库数据（页面模块 + 元素列表）',
        description='获取 UI 元素库：返回 page_modules（页面模块/元素分组索引，含每个模块的元素数量）'
                    '与 items（元素列表：id/name/element_type/page/group_id/locator_strategy/'
                    'locator_value/locator_options/backup_locators/validation_status/usage_count）。'
                    '支持按 page（页面名）、group_id（分组）、keyword（名称/描述/页面模糊匹配）过滤；'
                    '分页作用于元素列表，page_modules 始终返回全量索引。'
                    '组装 UI 用例步骤时用 items[].id 作为 element_id。',
        examples=({'project_id': 1, 'page': '登录页', 'limit': 50, 'offset': 0},),
    ),
    ToolMeta(
        name='get_ui_execution', title='UI 执行结果查询', category=CATEGORY_READ,
        domain='ui-automation',
        summary='查询 UI 自动化用例执行结果',
        description='查询 UI 自动化用例执行结果：confirm_run_ui_case 返回执行 ID 后用本工具轮询。'
                    '返回状态（pending/running/passed/failed/error）、错误信息、逐步骤结果、'
                    '耗时与截图元数据（不含 base64 图片数据）。'
                    '状态为 running/pending 时请间隔数秒后再次调用。',
        examples=({'execution_id': 42},),
    ),
    ToolMeta(
        name='get_ui_suite_execution', title='UI 套件执行结果', category=CATEGORY_READ,
        domain='ui-automation',
        summary='查询 UI 自动化套件执行结果',
        description='查询 UI 自动化套件执行结果：preview_run_ui_suite 确认执行后返回的执行 ID，'
                    '用本工具轮询。返回套件状态（PENDING/RUNNING/SUCCESS/FAILED/ABORTED）、'
                    '用例总数/通过数/失败数/跳过数、通过率、耗时、错误信息，以及逐用例结果'
                    '（cases：test_case_id/test_case_name/status/steps/error/时间/截图）'
                    '与逐用例执行记录（case_executions：id/test_case_id/test_case__name/'
                    'status/execution_time/error_message）。'
                    '状态为 PENDING/RUNNING 时请间隔数秒后再次调用。',
        examples=({'execution_id': 42},),
    ),

    # ------------------------- 只读：压测 ------------------------- #
    ToolMeta(
        name='list_perf_scenes', title='压测场景列表', category=CATEGORY_READ,
        domain='perf-testing',
        summary='列出压测项目下的压测场景',
        description='列出指定压测项目下的压测场景（id/name/engine/enabled/created_at）。'
                    '需对该压测项目有访问权限，支持 limit/offset 分页（上限 100），'
                    '结果带 60 秒缓存。',
        examples=({'project_id': 1, 'limit': 20, 'offset': 0},),
    ),
    ToolMeta(
        name='get_report', title='压测报告摘要', category=CATEGORY_READ,
        domain='perf-testing',
        summary='获取压测执行报告摘要',
        description='获取指定压测执行的报告摘要：状态、SLA 结果、结论（verdict）及明细、'
                    '汇总指标、时长与按请求维度的统计（总量/失败/错误率/平均响应/P95/TPS）。'
                    '直接复用执行落库指标，不重新计算。',
        examples=({'execution_id': 42},),
    ),
    ToolMeta(
        name='analyze_failure', title='压测失败 AI 分析', category=CATEGORY_READ,
        domain='perf-testing',
        summary='触发或获取压测失败的 AI 分析',
        description='压测失败 AI 分析：命中缓存时即时返回分析结果（status=completed）；'
                    '未命中时投递 Django-Q2 异步分析任务并返回 status=accepted，'
                    '约 30 秒后再次调用本工具获取结果。队列不可用时报错并提示改用页面 SSE 入口。',
        read_only=False, idempotent=False, open_world=True,
        examples=({'execution_id': 42},),
    ),

    # ------------------- 执行类：preview / confirm 两段（5 组） ------------------- #
    ToolMeta(
        name='preview_run_api_suite', title='预览执行 API 套件', category=CATEGORY_PREVIEW,
        domain='api-testing',
        summary='预览执行接口测试套件（危险操作第一步）',
        description='危险操作第一步：生成执行接口测试套件的影响预览并返回 confirm_token（5 分钟有效）。'
                    '套件由多个场景用例按顺序组成，执行会真实发起 HTTP 调用；'
                    '可选 environment_id 覆盖套件自带执行环境。'
                    '本步骤不执行任何用例，确认执行请调用 confirm_run_api_suite；'
                    '执行后可用 get_api_suite_execution 轮询结果。',
        read_only=False, idempotent=False, paired_with='confirm_run_api_suite',
        examples=({'suite_id': 12, 'environment_id': 3},),
    ),
    ToolMeta(
        name='confirm_run_api_suite', title='确认执行 API 套件', category=CATEGORY_CONFIRM,
        domain='api-testing',
        summary='确认执行接口测试套件（危险操作第二步）',
        description='危险操作第二步：校验 preview_run_api_suite 返回的 confirm_token 后异步执行套件，'
                    '返回套件执行 ID。令牌一次性消费且 5 分钟内有效；'
                    '人工审批模式下返回 awaiting_approval，需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_run_api_suite',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_run_api_scenario', title='预览执行接口场景', category=CATEGORY_PREVIEW,
        domain='api-testing',
        summary='预览执行接口场景用例（危险操作第一步）',
        description='危险操作第一步：生成执行接口场景用例的影响预览并返回 confirm_token（5 分钟有效）。'
                    '可选 environment_id 覆盖场景用例自带环境；预览会显示启用的请求数、'
                    '其中写请求（POST/PUT/PATCH/DELETE）数量、执行环境名或「未绑定执行环境'
                    '（使用请求内地址）」，并提示会真实发起 HTTP 调用。'
                    '本步骤不发起任何 HTTP 请求，确认执行请调用 confirm_run_api_scenario。',
        read_only=False, idempotent=False, paired_with='confirm_run_api_scenario',
        examples=({'scenario_case_id': 8, 'environment_id': 3},),
    ),
    ToolMeta(
        name='confirm_run_api_scenario', title='确认执行接口场景', category=CATEGORY_CONFIRM,
        domain='api-testing',
        summary='确认执行接口场景用例（危险操作第二步）',
        description='危险操作第二步：校验令牌后异步执行场景用例，返回执行 ID；'
                    '请用 get_api_scenario_execution 轮询请求级结果。'
                    '令牌一次性消费且 5 分钟内有效；人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_run_api_scenario',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_run_ui_case', title='预览执行 UI 用例', category=CATEGORY_PREVIEW,
        domain='ui-automation',
        summary='预览执行 UI 自动化用例（危险操作第一步）',
        description='危险操作第一步：生成执行 UI 自动化用例的影响预览并返回 confirm_token（5 分钟有效）。'
                    '可选执行引擎（playwright/selenium）、浏览器与 headless 模式。'
                    '本步骤不执行任何用例，确认执行请调用 confirm_run_ui_case。',
        read_only=False, idempotent=False, paired_with='confirm_run_ui_case',
        examples=({'case_id': 8, 'engine': 'playwright', 'browser': 'chrome', 'headless': True},),
    ),
    ToolMeta(
        name='confirm_run_ui_case', title='确认执行 UI 用例', category=CATEGORY_CONFIRM,
        domain='ui-automation',
        summary='确认执行 UI 自动化用例（危险操作第二步）',
        description='危险操作第二步：校验 preview_run_ui_case 返回的 confirm_token 后异步执行用例，'
                    '返回执行 ID。令牌一次性消费且 5 分钟内有效；'
                    '人工审批模式下返回 awaiting_approval，需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_run_ui_case',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_run_ui_suite', title='预览执行 UI 套件', category=CATEGORY_PREVIEW,
        domain='ui-automation',
        summary='预览执行 UI 自动化套件（危险操作第一步）',
        description='危险操作第一步：生成执行 UI 自动化套件的影响预览并返回 confirm_token（5 分钟有效）。'
                    '可选引擎（playwright/selenium）、浏览器、headless 与登录态 storage_state_id。'
                    '本步骤不启动浏览器，确认执行请调用 confirm_run_ui_suite；'
                    '执行后可用 get_ui_suite_execution 轮询结果。',
        read_only=False, idempotent=False, paired_with='confirm_run_ui_suite',
        examples=({'suite_id': 5, 'engine': 'playwright', 'browser': 'chrome', 'headless': True},),
    ),
    ToolMeta(
        name='confirm_run_ui_suite', title='确认执行 UI 套件', category=CATEGORY_CONFIRM,
        domain='ui-automation',
        summary='确认执行 UI 自动化套件（危险操作第二步）',
        description='危险操作第二步：校验令牌后异步执行套件，返回套件执行 ID；'
                    '请用 get_ui_suite_execution 轮询结果。令牌一次性消费且 5 分钟内有效；'
                    '人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_run_ui_suite',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_run_perf_scene', title='预览发起压测', category=CATEGORY_PREVIEW,
        domain='perf-testing',
        summary='预览发起压测（危险操作第一步）',
        description='危险操作第一步：生成发起压测的影响预览并返回 confirm_token（5 分钟有效）。'
                    '本步骤不产生任何压测流量，确认执行请调用 confirm_run_perf_scene。',
        read_only=False, idempotent=False, paired_with='confirm_run_perf_scene',
        examples=({'scene_id': 3},),
    ),
    ToolMeta(
        name='confirm_run_perf_scene', title='确认发起压测', category=CATEGORY_CONFIRM,
        domain='perf-testing',
        summary='确认发起压测（危险操作第二步）',
        description='危险操作第二步：校验 preview_run_perf_scene 返回的 confirm_token 后异步发起压测，'
                    '返回执行 ID。令牌一次性消费且 5 分钟内有效；'
                    '人工审批模式下返回 awaiting_approval，需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_run_perf_scene',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),

    # ------------------- 创建类：preview / confirm 两段（4 组） ------------------- #
    ToolMeta(
        name='preview_create_testcase', title='预览创建用例', category=CATEGORY_PREVIEW,
        domain='testcases',
        summary='预览创建功能测试用例（危险操作第一步）',
        description='危险操作第一步：生成创建功能测试用例的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data 结构：{title, description?, notes?, preconditions?, expected_result?, '
                    'steps?（文本或 [{action, expected}]), '
                    'priority?（low/medium/high/critical，或中文 低/中/高/紧急）, '
                    'status?, test_type?, tags?, module_id? 或 module_path?'
                    '（「父模块/子模块」路径，只知模块名时用它；路径天然消除同名歧义）, '
                    'create_missing?（末级模块不存在时是否自动创建，默认 false）, '
                    'assignee_id?, version_ids?, step_details?}。'
                    '本步骤不落库，确认创建请调用 confirm_create_testcase。',
        read_only=False, idempotent=False, paired_with='confirm_create_testcase',
        examples=({'project_id': 1,
                   'data': {'title': '登录成功', 'expected_result': '跳转到首页', 'priority': 'high',
                            'module_id': 3, 'tags': ['登录'],
                            'steps': [{'action': '输入账号密码并点击登录', 'expected': '登录成功'}]}},),
    ),
    ToolMeta(
        name='confirm_create_testcase', title='确认创建用例', category=CATEGORY_CONFIRM,
        domain='testcases',
        summary='确认创建功能测试用例（危险操作第二步）',
        description='危险操作第二步：校验 preview_create_testcase 返回的 confirm_token 后创建用例，'
                    '返回 testcase_id。令牌一次性消费且 5 分钟内有效；'
                    '人工审批模式下返回 awaiting_approval，需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_create_testcase',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_create_ui_case', title='预览新建关联 UI 用例', category=CATEGORY_PREVIEW,
        domain='ui-automation',
        summary='预览新建 UI 自动化用例并关联功能用例（危险操作第一步）',
        description='危险操作第一步：生成新建 UI 自动化用例的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data：{name, description?, folder_id? 或 folder_path?'
                    '（「父目录/子目录」路径，只知目录名时用它）, '
                    'create_missing?（末级目录不存在时是否自动创建，默认 false）, '
                    'priority?（high/medium/low，或中文 高/中/低）, '
                    'status?, steps?: '
                    '[{action_type, element_id?, input_value?, action_params?, wait_time?, '
                    'assert_type?, assert_value?, description?}]}；'
                    'test_case_id 传入功能测试用例 ID 时创建后自动建立 UI 自动化关联；'
                    '该功能用例已有同类型关联时确认会被拒绝，确需替换须显式传 replace_link=true。'
                    '步骤元素必须属于该 UI 项目（可从 list_ui_elements 获取）；'
                    '元素定位器未验证时确认结果会带 warnings 提示可能执行失败。本步骤不落库。',
        read_only=False, idempotent=False, paired_with='confirm_create_ui_case',
        examples=({'project_id': 3, 'test_case_id': 101,
                   'data': {'name': '登录-正常登录', 'priority': 'high', 'status': 'ready',
                            'steps': [{'action_type': 'fill', 'element_id': 12,
                                       'input_value': 'admin'},
                                      {'action_type': 'click', 'element_id': 13}]}},),
    ),
    ToolMeta(
        name='confirm_create_ui_case', title='确认新建关联 UI 用例', category=CATEGORY_CONFIRM,
        domain='ui-automation',
        summary='确认新建 UI 自动化用例并关联功能用例（危险操作第二步）',
        description='危险操作第二步：校验令牌后创建 UI 自动化用例（含步骤）并按需建立与功能用例的关联，'
                    '返回 case_id 与 automation_link；步骤元素定位器未验证时附 warnings。'
                    '功能用例已有同类型关联且未传 replace_link=true 时整体拒绝（不留孤儿用例）。'
                    '令牌一次性消费且 5 分钟内有效；'
                    '人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_create_ui_case',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_create_ui_element', title='预览新建元素', category=CATEGORY_PREVIEW,
        domain='ui-automation',
        summary='预览新建 UI 元素（危险操作第一步）',
        description='危险操作第一步：生成新建 UI 元素的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data：{name, element_type?（INPUT/BUTTON/LINK/DROPDOWN/CHECKBOX/RADIO/TEXT/'
                    'IMAGE/CONTAINER/TABLE/FORM/MODAL，默认 BUTTON）, '
                    'page?, group_id? 或 group_path?（「父分组/子分组」路径，只知分组名时用它，'
                    '优先于 page 的前缀启发式）, create_missing?（末级分组不存在时是否自动创建，默认 false）, '
                    'locator_strategy（策略 ID 或名称，如 CSS/XPath/text）, locator_value, '
                    'locator_options?, backup_locators?: [{strategy, value}], description?, '
                    'component_name?, wait_timeout?, order?}。'
                    'page 未配合 group_id/group_path 时自动挂到同名页面分组；'
                    '同项目内「定位策略 + 定位值 + 定位选项」完全相同的元素直接复用（reused=true）。'
                    '组装 UI 用例步骤前先用 list_ui_elements 查已有元素，缺失时再调用本工具。',
        read_only=False, idempotent=False, paired_with='confirm_create_ui_element',
        examples=({'project_id': 3,
                   'data': {'name': '登录按钮', 'element_type': 'BUTTON', 'page': '登录页',
                            'locator_strategy': 'CSS', 'locator_value': '#login-btn'}},),
    ),
    ToolMeta(
        name='confirm_create_ui_element', title='确认新建元素', category=CATEGORY_CONFIRM,
        domain='ui-automation',
        summary='确认新建 UI 元素（危险操作第二步）',
        description='危险操作第二步：校验令牌后创建元素，返回 element_id、reused 标记'
                    '（reused=true 表示命中已有元素、未新建）、validation_status 与 warning。'
                    '平台暂无真实 DOM 校验能力（validate_locator 为模拟实现），'
                    'validation_status 非 VALID 时 warning 会提示「定位器未经验证」，'
                    '请确认 selector 来自录制脚本、元素库或人工确认，否则用例可能执行失败。'
                    '令牌一次性消费且 5 分钟内有效；人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_create_ui_element',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_create_api_scenario_case', title='预览新建关联接口场景',
        category=CATEGORY_PREVIEW,
        domain='api-testing',
        summary='预览新建接口场景用例并关联功能用例（危险操作第一步）',
        description='危险操作第一步：生成新建接口场景用例的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data：{name, description?, folder_id? 或 folder_path?'
                    '（「父目录/子目录」路径，只知目录名时用它）, '
                    'create_missing?（末级目录不存在时是否自动创建，默认 false）, '
                    'environment_id?, requests?: '
                    '[{request_id, order?, enabled?, assertions?, extractors?}]}；'
                    'requests 至少需要 1 个启用请求（enabled 默认 true），否则确认会被拒绝；'
                    '未提供 assertions/extractors 时沿用接口自身配置；'
                    'test_case_id 传入功能测试用例 ID 时创建后自动建立接口场景关联；'
                    '该功能用例已有同类型关联时确认会被拒绝，确需替换须显式传 replace_link=true。'
                    '本步骤不落库。',
        read_only=False, idempotent=False, paired_with='confirm_create_api_scenario_case',
        examples=({'project_id': 2, 'test_case_id': 101,
                   'data': {'name': '下单主流程', 'environment_id': 4,
                            'requests': [{'request_id': 21}, {'request_id': 22}]}},),
    ),
    ToolMeta(
        name='confirm_create_api_scenario_case', title='确认新建关联接口场景',
        category=CATEGORY_CONFIRM,
        domain='api-testing',
        summary='确认新建接口场景用例并关联功能用例（危险操作第二步）',
        description='危险操作第二步：校验令牌后创建接口场景用例（含请求编排）并按需建立与功能用例的关联，'
                    '返回 scenario_case_id 与 automation_link；无启用请求时整体拒绝。'
                    '功能用例已有同类型关联且未传 replace_link=true 时整体拒绝（不留孤儿场景）。'
                    '令牌一次性消费且 5 分钟内有效；'
                    '人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_create_api_scenario_case',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_create_api_request', title='预览新建接口请求', category=CATEGORY_PREVIEW,
        domain='api-testing',
        summary='预览新建接口请求（危险操作第一步）',
        description='危险操作第一步：生成新建接口请求的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data：{name, method?（默认 GET）, url, '
                    'collection_id? 或 collection_path?（「父集合/子集合」路径）或 collection_name?'
                    '（顶层名称，不存在时自动新建该集合）, '
                    'create_missing?（collection_path 末级不存在时是否自动创建，默认 false）, '
                    'description?, request_type?, headers?, '
                    'params?, body?, auth?, assertions?, extractors?, pre_request_script?, '
                    'post_request_script?, order?}。集合是请求的归属目录，场景用例与 '
                    'list_api_requests 都按集合所属项目过滤，所以必须落到集合下。'
                    '组装场景用例前先用 list_api_requests 查已有接口，缺失时再调用本工具。',
        read_only=False, idempotent=False, paired_with='confirm_create_api_request',
        examples=({'project_id': 2,
                   'data': {'name': '登录接口', 'method': 'POST',
                            'url': '{{base_url}}/api/login',
                            'collection_name': '用户模块',
                            'body': {'username': 'admin', 'password': '123456'}}},),
    ),
    ToolMeta(
        name='confirm_create_api_request', title='确认新建接口请求', category=CATEGORY_CONFIRM,
        domain='api-testing',
        summary='确认新建接口请求（危险操作第二步）',
        description='危险操作第二步：校验令牌后创建接口请求，返回 request_id、所属集合'
                    '（collection_created=true 表示顺带新建了集合）与 similar_requests'
                    '（同 method + url 的既有请求，用于判断是否重复）。'
                    '令牌一次性消费且 5 分钟内有效；人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_create_api_request',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_import_recorded_script', title='预览导入录制脚本', category=CATEGORY_PREVIEW,
        domain='ui-automation',
        summary='预览导入 Python Playwright 录制脚本',
        description='校验项目权限、脚本大小、Python 语法及脚本安全规则，返回导入摘要与 confirm_token（5 分钟有效）。'
                    'content 为 UTF-8 Python Playwright 脚本文本，最大 2MB；本步骤不创建脚本。'
                    '确认导入请调用 confirm_import_recorded_script；人工审批模式下还需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='confirm_import_recorded_script',
        examples=({'project_id': 3,
                   'content': 'from playwright.sync_api import sync_playwright\n'},),
    ),
    ToolMeta(
        name='confirm_import_recorded_script', title='确认导入录制脚本', category=CATEGORY_CONFIRM,
        domain='ui-automation',
        summary='确认创建 UI 自动化录制脚本',
        description='校验 preview_import_recorded_script 返回的 confirm_token 后创建 Python Playwright 录制脚本，'
                    '返回脚本 ID 与内容摘要。令牌一次性消费且 5 分钟内有效；'
                    '人工审批模式下返回 awaiting_approval，需轮询 get_approval_status。',
        read_only=False, idempotent=False, paired_with='preview_import_recorded_script',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),

    # ------------------- 更新类：preview / confirm 两段（4 组） ------------------- #
    ToolMeta(
        name='preview_update_testcase', title='预览更新功能用例', category=CATEGORY_PREVIEW,
        domain='testcases',
        summary='预览更新功能测试用例（危险操作第一步）',
        description='危险操作第一步：生成更新功能测试用例的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data 为需要更新的字段集合（未提供的字段保持不变）：{title?, description?, '
                    'notes?, preconditions?, steps?, expected_result?, '
                    'priority?（low/medium/high/critical，或中文 低/中/高/紧急）, status?, '
                    'test_type?, tags?, module_id? 或 module_path?（「父模块/子模块」）, '
                    'create_missing?, assignee_id?, version_ids?, step_details?}。'
                    'step_details 会整体覆盖原结构化步骤。本步骤不修改数据。',
        read_only=False, idempotent=False, paired_with='confirm_update_testcase',
        examples=({'testcase_id': 101,
                   'data': {'priority': 'high', 'status': 'active', 'tags': ['回归']}},),
    ),
    ToolMeta(
        name='confirm_update_testcase', title='确认更新功能用例', category=CATEGORY_CONFIRM,
        domain='testcases',
        summary='确认更新功能测试用例（危险操作第二步）',
        description='危险操作第二步：校验令牌后更新功能测试用例，返回实际变更字段与覆盖的步骤数。'
                    '令牌一次性消费且 5 分钟内有效；人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, destructive=True,
        paired_with='preview_update_testcase',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_update_ui_case', title='预览更新 UI 用例', category=CATEGORY_PREVIEW,
        domain='ui-automation',
        summary='预览更新 UI 自动化用例（危险操作第一步）',
        description='危险操作第一步：生成更新 UI 自动化用例的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data：{name?, description?, folder_id? 或 folder_path?'
                    '（「父目录/子目录」）, create_missing?, '
                    'priority?（high/medium/low，或中文 高/中/低）, status?, steps?}；'
                    'steps 传入时会整体覆盖原步骤（元素必须属于用例所在项目）。本步骤不修改数据。',
        read_only=False, idempotent=False, paired_with='confirm_update_ui_case',
        examples=({'case_id': 8, 'data': {'priority': 'high', 'status': 'ready'}},),
    ),
    ToolMeta(
        name='confirm_update_ui_case', title='确认更新 UI 用例', category=CATEGORY_CONFIRM,
        domain='ui-automation',
        summary='确认更新 UI 自动化用例（危险操作第二步）',
        description='危险操作第二步：校验令牌后更新 UI 自动化用例，返回变更字段与覆盖的步骤数。'
                    '令牌一次性消费且 5 分钟内有效；人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, destructive=True,
        paired_with='preview_update_ui_case',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_update_api_scenario_case', title='预览更新接口场景',
        category=CATEGORY_PREVIEW,
        domain='api-testing',
        summary='预览更新接口场景用例（危险操作第一步）',
        description='危险操作第一步：生成更新接口场景用例的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data：{name?, description?, folder_id? 或 folder_path?'
                    '（「父目录/子目录」）, create_missing?, environment_id?, requests?}；'
                    'requests 传入时会整体覆盖原请求编排。本步骤不修改数据。',
        read_only=False, idempotent=False, paired_with='confirm_update_api_scenario_case',
        examples=({'scenario_case_id': 8,
                   'data': {'name': '下单主流程-v2', 'requests': [{'request_id': 21}]}},),
    ),
    ToolMeta(
        name='confirm_update_api_scenario_case', title='确认更新接口场景',
        category=CATEGORY_CONFIRM,
        domain='api-testing',
        summary='确认更新接口场景用例（危险操作第二步）',
        description='危险操作第二步：校验令牌后更新接口场景用例，返回变更字段、覆盖的请求数'
                    '与 runnable（是否还有启用的请求）；runnable=false 时附带 warning，'
                    '该场景无法执行。'
                    '令牌一次性消费且 5 分钟内有效；人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, destructive=True,
        paired_with='preview_update_api_scenario_case',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
    ToolMeta(
        name='preview_update_ui_element', title='预览更新元素', category=CATEGORY_PREVIEW,
        domain='ui-automation',
        summary='预览更新 UI 元素（危险操作第一步）',
        description='危险操作第一步：生成更新 UI 元素的影响预览并返回 confirm_token（5 分钟有效）。'
                    'data：{name?, description?, page?, component_name?, element_type?, '
                    'locator_strategy?（策略 ID 或名称，如 CSS/XPath/text）, locator_value?, '
                    'locator_options?, backup_locators?, group_id?, parent_element_id?, '
                    'wait_timeout?, order?, is_visible?, is_enabled?, force_action?}。本步骤不修改数据。',
        read_only=False, idempotent=False, paired_with='confirm_update_ui_element',
        examples=({'element_id': 12,
                   'data': {'locator_strategy': 'CSS', 'locator_value': '#login-btn',
                            'wait_timeout': 10}},),
    ),
    ToolMeta(
        name='confirm_update_ui_element', title='确认更新元素', category=CATEGORY_CONFIRM,
        domain='ui-automation',
        summary='确认更新 UI 元素（危险操作第二步）',
        description='危险操作第二步：校验令牌后更新元素，返回变更字段与最新定位信息。'
                    '令牌一次性消费且 5 分钟内有效；人工审批模式下需轮询 get_approval_status。',
        read_only=False, idempotent=False, destructive=True,
        paired_with='preview_update_ui_element',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),

    # ------------------------- 审批查询（1） ------------------------- #
    ToolMeta(
        name='get_approval_status', title='审批状态查询', category=CATEGORY_APPROVAL,
        domain='platform',
        summary='查询危险操作的人工审批状态',
        description='只读查询危险操作的人工审批状态，供 Agent 轮询：confirm_* 返回 awaiting_approval 后，'
                    '用同一 confirm_token 反复调用本工具，直至返回 approved（附执行结果）/ '
                    'rejected / expired。',
        examples=({'confirm_token': 'cf_xxxxxxxx'},),
    ),
]

#: 工具注册表：name -> ToolMeta
TOOL_REGISTRY = {meta.name: meta for meta in _TOOLS}


def get_tool_meta(name: str):
    """按工具名获取元数据，未登记返回 None。"""
    return TOOL_REGISTRY.get(name)


def catalog_summary() -> dict:
    """目录汇总：总数 + 分类计数。"""
    by_category = {c: 0 for c in CATEGORY_ORDER}
    for meta in _TOOLS:
        by_category[meta.category] = by_category.get(meta.category, 0) + 1
    return {
        'total': len(_TOOLS),
        'by_category': by_category,
    }
