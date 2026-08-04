# 康康健健 HealthDoc 后端

Flask 后端负责三角色授权、实名认证、付款托管、结算退款、受控关联账号会话、健康身份码多人预约、报告复核、投诉与退款、评论治理、站内/邮件通知及 HealthDoc Agent。本地使用 SQLite schema v13；服务器通过 `DATABASE_URL` 连接 GaussDB/openGauss，并使用 v13 保留式增量迁移。

## 1.0—3.0 后端演进

- 1.0 建立 Flask API、JWT 三角色授权、机构/套餐、基础预约和健康记录。
- 2.0 增加自主测量、报告草稿/发布、时间线、趋势、亲友授权和 AI/OCR 权限链。
- 3.0 引入健康领域、套餐版本、预约组、容量候补、通知 outbox、图文报告；schema v8 进一步增加机构主体、分院和跨院访问审计。
- 当前只维护 schema v13 的统一模型和接口；旧数据库通过保留式迁移升级。

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

本地通知默认 `NOTIFICATION_EMAIL_DRY_RUN=1`，只验证 Outbox/重试流程而不连接外部邮箱。需要联调真实 SMTP 时，在被 Git 忽略的 `.env` 中配置 `SMTP_*` 并设为 `0`；`NOTIFICATION_EMAIL_REDIRECT` 可将所有通知统一投递到一个测试收件箱。注册必须填写有效邮箱，但邮箱只作为通知渠道，不作为账号唯一标识，因此家庭成员和验收账号可以共用一个邮箱；空位提醒直接使用当前账号绑定的通知邮箱。新建验收库时可用 `DEMO_SHARED_EMAIL` 统一绑定测试账号，真实地址不要写入受版本控制的文件。

生产环境必须设置至少 32 字符的高熵 `ACCOUNT_CREDENTIAL_ENCRYPTION_KEY`，用于加密发件箱中尚未发送的机构初始账密。它必须与 `AGENT_DATA_ENCRYPTION_KEY`、JWT 和 SMTP 凭据独立，不得提交真实值；冷备份和恢复须连同 root-only 环境文件保留该密钥，随意轮换会使尚未清除的待发凭据无法解密。

根目录的 `scripts/start-full-dev.ps1` 和 `scripts/start-full-prod.ps1` 会在后端就绪后自动启动隐藏的常驻 worker，每 5 秒处理一次 Outbox，并在前端命令退出时停止。单独运行可使用 `scripts/start-notification-worker.ps1`，或在后端目录执行 `python scripts/notification_worker.py --watch --interval-seconds 5`。条件更新保证误开两个 worker 时同一条通知只会被一个进程并发领取；`sending` 使用 300 秒租约，崩溃遗留的过期租约会返回重试队列。收到 SIGTERM 后 worker 不再领取下一条，而是完成当前 SMTP 结果落库后退出。SMTP 与数据库无法组成原子事务，因此这里明确采用 at-least-once：极端崩溃发生在 SMTP 已接收、数据库尚未落库之间时可能重发，但不会把记录永久卡在 `sending`。发送前会把 Outbox 载荷转换为连续的中文业务文本，不会把 JSON 原文发给用户。生产 systemd 单元还传入 `--start-gate-file /var/lib/healthdoc/notification-worker.enabled`：发布候选阶段 worker 可以启动并等待，但门闩文件创建前不会领取或发送任何 Outbox。

## 数据库与 schema v13

默认数据库为 `instance/health_system.db`。SQLite 连接启用外键和 30 秒写锁等待；`PRAGMA user_version=13` 标识当前结构。新空库直接创建 v13，旧库使用原子副本升级；生产 openGauss/GaussDB 使用 `migrations/versions/20260804_schema_v13.py` 与 `scripts/migrate_schema_v13.py`。

```powershell
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
```

验收业务数据可通过专用脚本重建；`--check-only` 只校验目标库和账号边界，`--apply --yes` 才会覆盖验收业务记录并保留全部验收账号及密码哈希：

```powershell
.\.venv\Scripts\python.exe .\scripts\reset_v13_demo_data.py --check-only
.\.venv\Scripts\python.exe .\scripts\reset_v13_demo_data.py --apply --yes
.\.venv\Scripts\python.exe .\scripts\validate_v13_demo.py `
  --database .\instance\health_system.db --upload-dir .\uploads
