# Health System Backend

Flask 后端负责三角色认证与授权、健康档案、OCR、指标标准化、机构运营、系统管理、机构图片处理和本地 SQLite schema v2。

## 环境与安装

要求 Python 3.10+。在 backend 目录执行：

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
~~~

可选配置：

~~~powershell
Copy-Item .env.example .env
~~~

后端默认监听 http://127.0.0.1:5050，健康检查为 GET /api/health。

## SQLite 数据库

默认数据库：

~~~text
instance/health_system.db
~~~

开发入口和 Waitress 入口读取同一个文件。当前正式库已经迁移到 schema v2：

| 检查项 | 当前值 |
|---|---:|
| PRAGMA user_version | 2 |
| users | 2 |
| institutions | 3 |
| packages | 15 |
| health_records | 12 |

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

## 数据模型要点

- users.role：user、institution_admin、admin。
- users.managed_institution_id：机构管理员所属机构；唯一且仅 institution_admin 可非空。
- institutions.is_active、packages.is_active：软停用状态。
- institution_invites：只保存邀请码 SHA-256 哈希、状态、签发人、使用人和撤销信息。
- institution_images：每机构 0–7 排序位，第一张为封面。
- health_records.institution_id 与 package_id 可为空，未选择机构也可正常保存档案和指标。

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
- `POST /api/ai/chat/stream`：SSE 流式对话；普通问题不会读取档案，涉及个人报告且未选择档案时返回 `select_records` 动作。
- `POST /api/ai/analyze/stream`：同一归属人的档案可多选；服务端先确定性计算指标变化，再交给 AI 解释。
- `POST /api/ai/chat`：保留的非流式兼容接口。
- 发送任何档案前必须为本次请求提交 `consent: true`；档案 ID、状态、指标和亲友授权会在每次请求中重新校验。
- 两类管理员：后台不提供健康 AI，也不能把健康档案 ID 作为 AI 上下文。

后端不保存聊天或分析结果。流事件依次使用 `meta`、`status`、`delta`、`action`、`done`、`error`；真实模式从 .env 读取：

~~~env
AI_PROVIDER=deepseek
AI_USE_MOCK=0
DEEPSEEK_API_KEY=
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
AI_CONNECT_TIMEOUT_SECONDS=5
AI_READ_TIMEOUT_SECONDS=30
AI_REQUEST_TIMEOUT_SECONDS=60
AI_SUPPORT_PHONE=
~~~

离线测试可设置 AI_USE_MOCK=1。真实密钥不得写入源码、.env.example、测试输出或提交记录。

## OCR

离线开发建议：

~~~env
OCR_USE_MOCK=1
~~~

真实 OCR 需在 .env 配置 HUAWEI_OCR_ENDPOINT、HUAWEI_OCR_AK、HUAWEI_OCR_SK 和 HUAWEI_PROJECT_ID。OCR 生成候选映射，用户确认后才形成 confirmed 档案。

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

当前完整结果：81 passed。

测试使用独立内存 SQLite，不修改 instance/health_system.db；覆盖三角色路由与接口隔离、邀请码生命周期和并发消费、一机构一管理员、机构软停用、相册限制与清理、可选机构、指标规范化、报告文件保护、机构数据脱敏只读、AI/OCR、schema v2 模型约束及迁移幂等性。
