# 康康健健 HealthDoc 后端

Flask 后端负责三角色认证与授权、健康档案、OCR、指标标准化、机构运营、系统管理和机构图片处理。本地默认使用 SQLite schema v2，生产环境可通过 `DATABASE_URL` 使用 GaussDB/openGauss。

## 环境与安装

要求 Python 3.10+。在 backend 目录执行：

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
~~~

可选配置：

~~~powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
~~~

后端默认监听 http://127.0.0.1:5050，健康检查为 GET /api/health。

## 数据库

默认数据库：

~~~text
instance/health_system.db
~~~

生产服务器设置 `DATABASE_URL=opengauss+psycopg2://...` 后使用 openGauss；`DATABASE_URL` 的优先级高于本地 `LOCAL_DATABASE_URL`。一次性数据迁移脚本为 `scripts/migrate_sqlite_to_gaussdb.py`，日常代码发布不应重复覆盖线上数据。

开发入口和 Waitress 入口读取同一个文件。2026-07-11 的 schema v2 迁移验收基线为：

| 检查项 | 迁移验收值 |
|---|---:|
| PRAGMA user_version | 2 |
| users | 2 |
| institutions | 3 |
| packages | 15 |
| health_records | 12 |

本地数据库已被 Git 忽略，后续业务行数会随测试和演示变化；这些数量只记录迁移时的数据保留结果。

每个 SQLite 连接自动启用：

~~~sql
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
~~~

新空库会直接创建最新模型并设置 user_version=2，然后写入默认管理员、3 家机构、15 个套餐和指标字典。非空旧库若版本不是 2，应用会在创建新表前拒绝启动并给出迁移提示，避免 create_all 形成半升级结构。

如需切换另一个本地 SQLite 文件，可在 .env 设置：

~~~env
LOCAL_DATABASE_URL=sqlite:///another-local.db
~~~

## v2 结构升级

先停止后端，再检查：

~~~powershell
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
~~~

需要升级时执行：

~~~powershell
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
~~~

脚本流程：

1. 读取旧库并预检源表。
2. 按当前 SQLAlchemy 模型创建临时 v2 数据库。
3. 按列交集和外键顺序复制数据，并迁移旧 logo_url 为相册首图。
4. 验证业务表行数、主键、外键、唯一约束、角色约束和 integrity_check。
5. 设置 PRAGMA user_version=2。
6. 创建 instance/health_system.before-schema-v2-时间戳.db 备份。
7. 原子替换正式数据库。

迁移失败时正式文件不会被替换。对已经是 v2 的数据库重复运行是幂等检查，不会再次重建。

本地测试和 OCR 调试可能留下未被数据库引用的上传文件。先预览、再清理：

~~~powershell
.\.venv\Scripts\python.exe .\scripts\cleanup_local_runtime.py
.\.venv\Scripts\python.exe .\scripts\cleanup_local_runtime.py --apply
~~~

清理器会递归检查档案、待确认 OCR 附件和机构相册引用，不修改数据库，也不会处理服务器 openGauss 数据。

## 数据模型要点

- users.role：user、institution_admin、admin。
- users.managed_institution_id：机构管理员所属机构；唯一且仅 institution_admin 可非空。
- institutions.is_active、packages.is_active：软停用状态。
- institution_invites：只保存邀请码 SHA-256 哈希、状态、签发人、使用人和撤销信息。
- institution_images：每机构 0–7 排序位，第一张为封面。
- health_records.institution_id 与 package_id 可为空，未选择机构也可正常保存档案和指标。
- health_records 的用户展示编号由内部主键派生为 `health{id}`，通过 `display_id` 返回；数据库仍使用整数主键，API 路径继续传数字 ID。
- `ocr_raw_text` 保存 OCR 解析快照、`parser_version` 和候选诊断；既有档案重新 OCR 时还会暂存带随机 `attachment_id` 的待确认附件，但不会新增分析结果或聊天记录表。

## 接口分区

