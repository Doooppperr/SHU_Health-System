# 康康健健 HealthDoc 后端

Flask 后端负责认证与三角色授权、账号邮件验证、机构主体/分院协作、领域化体检数据、多人预约、评价回复、空位提醒、私有附件、OCR 和健康 AI。本地使用 SQLite schema v9；服务器通过 `DATABASE_URL` 连接 GaussDB/openGauss，并使用 Alembic 增量迁移。

## 1.0—3.0 后端演进

- 1.0 建立 Flask API、JWT 三角色授权、机构/套餐、基础预约和健康记录。
- 2.0 增加自主测量、报告草稿/锁定/归档、时间线、趋势、亲友授权和 AI/OCR 权限链。
- 3.0 引入健康领域、套餐版本、预约组、容量候补、通知 outbox、图文报告；schema v8 进一步增加机构主体、分院和跨院访问审计。
- 当前只维护 schema v9 的统一模型和接口；旧地址仅在有明确兼容价值时保留，旧数据库通过迁移脚本升级。

## 环境与安装

要求 Python 3.10+。在 `backend` 目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
```

开发后端默认监听 <http://127.0.0.1:5050>，健康检查为 `GET /api/health`。

本地通知默认 `NOTIFICATION_EMAIL_DRY_RUN=1`，只验证 Outbox/重试流程而不连接外部邮箱。需要联调真实 SMTP 时，在被 Git 忽略的 `.env` 中配置 `SMTP_*` 并设为 `0`；`NOTIFICATION_EMAIL_REDIRECT` 可将所有通知统一投递到一个测试收件箱。注册必须填写有效邮箱，但邮箱只作为通知渠道，不作为账号唯一标识，因此家庭成员和演示账号可以共用一个邮箱；空位提醒直接使用当前账号绑定的通知邮箱。新建演示库时可用 `DEMO_SHARED_EMAIL` 统一绑定测试账号，真实地址不要写入受版本控制的文件。

根目录的 `scripts/start-full-dev.ps1` 和 `scripts/start-full-prod.ps1` 会在后端就绪后自动启动隐藏的常驻 worker，每 5 秒处理一次 Outbox，并在前端命令退出时停止。单独运行可使用 `scripts/start-notification-worker.ps1`，或在后端目录执行 `python scripts/notification_worker.py --watch --interval-seconds 5`。条件更新保证误开两个 worker 时同一条通知只会被一个进程领取；发送前会把 Outbox 载荷转换为连续的中文业务文本，不会把 JSON 原文发给用户。

## 数据库与 schema v9

默认数据库为 `instance/health_system.db`。SQLite 连接启用外键；`PRAGMA user_version=8` 标识当前结构。新空库会直接创建 v8，v4–v7 使用升级脚本全量保留迁移；生产 openGauss/GaussDB 使用 `migrations/versions/20260720_schema_v8.py`，其下修订为 schema v7。

```powershell
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
```

演示业务数据可通过专用脚本重建；`--check-only` 只校验目标库和账号边界，`--apply --yes` 才会覆盖演示业务记录并保留全部演示账号及密码哈希：

```powershell
.\.venv\Scripts\python.exe .\scripts\reset_v8_demo_data.py --check-only
.\.venv\Scripts\python.exe .\scripts\reset_v8_demo_data.py --apply --yes
```

升级与重建都会先校验完整性并生成时间戳备份。当前核心表包括：

- 平台与授权：`users`、`friend_relations`、`organizations`、`institutions`、`packages`、`institution_invites`、`institution_images`、`comments`；
- 指标字典：`indicator_categories`、`indicator_dicts`；
- 健康模型：`health_domains`、`indicator_domain_links`、`self_measurements`、`institution_reports`、`report_indicators`、`report_text_results`、`report_assets`、`report_access_logs`；
- 套餐与预约：`package_versions`、`package_version_domains`、`booking_groups`、`appointments`、`appointment_events`、`appointment_capacity_slots`、`waitlist_subscriptions`、`waitlist_subscription_participants`；
- 通知可靠性：`availability_notification_events`、`notification_outbox`。

## 角色与账号规则

- `user`：必须有唯一 `health_id`，不能绑定机构。
- `institution_admin`：必须绑定一个具体分院，不拥有健康身份码；账号无总部权限。本院报告可生产和归档，同机构兄弟分院已归档报告仅可查看并写入审计日志。
- `admin`：不能绑定机构，也不拥有健康身份码。
- `is_active=false` 后，登录、刷新令牌及所有角色保护接口立即拒绝账号。
- 普通用户注册时健康身份码由服务端生成，前端不能指定或修改。
- 机构工作人员注册必须消费当前有效邀请码；邀请码每机构只有一行，重新生成覆盖当前值并使旧明文失效，数据库仅保存哈希。
- 系统管理员可停用/恢复账号。删除普通用户需显式 `confirm=true` 并级联其健康数据；删除机构账号保留报告及创建者用户名快照，只把创建者外键置空。

## 当前 API 分区

| 前缀 | 主要角色 | 内容 |
|---|---|---|
| `/api/auth` | 公开/登录用户 | 图片验证码、注册、登录、刷新、注销 |
| `/api/users/me` | 登录用户 | 当前账号与实时角色 |
| `/api/profile/me` | 普通用户 | 本人健康身份和个人健康资料 |
| `/api/self-measurements` | 普通用户 | 总览测量抽屉使用的六类日常测量 CRUD |
| `/api/health/dashboard` | 普通用户 | 今日测量、下一次体检、最新健康数据和最近时间线 |
| `/api/health-data` | 普通用户 | 仅机构已归档体检报告列表、详情和私有附件；个人测量不进入此列表 |
| `/api/health/timeline` | 普通用户 | `all/exam/self` 三种记录类型的统一时间线 |
| `/api/health-trends/{domain_id}` | 普通用户 | 按健康领域和来源分轨的长期趋势 |
| `/api/ai/trends/stream` | 普通用户 | 本次页面授权后的当前趋势流式 AI 解读 |
| `/api/organizations` | 登录用户 | 机构主体及其分院的公开分组读模型 |
| `/api/org/context` | 机构账号 | 当前机构主体、当前分院、兄弟分院和协作权限 |
| `/api/friends` | 普通用户 | 亲友关系与授权状态 |
| `/api/institutions` | 登录用户 | 启用机构、详情和套餐浏览 |
| `/api/appointments`、`/api/booking-groups` | 普通用户 | 未来 30 天余量、1–5 人预约组与整组取消 |
| `/api/waitlist-subscriptions` | 普通用户 | 候补提醒创建、查看和取消 |
| `/api/comments` | 用户/管理员 | 公开评论、我的评论和审核 |
| `/api/indicators/dicts` | 登录用户 | 指标字典 |
| `/api/org` | 机构账号 | 本机构资料、套餐审核申请、预约履约、相册和报告生产 |
| `/api/admin` | 系统管理员 | 平台统计、机构、套餐变更审批、相册和邀请码 |
| `/api/users` | 系统管理员 | 账号列表、停用、恢复和删除 |
| `/api/ai` | 访客/普通用户 | FAQ、流式对话、报告列表和分析 |

所有受限接口都在服务端逐请求查询账号、角色、启用状态和机构绑定。前端隐藏菜单不是安全边界。

## 机构、套餐、相册和评论

- 机构采用 `is_active` 软停用；套餐新增、修改、下架和恢复都由所属机构提交审核申请，管理员只能通过或驳回，不能直接改套餐。
- 通过申请时套餐与审核状态在同一事务更新；驳回不影响当前套餐，待审申请可撤回后重提，完整前后值与操作人永久留痕。
- 每机构最多 8 张 JPEG、PNG 或 WebP 图片，单张最大 5 MB；服务端真实解码、修正方向、重编码并清除 EXIF。
- 相册排序一次提交完整 ID 集合并归一化；第一张作为公开封面。
- `/uploads/<path>` 只服务 `institution_images` 已登记的存储键，`reports/` 和孤儿文件返回 404。
- 用户只有在拥有当前机构的已发布匹配报告时才能发布评论；评论默认等待管理员审核，用户可查看和删除自己的评论。

## 日常记录、健康数据与报告状态机

### 日常测量接口

仅 `indicator_dicts.allow_self_measurement=true` 的六项可录入：`HEIGHT`、`WEIGHT`、`HR`、`TEMP`、`SPO2`、`FBG`。数值必须非负，同日允许多次记录，只能修改或删除本人自测。前端不再提供独立日常测量模块，而由健康总览抽屉调用这些接口。

### 机构报告

机构报告只属于登录账号当前绑定机构。状态流为：

```text
draft ──lock──> locked ──submit + exact user identity──> published
```

- `draft`：只能从 `awaiting_report` 预约建立；受检者、体检日期和套餐来自预约快照，只可编辑指标。
- `locked`：复核完成后锁定，删除草稿临时原文件；内容写接口从此返回 409。
- `published`：健康身份码和真实姓名对应唯一启用注册用户，提交后立即永久归档并向用户只读开放，不可撤下或删除。

预约直接绑定注册普通用户，不再提供健康码候选匹配。提交时再次校验账号仍然启用，成功后在同一事务写入报告发布字段，并把预约由 `awaiting_report` 改为 `fulfilled`。

预约状态为 `unfulfilled → awaiting_report → fulfilled`；`unfulfilled` 可由用户取消为 `cancelled`，或由机构随时置为不可恢复的 `invalidated`。同一受检者同日已有有效预约时统一返回 `APPOINTMENT_DATE_CONFLICT`，前端显示明确业务窗口。预约成功后，机构收到接待提醒，预约人和每位受检者收到包含掩码身份信息、分院地址和套餐须知的幂等确认邮件。预约创建后即按预约日期进入本人及获授权亲友健康时间线，并使用同一预约 ID 随状态变化更新展示。每日上限为空表示不限量，正整数表示限制；降低上限不取消既有预约。

## 时间线、趋势与亲友隐私

- 时间线通过 `record_type=all|exam|self` 合并体检全生命周期和按自然日聚合的个人记录，使用专用只读 DTO。
- 趋势按日期生成一个“每日有效值”：同日存在已发布机构指标时优先；否则使用当日时间最后的自测。
- 机构报告不含某指标时，该指标仍可从当天最后一次自测取值。
- 亲友访问每次重新验证当前授权状态；亲友接口只授予查看能力，不提供代传、代记或修改入口。
- 亲友 DTO 只保留选择对象所需的账号 ID/用户名，并从健康事件中排除 `user_id`、`matched_user_id`、受检者姓名快照、健康身份码、真实姓名、生日、性别、邮箱、手机号、过敏史和既往史。
- 不存在与无权访问尽量统一为 404，减少对象存在性泄露。

## AI 助手

- 匿名用户只能访问公开 FAQ/导览，不能附带健康上下文。
- `GET /api/ai/records` 按需返回本人或授权亲友的可分析已发布报告元数据。
- `POST /api/ai/chat/stream` 使用 SSE；普通问题不读取报告，需要个人上下文但未选择时返回 `select_records` 动作。
- `POST /api/ai/analyze/stream` 支持同一归属人的单/多报告分析。
- `POST /api/ai/chat` 是非流式兼容接口。
- 每次附带报告必须显式 `consent: true`；报告、权限和状态逐请求重新校验。
- 事件仍为 `meta`、`status`、`delta`、`action`、`done`、`error`；不发送 Waitress 禁止的 `Connection` 头。
- 历史最多 20 条并做确定性裁剪；聊天和分析结果不写数据库，日志不记录消息正文、指标值或密钥。
- 当前上下文使用 `institution_reports` 与 `report_indicators`，趋势事实来自服务端每日有效序列；个人资料和健康身份码不会发送给模型。

真实模式配置见 `.env.example`。默认连接、读取、总截止为 5/30/60 秒；访客和登录用户默认每分钟 10/30 次进程内限流。

RAG 仅索引 `rag_sources/manifest.json` 批准的公共知识，不索引用户问题、聊天、OCR 原文、用户 ID 或健康指标值。首次显式执行 `.\.venv\Scripts\python.exe scripts\rag_sync.py sync`，成功后再设置 `RAG_ENABLED=1`；应用启动和请求期间不联网更新语料。来源哈希变化会进入 quarantine，审核批准后才能切换索引。SSE 增加 `status.stage=retrieving`，只返回 `rag_used`、`retrieval_status` 和 `knowledge_source_count`，不向前端暴露来源正文或 URL。

可分析对象为本人或已授权亲友的 `published` 机构报告。精确 `selected_record_ids` 与 `record_scope: {"owner_id": 2, "mode": "all_confirmed"}` 互斥，后者在 schema v8 中解析为该归属人的全部已发布报告；两种方式都必须逐请求同意并重新鉴权。

## 报告识别导入（OCR）

- 当前入口：`POST /api/org/reports/ocr`，仅机构账号。
- 导入 PDF、PNG、JPEG 或 WebP；总请求上限 20 MiB，PDF 默认最多 8 页。
- `region-v2` 按表格区域独立解析，同时保留表格外文本；不依赖固定报告坐标。
- 指标仅映射当前字典中的代码、名称和别名。英文短代码必须精确命中；未知项目进入未匹配列表。
- 重复冲突、低置信度、非法数值和不安全单位必须人工复核，不能自动写成其他指标。
- OCR 产生一个 `draft` 报告、`input_source=ocr` 指标和诊断信息；之后使用普通草稿接口修订。
- 草稿可保留临时原文件用于复核；`lock` 时物理删除，数据库清空 `temporary_file_url`。

## 文件清理

本地孤儿文件脚本默认 dry-run，只读取 SQLite 引用并限制在 `backend/uploads`：

```powershell
.\.venv\Scripts\python.exe .\scripts\cleanup_local_runtime.py
.\.venv\Scripts\python.exe .\scripts\cleanup_local_runtime.py --apply
```

它不会修改数据库、虚拟环境、生产 openGauss 或上传根目录外文件。

## 启动

开发：

```powershell
.\.venv\Scripts\python.exe run.py
```

Waitress 本机演示推荐从项目根目录运行：

```powershell
.\scripts\start-backend-prod.ps1
```

脚本默认监听 `127.0.0.1:5050`，缺少 JWT 密钥时写入随机安全值。直接调用 Waitress 或对外监听时必须手工提供至少 32 字符的 `JWT_SECRET_KEY` 和至少 12 字符的 `DEFAULT_ADMIN_PASSWORD`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

当前 56 项测试使用独立内存 SQLite，不修改 `instance/health_system.db`；覆盖 schema v8、SQLite/openGauss 结构校验、v7→v8 全量保留升级、机构主体与分院权限、跨院审计、演示数据安全重建、健康领域、套餐版本、多人预约组、同日预约冲突、候补提醒、用户与机构预约邮件、Outbox 防重复与连续中文邮件、报告指标/文字/附件、亲友边界、趋势参考范围、趋势 AI 授权、时间线、RAG 降级和全量快照迁移。完整结论见 [`../项目文档/测试报告.md`](../项目文档/测试报告.md)。

## 生产数据库同步

`DATABASE_URL` 的优先级高于 `LOCAL_DATABASE_URL`。`initialize_or_validate_schema()` 对非 SQLite 的 `create_all()` 只负责空库初始化，不用于修改已有表。显式全量同步使用：

```powershell
.\.venv\Scripts\python.exe .\scripts\migrate_sqlite_to_gaussdb.py `
  --source .\instance\health_system.db --target-url $env:TARGET_DATABASE_URL --replace
```

脚本验证源库完整性与外键，创建完整目标 schema，复制全部表、重置生成序列并逐表核对行数。`--replace` 会清空目标应用表，只能在已备份且明确允许覆盖的演示环境使用；服务器演示发布入口为 `scripts/deploy-server.ps1 -SyncDemoDatabase`，会同步 30 个 demo-v8 合成素材并安装独立通知 worker 服务，同时保留服务器 SMTP 配置。