```

升级与重建都会先校验完整性并生成时间戳备份。当前核心表包括：

- 平台与授权：`users`、`friend_relations`、`organizations`、`institutions`、`packages`、`institution_invites`、`institution_images`、`comments`；
- 指标字典：`indicator_categories`、`indicator_dicts`、`indicator_reference_rules`；
- 健康模型：`health_domains`、`indicator_domain_links`、`self_measurements`、`institution_reports`、`report_indicators`、`report_text_results`、`report_assets`、`report_access_logs`；
- 套餐与预约：`package_versions`、`package_version_domains`、`booking_groups`、`appointments`、`appointment_events`、`appointment_capacity_slots`、`waitlist_subscriptions`、`waitlist_subscription_participants`；
- 规范附件：`report_asset_types`、`package_version_asset_requirements`、`report_assets`；
- 通知可靠性：`availability_notification_events`、`notification_outbox`、`user_notifications`。
- Agent：`agent_threads`、`agent_runs`、`agent_tool_events`、`agent_pending_actions`、`agent_action_executions`、`support_handoffs`；
- OAuth/MCP：`oauth_clients`、`oauth_authorization_codes`、`oauth_access_tokens`、`oauth_refresh_tokens`。
- v12 关联与预约凭据：`delegation_session_audits`、`delegated_action_audits`、`booking_participant_tokens`、`booking_participant_authorizations`；
- v12 治理与画像：`appointment_complaints`、`complaint_messages`、`complaint_events`、`comment_sanctions`、`comment_appeals`、`institution_audience_insight_cache`。

## 角色与账号规则

- `user`：必须有唯一 `health_id`，不能绑定机构；完成姓名、性别、出生日期前，业务写操作统一返回 `IDENTITY_REQUIRED`。
- `institution_admin`：必须绑定一个具体分院，不拥有健康身份码；账号无总部权限。本院报告可生产和归档，同机构兄弟分院已归档报告仅可查看并写入审计日志。
- `admin`：不能绑定机构，也不拥有健康身份码。
- `is_active=false` 后，登录、刷新令牌及所有角色保护接口立即拒绝账号。
- 普通用户注册时健康身份码由服务端生成，前端不能指定或修改。
- 公共注册只创建普通用户。分院与唯一机构账号由系统管理员在同一事务创建；初始凭据只在加密 Outbox 暂存，邮件成功后清除。
- 系统管理员可停用/恢复账号。删除普通用户需显式 `confirm=true` 并级联其健康数据；删除机构账号保留报告及创建者用户名快照，只把创建者外键置空。

## 当前 API 分区

| 前缀 | 主要角色 | 内容 |
|---|---|---|
| `/api/auth` | 公开/登录用户 | 图片验证码、注册、登录、刷新、注销 |
| `/api/public` | 公开 | 启用机构、分院、在售套餐和平台联系方式的脱敏只读目录 |
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
| `/api/friends` | 普通用户 | 双向关联账号申请、接受、双方备注、撤销、列表与受控切换 |
| `/api/notifications` | 普通用户 | 站内通知、未读数和业务跳转 |
| `/api/institutions` | 登录用户 | 启用机构、详情和套餐浏览 |
| `/api/appointments`、`/api/booking-groups` | 普通用户 | 上海时区明天至第 30 天、三类参与人、受检预约、代预约回执与部分取消 |
| `/api/waitlist-subscriptions` | 普通用户 | 候补提醒创建、查看和取消 |
| `/api/comments` | 用户/管理员 | 公开评论、隐藏原因、7/30 天或永久禁言与单次申诉 |
| `/api/complaints` | 用户/机构/管理员 | 每个预约一条投诉、追加式消息/事件、升级、接管和关闭 |
| `/api/indicators/dicts` | 登录用户 | 指标字典 |
| `/api/org` | 机构账号 | 本机构资料、套餐审核申请、预约履约、相册和报告生产 |
| `/api/admin` | 系统管理员 | 平台统计、分院+账号创建、身份修正、套餐审批、投诉和评论治理 |
| `/api/users` | 系统管理员 | 账号列表、停用、恢复和删除 |
| `/api/ai` | 访客/普通用户 | FAQ、流式对话、报告列表和分析 |
| `/api/agent` | 普通用户/管理员 | Agent 线程、SSE 运行、第一方确认、人工工单运营 |
| `/oauth`、`/.well-known` | 外部客户端/普通用户 | 动态注册、PKCE 授权、token 轮换与撤销 |
| `/api/admin/oauth-clients` | 系统管理员 | 外部客户端审批、拒绝与撤销 |

所有受限接口都在服务端逐请求查询账号、角色、启用状态和机构绑定。前端隐藏菜单不是安全边界。

平台固定联系方式由 `app/services/platform_contact.py` 统一维护，并由 `GET /api/public/contact` 及公开目录响应提供；前端展示和文档不得建立另一套运行时配置来源。

## 机构、套餐、相册和评论

- 机构采用 `is_active` 软停用；套餐新增、修改、下架和恢复都由所属机构提交审核申请，管理员只能通过或驳回，不能直接改套餐。
- 通过申请时套餐与审核状态在同一事务更新；驳回不影响当前套餐，待审申请可撤回后重提，完整前后值与操作人永久留痕。
- 每机构最多 8 张 JPEG、PNG 或 WebP 图片，单张最大 5 MB；服务端真实解码、修正方向、重编码并清除 EXIF。
- 相册排序一次提交完整 ID 集合并归一化；第一张作为公开封面。
- `/uploads/<path>` 只服务 `institution_images` 已登记的存储键，`reports/` 和孤儿文件返回 404。
- 用户只有在拥有当前机构的已发布匹配报告时才能发布评论；评论处罚只影响发言，不影响预约、报告或投诉。每次处罚最多申诉一次，解封不自动恢复隐藏评论。

## 日常记录、健康数据与报告状态机

### 日常测量接口

仅 `indicator_dicts.allow_self_measurement=true` 的六项可录入：`HEIGHT`、`WEIGHT`、`HR`、`TEMP`、`SPO2`、`FBG`。数值必须非负，同日允许多次记录，只能修改或删除本人自测。前端不再提供独立日常测量模块，而由健康总览抽屉调用这些接口。

### 机构报告

机构报告只属于登录账号当前绑定机构。状态流为：

```text
draft ──上传医生确认──> pending_review ──复核医生确认──> published
```

- `draft`：从已到检预约建立，可上传、OCR、录入和修改。
- `pending_review`：上传确认必须记录上传医生；机构仍可修正指标、结论和附件。
- `published`：复核确认必须记录复核医生；发布、预约履约、事件、通知、锁档和临时文件清理同事务完成，之后不可修改或撤回。

预约参与人可来自当前有效账号、已关联账号或短时一次性健康码参与人令牌。令牌只保存哈希，绑定预约人和目标用户，提交时重新校验并以条件更新一次性消费；原始健康身份码不写 URL、持久缓存、普通日志或模型上下文。Agent 另外使用线程级非秘密 slot：真实 bearer 只存在于加密线程状态的独立映射，模型消息和历史只保存 slot，工具调用进入类型化执行边界时才解析为 bearer。

预约状态为 `unfulfilled → awaiting_report → fulfilled`；未履约记录可取消，机构取消和未到检分别保留明确终态。所有创建入口按上海时区只接受明天至第 30 天；升级前已有当天预约不追溯取消。预约成功后，机构收到接待提醒，受检者收到站内通知及可用邮箱通知；原预约人另见脱敏代预约回执。每日上限为空表示不限量，正整数表示限制；降低上限不取消既有预约。

## 时间线、趋势与亲友隐私

- 时间线通过 `record_type=all|exam|self` 合并体检全生命周期和按自然日聚合的个人记录，使用专用只读 DTO。
- 趋势按日期生成一个“每日有效值”：同日存在已发布机构指标时优先；否则使用当日时间最后的自测。
- 机构报告不含某指标时，该指标仍可从当天最后一次自测取值。
- 健康时间线、体检数据、趋势和 Agent 健康读取只访问当前有效账号，不再接受亲友 owner 选择器。
- 关联切换使用受控会话，记录真实操作者、有效账号、完整链，最多三层且禁止重复/循环；撤销、停用、密码安全版本变化或链路失效会立即使下游会话失效。
- 健康码匹配只返回姓名、性别、出生年份和脱敏身份码；服务端可采用最近身高体重生成预约快照，但不向未关联预约人返回历史数值。
- 不存在与无权访问尽量统一为 404，减少对象存在性泄露。

## AI 助手

- 匿名用户只能访问公开 FAQ/导览，不能附带健康上下文。
- `GET /api/ai/records` 按需返回当前有效账号的可分析已发布报告元数据；切换关联账号前不能直接选择亲友作为 owner。
- `POST /api/ai/chat/stream` 使用 SSE；普通问题不读取报告，需要个人上下文但未选择时返回 `select_records` 动作。
- `POST /api/ai/analyze/stream` 支持同一归属人的单/多报告分析。
- `POST /api/ai/chat` 是非流式兼容接口。
- 每次附带报告必须显式 `consent: true`；报告、权限和状态逐请求重新校验。
- 事件仍为 `meta`、`status`、`delta`、`action`、`done`、`error`；不发送 Waitress 禁止的 `Connection` 头。
- 历史最多 20 条并做确定性裁剪；聊天和分析结果不写数据库，日志不记录消息正文、指标值或密钥。
- 当前上下文使用 `institution_reports` 与 `report_indicators`，趋势事实来自服务端每日有效序列；个人资料和健康身份码不会发送给模型。

真实模式配置见 `.env.example`。默认连接、读取、总截止为 5/30/60 秒；访客和登录用户默认每分钟 10/30 次进程内限流。

RAG 仅索引 `rag_sources/manifest.json` 批准的公共知识，不索引用户问题、聊天、OCR 原文、用户 ID 或健康指标值。首次显式执行 `.\.venv\Scripts\python.exe scripts\rag_sync.py sync`，成功后再设置 `RAG_ENABLED=1`；应用启动和请求期间不联网更新语料。来源哈希变化会进入 quarantine，审核批准后才能切换索引。SSE 增加 `status.stage=retrieving`，只返回 `rag_used`、`retrieval_status` 和 `knowledge_source_count`，不向前端暴露来源正文或 URL。

可分析对象只属于当前有效账号的 `published` 机构报告；查看关联账号前必须先建立受控切换会话。精确 `selected_record_ids` 与 `record_scope: {"owner_id": <当前有效账号 ID>, "mode": "all_confirmed"}` 互斥，服务端对两种方式都逐请求校验 owner、状态、指标存在性和本次同意；旧客户端传入其他亲友 `owner_id` 会被拒绝，不能绕过账号切换。

## HealthDoc Agent

`POST /api/agent/threads/{id}/runs/stream` 由 LangGraph 驱动。DeepSeek 只负责规划和解释，Pydantic 类型化工具负责读取档案、确定性计算趋势、比较机构/套餐、查询容量和预约状态。预约、取消、候补、人工客服工具只创建 AES-GCM 加密草稿；用户通过 `/api/agent/actions/{id}/decision/stream` 确认后，服务端重新校验并调用原预约领域函数，Action ID 保证重复确认不重复写入。

`python scripts/cleanup_agent_state.py` 负责使过期草稿失效并覆盖闲置线程密文；服务器由 `healthdoc-agent-cleanup.timer` 每小时触发。外部 MCP 进程只监听 loopback 5051，通过 OAuth access token 和内部共享密钥调用同一工具网关，不直连数据库或 Qdrant。

## 报告识别导入（OCR）

- 当前入口：`POST /api/org/reports/ocr`，仅机构账号。
- 导入 PDF、PNG、JPEG 或 WebP；总请求上限 20 MiB，PDF 默认最多 8 页。
- `region-v2` 按表格区域独立解析，同时保留表格外文本；不依赖固定报告坐标。
- 指标仅映射当前字典中的代码、名称和别名。英文短代码必须精确命中；未知项目进入未匹配列表。
- 重复冲突、低置信度、非法数值和不兼容单位必须人工复核，不能自动写成其他指标。
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

Waitress 本机验收推荐从项目根目录运行：

```powershell
.\scripts\start-backend-prod.ps1
```

脚本默认监听 `127.0.0.1:5050`，缺少 JWT 密钥时写入随机安全值。直接调用 Waitress 或对外监听时必须手工提供至少 32 字符的 `JWT_SECRET_KEY` 和至少 12 字符的 `DEFAULT_ADMIN_PASSWORD`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

后端自动化默认使用独立内存 SQLite，不修改 `instance/health_system.db`；v12 数据强校验器必须显式传入数据库和上传目录，并以只读方式核对 schema、账号、数据与媒体。最终门禁结论见 [`../项目文档/测试报告.md`](../项目文档/测试报告.md)。

## 生产数据库同步

`DATABASE_URL` 的优先级高于 `LOCAL_DATABASE_URL`。`initialize_or_validate_schema()` 对非 SQLite 的 `create_all()` 只负责空库初始化，不用于修改已有表。显式全量同步使用：

```powershell
.\.venv\Scripts\python.exe .\scripts\migrate_sqlite_to_gaussdb.py `
  --source .\instance\health_system.db --target-url $env:TARGET_DATABASE_URL --replace
```

脚本验证源库完整性与外键，创建完整目标 schema，复制全部表、重置生成序列并逐表核对行数。`--replace` 会清空目标应用表，只能在已备份且明确允许覆盖的隔离验收环境使用。普通服务器素材更新使用 `scripts/deploy-server.ps1 -SyncDemoMedia`，只同步清单限定的 `demo-v8/demo-v10` 素材；完整验收库覆盖才使用 `-SyncDemoDatabase`。

服务器发布为每个 `/opt/healthdoc/releases/<release_id>` 创建独立 `venv`，systemd 统一从 `/opt/healthdoc/current/venv` 启动；发布失败不会改写旧 release 的依赖。切换前会备份数据库、上传、环境、Apache、旧 release，以及 HealthDoc systemd unit 文件和 enable/mask 状态，并验证冷备 tar 可读；完整细节见 [`../项目文档/服务器部署与同步.md`](../项目文档/服务器部署与同步.md)。