| 前缀 | 使用者 | 主要能力 |
|---|---|---|
| /api/auth | 公开/全部角色 | 验证码、注册、登录、刷新、退出；注册可选 invite_code |
| /api/records、/api/trends、/api/friends | 普通用户 | 本人/授权亲友档案、趋势和亲友授权 |
| /api/institutions、/api/comments | 普通用户/系统管理员 | 登录用户的机构服务与评论；评论审核操作仅限系统管理员 |
| /api/org | 机构管理员 | 所属机构 dashboard、资料、套餐、相册 |
| /api/institution-health | 机构管理员 | 本机构 confirmed 档案、指标和趋势的脱敏只读查询 |
| /api/admin | 系统管理员 | dashboard、机构、套餐、图片、邀请码、机构管理员撤销、全局档案监管 |
| /api/users | 当前用户/系统管理员 | 当前资料、用户与角色管理 |
| /api/ai | 公开/普通用户 | 匿名导览和普通用户健康科普 |

普通用户、机构管理员和系统管理员由独立角色守卫保护，不能跨工作台复用接口。

## 邀请码与机构管理员

- 每个机构最多一个当前邀请码和一个机构管理员。
- 邀请码由系统管理员签发，单次使用、永不过期，明文仅在签发响应中返回一次。
- 重新签发会立即替换旧码；数据库和后续查询均不返回明文或哈希。
- 注册成功与邀请码消费在同一事务中完成，可防止重复或并发消费。
- 系统管理员撤销机构管理员后，账号立即降级为普通用户并解除机构绑定；个人数据保留。
- 停用机构会撤销其管理员和未使用邀请码，但保留历史档案、套餐、评论和图片。

## 机构图片

系统管理员与对应机构管理员均可管理相册：

- 每机构最多 8 张。
- 接受 JPEG、PNG、WebP。
- 单张最多 5 MB。
- 服务端使用 Pillow 实际解码并重编码，清除 EXIF。
- 支持拖拽排序，排序第一张为封面。
- API 不暴露内部 storage_key。
- 公共 /uploads 只提供 `institution_images` 登记的机构图片；未登记孤儿文件和 reports/ 路径一律拒绝。新图片仍存入 institutions/，旧 logo 路径可通过数据库白名单兼容。

## 健康档案与隐私

- 机构、套餐均为可选；选择套餐会校验并推导机构，清空机构会清空套餐。
- 数值型指标会清理空格、全角符号、单位文本和方向箭头后保存标准数值。
- 不支持的单位不静默换算，返回人工确认。
- 只有来源为当前机构且状态为 confirmed 的标准化数据可供机构管理员读取。
- 机构健康接口不返回邮箱、手机号、上传人和原始报告，且没有写操作。
- 机构健康访问只写不含健康明细的结构化运行日志。
- 原始报告由鉴权接口 GET /api/records/{id}/file 返回，普通公共上传路径不能读取报告。

## AI 助手

AI 接口支持：

- 匿名用户：公开系统 FAQ 和导览，不允许档案上下文。
- `GET /api/ai/records`：按需返回本人及已授权亲友的可分析档案；只包含已确认且至少有一项指标的档案。
- `GET /api/ai/records` 同时返回按归属人聚合的 `owners`（档案数和日期范围）。
- `POST /api/ai/chat/stream`：SSE 流式对话；普通问题不会读取档案，涉及个人报告且未选择档案时返回 `select_records` 动作。
- `POST /api/ai/analyze/stream`：同一归属人的档案可多选；服务端先确定性计算指标变化，再交给 AI 解释。
- `POST /api/ai/chat`：保留的非流式兼容接口。
- 发送任何档案前必须为本次请求提交 `consent: true`；档案 ID、状态、指标和亲友授权会在每次请求中重新校验。
- 可分析档案必须为本人或已授权亲友的 `confirmed` 档案且至少有一项指标；一次请求必须属于同一所有者，不设置数量上限，数据库查询会分批执行。
- 请求可在 `selected_record_ids` 精确档案和 `record_scope: {"owner_id": 2, "mode": "all_confirmed"}` 全部历史之间二选一；两者互斥且每次均需 `consent: true`。
- 两类管理员：后台不提供健康 AI，也不能把健康档案 ID 作为 AI 上下文。

后端不保存聊天或分析结果。历史满 20 条时使用本地确定性裁剪与摘要合并，不会额外调用一次模型。流事件使用 `meta`、`status`、`delta`、`action`、`done`、`error`；所有流请求带 `request_id`，错误事件包含稳定 `code` 和 `retryable`。响应只发送 `Cache-Control` 与 `X-Accel-Buffering` 等端到端头，不设置 Waitress/WSGI 禁止的 `Connection` 头。

真实模式从 .env 读取：

~~~env
AI_PROVIDER=deepseek
AI_USE_MOCK=0
DEEPSEEK_API_KEY=
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
AI_CONNECT_TIMEOUT_SECONDS=5
AI_READ_TIMEOUT_SECONDS=30
AI_REQUEST_TIMEOUT_SECONDS=60
AI_MAX_HISTORY_MESSAGES=20
AI_SUPPORT_PHONE=
AI_GUEST_RATE_LIMIT_PER_MINUTE=10
AI_AUTH_RATE_LIMIT_PER_MINUTE=30
RAG_ENABLED=0
RAG_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
RAG_QDRANT_URL=
~~~

RAG 仅索引批准的公共知识，不索引用户问题、聊天、OCR 原文、用户 ID 或健康指标值。应用启动与请求期间不联网；首次在 `backend` 目录显式执行：

~~~powershell
.\.venv\Scripts\python.exe scripts\rag_sync.py sync
~~~

同步成功后把 `.env` 中 `RAG_ENABLED=1` 并重启后端。远端文件哈希变化时新文件会进入 `instance/rag/sources/*/quarantine-*.pdf`，旧索引继续工作；审核后用 `rag_sync.py approve-change <source_id> <sha256>` 批准，再次同步。SSE 新增 `status.stage=retrieving`，完成结果只返回 `rag_used`、`retrieval_status` 和 `knowledge_source_count`，不把来源正文或 URL 暴露给前端。

本地 100 份纵向档案的默认操作是 dry-run：

~~~powershell
$env:RAG_DEMO_PASSWORD="至少12字符的本地演示密码"
.\.venv\Scripts\python.exe scripts\seed_rag_demo.py apply
.\.venv\Scripts\python.exe scripts\seed_rag_demo.py verify
.\.venv\Scripts\python.exe scripts\seed_rag_demo.py cleanup
~~~

脚本只管理 `rag_demo_01` 至 `rag_demo_05` 和运行时 manifest 中记录的数据；重复执行不会增加数据。

连接超时、读取超时和总截止默认分别为 5/30/60 秒。上游在首个模型内容到达前发生连接中断或 502/503/504 时最多自动重试一次；客户端取消或断开会关闭上游流。结构化日志只记录模式、档案数量、提示长度、首次正文 `delta`/总耗时、状态和 token 用量，不记录消息正文、指标值或密钥。

离线测试可设置 AI_USE_MOCK=1。真实密钥不得写入源码、.env.example、测试输出或提交记录。请求与事件完整示例见 [`../项目文档/AI与OCR开发说明.md`](../项目文档/AI与OCR开发说明.md)。

## OCR

离线开发建议：

~~~env
OCR_USE_MOCK=1
~~~

真实 OCR 需在 .env 配置 HUAWEI_OCR_ENDPOINT、HUAWEI_OCR_AK、HUAWEI_OCR_SK 和 HUAWEI_PROJECT_ID。OCR 生成候选映射，用户确认后才形成 confirmed 档案。

真实华为通用表格 OCR 使用 `region-v2` 解析器：

- 每个 `table` 区域分别收集单元格、识别表头和推断标签/结果列，避免多个表格复用相同行列编号时发生串行或覆盖。
- 表格候选与表格之外的文本行并行提取，不再因为已经找到若干表格字段就关闭文本兜底；支持带表头表格、无表头代码/名称/结果表以及无边框“名称 + 数值”列表。
- 同一指标出现多个候选时先按规范值去重；值冲突、非法数值、低置信度或不安全的模糊匹配会设置 `requires_review`，并把分数压到自动确认阈值以下。
- 英文短代码只接受指标字典中的精确代码/别名，不做任意子串命中，避免把其他项目误映射为短代码指标。
- 数值必须只有一个明确数字；小数逗号/千分位逗号等有歧义的 OCR 文本不被静默改写。`μmol/L`、`µmol/L`、`umol/L` 和常见 `mol·L⁻¹` 写法只作为等价单位记法规范化，不进行未经配置的单位换算。

指标映射的边界是当前 `indicator_dicts` 配置。未配置的医疗项目、报告元数据和无法安全判定的字段会进入未匹配/过滤/人工复核结果，不会靠相似名称猜测或自动写库。

OCR 支持两种流程：

- 新档案：`POST /api/records/upload` 发送文件、体检日期和归属信息，创建 `parsed` 档案；`PUT /api/records/{id}/confirm` 确认候选映射。
- 既有档案：同一上传接口额外发送 `record_id`，服务端只写入待确认快照并保持原 `status`、正式报告和指标不变；`GET /api/records/{id}/ocr-pending` 可恢复页面，确认时必须带匹配的 `attachment_id`，也可用 `DELETE /api/records/{id}/ocr-pending` 放弃。
- 既有档案确认支持 `ocr_update_mode=replace_ocr|merge`。前端默认 `replace_ocr`：只删除未出现在本次已确认映射中的旧 OCR 来源指标，手工来源指标始终保留；`merge` 只做 upsert 并保留旧 OCR 指标。为兼容旧调用方，服务端省略该字段时仍默认 `merge`。
- OCR 快照记录 `pages_total/processed/succeeded/failed/empty/truncated` 与 `replacement_safe`。只要存在请求失败页、HTTP 成功但未提取到字段的空页、超过 `OCR_PDF_MAX_PAGES` 的截断页，或旧快照缺少完整性证据，后端都会拒绝 `replace_ocr`；同时要求替换请求显式确认每一个候选，避免默认忽略项导致旧值被误删。
- 新建 OCR 档案若结果不完整，`PUT /api/records/{id}/confirm` 必须显式携带 `accept_incomplete_ocr=true`。前端通过醒目警告和二次确认生成该字段，旧客户端无法绕过服务端保护。

既有档案补传上传、补传确认、补传取消和档案删除共享基于 `ocr_raw_text` 快照的乐观并发控制。旧页面或并发操作分别返回 `OCR_ATTACHMENT_CONFLICT`、`OCR_ATTACHMENT_STALE` 或 `RECORD_DELETE_CONFLICT`（HTTP 409），并清理未被数据库采用的新文件。直接新建的 OCR 档案仍按普通 `parsed → confirmed` 流程处理。

## 启动

开发：

~~~powershell
.\.venv\Scripts\python.exe run.py
~~~

Waitress 本机演示（推荐从项目根目录使用启动脚本）：

~~~powershell
..\scripts\start-backend-prod.ps1
~~~

项目脚本默认监听 `127.0.0.1`：首次启动会自动在 `.env` 生成随机 JWT 密钥，并设置仅对当前回环进程生效的本机演示标志，因此可以继续使用本地演示账号。

直接调用 Waitress 不会执行自动初始化；必须事先在 `.env` 设置至少 32 字符的 `JWT_SECRET_KEY` 和至少 12 字符的 `DEFAULT_ADMIN_PASSWORD`：

~~~powershell
.\.venv\Scripts\python.exe -m waitress --listen=127.0.0.1:5050 --threads=8 wsgi:app
~~~

改为 `0.0.0.0` 等对外地址时同样要求强配置，并且必须限制防火墙访问范围。

项目根目录还提供 scripts/start-backend-dev.ps1 与 scripts/start-backend-prod.ps1。

## 测试

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
~~~

当前完整结果：161 passed。

测试使用独立内存 SQLite，不修改 instance/health_system.db；覆盖三角色路由与接口隔离、邀请码生命周期和并发消费、一机构一管理员、机构软停用、相册限制与清理、可选机构、指标规范化、报告文件保护、机构数据脱敏只读、AI SSE/分析/超时/取消、归属人范围与逐请求授权、RAG 切分/同步/重排/安全降级，以及 OCR 多表区域隔离、表格/文本并行、无表头列推断、短代码误匹配防护、候选冲突复核、数值/单位安全、既有档案 replace/merge 和并发控制。

另用三份不含真实健康信息的合成报告调用真实华为 OCR 完成 smoke：中文表格、中文列表和英文行式报告分别准确映射 10/9/8 个已配置指标，均无冲突或需复核项。该结果验证的是多布局通用解析链路，不代表未配置的任意医疗项目会被自动识别；未知指标仍需先扩充字典或人工处理。

GitHub Actions 使用 Python 3.12 执行 `pip check` 和完整 Pytest；前端任务使用 Node.js 20 执行依赖审计、Vitest 与生产构建，且不注入真实 DeepSeek 或华为云密钥。
