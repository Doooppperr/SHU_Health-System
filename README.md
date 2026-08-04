# 康康健健 HealthDoc：健康管理、体检预约与机构履约系统

康康健健 HealthDoc 是一个基于 Flask 与 Vue 3 的 B/S 健康服务平台，当前数据库基线为 schema v13。第七轮建立“下单付款—平台托管—报告交付—七日结算—机构到账—投诉退款”的正式资金闭环，并与既有预约、报告、通知、治理和 Agent 能力统一运行。

## 1.0—3.0 演进概览

| 阶段 | 产品重点 | 当前状态 |
|---|---|---|
| 1.0 | 三角色、机构与套餐浏览、基础预约和健康记录 | 基础账号、角色和服务入口继续保留 |
| 2.0 | 自主测量、时间线、趋势、亲友授权、机构报告履约和 AI/OCR | 已融入当前健康中心与机构任务流 |
| 3.0 | 平台化 UI、健康领域、套餐版本、多人预约、候补通知、图文报告和分院协作 | 历史数据库基线为 schema v8 |
| 4.0 | 账号安全、全站中文化、跨来源趋势、机构评价回复和套餐详情 | 历史数据库基线为 schema v9 |
| 5.0 | 健康身份码亲友授权、预约资料副本、规范附件、104 项指标、系统数据 AI、全站分页与通知中心 | 历史数据库基线为 schema v10 |
| 6.0 / schema v11 | LangGraph Agent、类型化工具、确认/幂等写入、混合 RAG、OAuth/MCP 和 OTel | 历史 Agent 基线 |
| 第六轮验收 / schema v12 | 访客浏览、机构单账号、实名认证、关联账号、多来源预约、报告复核、投诉、评论治理和经营画像 | 历史业务基线 |
| 第七轮优化 / schema v13 | 付款订单、平台托管、2.5% 服务费、七日结算、投诉退款、72 小时机构退款与运营暂停 | 当前开发与发布基线 |

版本演进不是多套并存系统。当前代码、测试、验收数据和文档共同描述 schema v13 平台，旧版本能力只在仍然有效时继续保留。

## 当前实现（schema v13）

- 访客：无需登录即可浏览启用机构、分院、在售套餐和公开评论；“加入我们”统一展示上海市宝山区上大路99号、021-114514、shucs666@shu.edu.cn，运行时以 `GET /api/public/contact`（后端 `platform_contact.py`）为唯一来源。访客预约先跳转登录，并保留合法的机构、套餐和日期参数。
- 普通用户：首次登录提示填写姓名、性别和出生日期；未完成前只允许只读访问和账号安全操作。完成后可记录日常测量、预约、评论、投诉并管理健康码代预约隐私开关。
- 关联账号：亲友申请被接受后成为双向关联账号。头像菜单可直接切换到关联账号；服务端记录真实操作者 `actor_user_id`、当前有效账号 `subject_user_id` 和完整链路，最多连续三层且禁止循环。
- 多人预约：一次最多五人，参与人来源为本人、关联账号和 10 分钟一次性健康身份码令牌。健康码只授予本次预约能力，不建立亲友关系，也不开放健康数据。
- 付款与托管：预约提交后生成 15 分钟待付款订单并暂占名额；付款成功才确认预约。多人一次付款、逐人形成独立资金明细，金额按两位小数记录。
- 结算与退款：报告正式发布并锁档七个自然日后，订单按 2.5% 平台服务费和 97.5% 机构净额结算；未结投诉暂停结算。退款始终全额原路退给付款人，结算后由机构退回净额、平台冲回服务费。
- 退款治理：投诉与退款合并。已结算订单被判定机构责任后，机构须在准确 72 小时内退款；逾期暂停新预约和运营写操作，完成全部待退款订单后自动恢复。
- 预约与趋势：所有入口按上海时区只允许明天至第 30 天；“我的受检预约”和“代预约回执”分开，卡片展示完整服务时间线。趋势页单列异常提示，支持最近异常、全部异常和“最新已恢复正常”。
- 机构账号：每个分院只有一个有效账号，由管理员创建分院时一并设置用户名、初始密码和邮箱。凭据邮件可重试，发送成功后清除发件箱中的加密临时凭据。机构可在无未结业务时软注销，管理员可恢复。
- 机构履约：报告状态为 `draft → pending_review → published`；上传确认和复核确认分别要求医生姓名，发布与预约履约、事件、通知和临时文件清理在同一事务完成。
- 平台治理：投诉按预约形成追加式消息与事件闭环；评论可隐藏并处以 7 天、30 天或永久禁言，用户每次处罚可申诉一次；机构首页提供分院/整个机构的匿名聚合画像和套餐建议。
- AI/OCR/Agent：OCR 草稿继续要求人工复核；AI 只解释确定性健康事实。Agent 的健康码参与人由第一方安全输入预处理；一次性 bearer 只保留在服务端加密 slot 映射中，模型只见非秘密 slot 与脱敏摘要，工具执行边界才解析 bearer，工具日志只记录字段名。

## 技术栈

| 层级 | 技术 |
|---|---|
| 前端 | Vue 3、Vite 6、Vue Router、Pinia、Element Plus、Axios、ECharts 6 |
| 后端 | Flask 3、Flask-SQLAlchemy、Flask-JWT-Extended、Flask-Cors、bcrypt |
| 数据库 | 本地 SQLite schema v13；GaussDB/openGauss 使用 Alembic v13 增量迁移 |
| 图片处理 | Pillow，服务端解码、重编码并清除 EXIF |
| OCR | 本地 Mock；可选华为云通用表格 OCR |
| AI/Agent/RAG | DeepSeek V4 Flash、LangGraph、Pydantic 工具、SSE、FastEmbed Dense + BM25、Qdrant RRF、本地 FAQ 与测试 Mock |
| 外部 Agent | OAuth 2.1 风格 Authorization Code + PKCE、MCP Streamable HTTP；生产须 HTTPS |
| 本机验收 | Waitress、Vite Preview |
| 测试 | Pytest、Vitest、Vue Test Utils、jsdom |

## 项目结构

```text
health system/
├─ backend/
│  ├─ app/
│  │  ├─ auth/、users/、profile/       # 认证、账号与个人资料
│  │  ├─ health/、exam_reports/        # 自测、时间线、趋势、用户报告
│  │  ├─ org/、admin/                  # 机构与系统管理工作区
│  │  ├─ friends/、comments/、ai/      # 延续的亲友、评论与 AI
│  │  ├─ agent/、oauth/                # 任务图、类型化工具、确认与外部授权
│  │  └─ services/                     # OCR、报告归档、存储与权限服务
│  ├─ instance/health_system.db        # 预置业务 SQLite 快照（Git 跟踪）
│  ├─ report_media/               # 报告媒体母版（处理后 PNG）
│  ├─ uploads/                         # 清单化 demo-v8/v10 验收素材受控跟踪，其余运行时文件忽略
│  ├─ scripts/                         # 数据库升级、同步与运行数据清理脚本
│  ├─ rag_sources/                     # 批准的公共 RAG 来源与黄金查询
│  └─ tests/
├─ frontend/src/
│  ├─ api/、stores/、components/
│  ├─ views/admin/、views/org/
│  └─ views/                           # 公开页面与普通用户页面
├─ scripts/                            # Windows 本地启动/发布脚本
├─ deploy/                             # Apache、systemd 与可回滚服务器发布
├─ local-assets/                       # 本地资料与历史备份（Git 忽略）
└─ 项目文档/
```

`local-assets/` 不参与构建、测试或发布。仓库只跟踪 `backend/instance/health_system.db` 这一份预置业务快照；时间戳数据库备份、RAG 运行目录和上传文件不进入 Git。

## 环境要求与首次安装

- Windows 10/11、PowerShell 5.1+
- Python 3.10+
- Node.js 20+
- npm 10+

在项目根目录执行：

```powershell
Set-Location .\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Set-Location ..\frontend
npm ci

Set-Location ..
if (-not (Test-Path .\backend\.env)) {
    Copy-Item .\backend\.env.example .\backend\.env
}
```

真实 OCR、AI、JWT、SMTP、管理员密码和 `ACCOUNT_CREDENTIAL_ENCRYPTION_KEY` 只允许写入被 Git 忽略的 `backend/.env`。机构凭据密钥必须使用独立高熵随机值，不得与 Agent、JWT 或 SMTP 密钥复用；环境冷备份必须保留它，否则尚未发送的机构初始账密无法解密。

## 本地 SQLite schema v13

正式本地数据库为：

```text
backend/instance/health_system.db
```

新空库首次启动会直接创建 v13。已有旧数据库不会被 `db.create_all()` 半升级；应用会提示执行原子升级脚本。旧 v4—v12 SQLite 可由同一脚本保留升级：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
```

升级脚本会：

1. 校验源 SQLite 完整性；
2. 保留当前系统管理员的主键、用户名、密码哈希及可兼容账号字段；
3. 在同目录临时文件创建完整 v13 结构，执行 v12→v13 资金模型升级并回填旧 `fulfilled` 预约；
4. 验证表、列、约束、外键和 `integrity_check`；
5. 生成带时间戳的升级前完整备份；
6. 原子替换正式数据库。

升级成功条件为 `PRAGMA user_version=13`、`integrity_check=ok` 且外键违规为 0。升级前备份必须保留到人工验收结束。

重建本地验收数据时，先只读检查，再显式确认覆盖业务验收记录。当前统一使用 `reset_v13_demo_data.py`；旧重置入口仅为历史命令兼容包装。完成后必须对显式数据库与上传目录运行 v13 强校验器：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\reset_v13_demo_data.py --check-only
.\.venv\Scripts\python.exe .\scripts\reset_v13_demo_data.py --apply --yes
.\.venv\Scripts\python.exe .\scripts\validate_v13_demo.py `
  --database .\instance\health_system.db --upload-dir .\uploads
```

## 本地启动

开发模式：

```powershell
.\scripts\start-full-dev.ps1
```

- 前端：<http://127.0.0.1:5173>
- 后端：<http://127.0.0.1:5050>
- 健康检查：<http://127.0.0.1:5050/api/health>

一键启动会在后端健康检查通过后，同时启动隐藏的通知 worker；它每 5 秒领取一次 Outbox，自动向机构发送新预约/约满提醒，并向预约人和受检者发送包含掩码身份信息、地点及检查须知的预约确认，同时处理空位提醒。邮件正文会按事件转换为连续、可读的中文说明，不暴露 JSON 字段或内部键名。关闭前端启动命令后，该 worker 会随之停止。仅需单独运行邮件处理时可执行 `./scripts/start-notification-worker.ps1`。

本机生产验收：

```powershell
.\scripts\start-full-prod.ps1
```

- 前端：<http://127.0.0.1:4173>
- 后端：<http://127.0.0.1:5050>

生产验收脚本默认只监听回环地址，并在缺失时向 `backend/.env` 生成随机 JWT 密钥。改为 `0.0.0.0` 或其他对外地址前，必须配置至少 12 字符的 `DEFAULT_ADMIN_PASSWORD`、至少 32 字符的 `JWT_SECRET_KEY`，并限制防火墙范围。

## 三角色入口与验收账号

| 角色 | 登录后入口 | 当前权限 |
|---|---|---|
| 普通用户 `user` | `/dashboard` | 多人预约付款、资金进度、投诉与退款，以及健康档案、关联账号、评论和健康 AI |
| 机构账号 `institution_admin` | `/org/dashboard` | 预约履约、报告上传/复核、收款与退款、投诉处理、经营画像和套餐审核申请 |
| 系统管理员 `admin` | `/admin/dashboard` | 平台财务、投诉裁决、机构暂停治理、分院账号与评论治理；无健康档案内容 |

本地验收数据包括：

- 普通用户：`test1` 至 `test6`，其中 `test6` 专用于未实名认证拦截；
- 机构账号：`institution1_staff1` 至 `institution15_staff1`，每个分院恰好一个有效绑定账号；
- 管理员：环境默认 `admin` 与专用验收管理员 `demo_admin`；
- 22 个专用验收账号统一密码：`Shuhealthdoc！`（末尾为全角感叹号）；
- 环境默认管理员：`admin` 由环境配置负责，不作为人工验收账号记录明文密码

验收快照有 23 个启用可登录账号、5 个机构主体和 15 家分院；`test1` 继续覆盖全部 104 个活动指标和长期趋势。v13 另包含 11 笔付款订单、全部六种资金状态、5 个退款案件、72 小时待退款及逾期暂停场景。精确结果由只读 `validate_v13_demo.py` 输出，完整矩阵见[验收账号与预置数据](项目文档/验收账号与预置数据.md)。

## 当前业务与隐私规则

- 公共注册只创建普通用户；访客可浏览启用机构、分院和已审核在售套餐，但预约写操作必须登录。
- 新用户只填写姓名、性别和出生日期完成一次性实名认证；未完成时，后端统一阻止测量、预约、关联、评论和投诉等业务写操作。管理员可修正身份资料，但不能读取健康档案。
- 关联亲友接受后成为双向关系，可建立最多三层的受控关联会话；账号切换入口位于个人工作台侧栏左下角，可直接切换到链内祖先或其他有效关联账号，无需“返回上一级”。服务端持续记录真实操作者、当前有效账号和完整链，撤销、停用或安全版本变化立即使下游会话失效。
- 多人预约参与人来自当前账号、已关联账号或一次性健康身份码令牌，最多五人。健康身份码匹配只返回脱敏摘要，不建立关联关系；受检者可关闭新代预约能力。
- 全部预约入口以上海时区限制为最早明天、最晚第 30 天；预约卡按创建、到检、上传、待复核、发布和取消节点展示。
- 机构报告采用 `draft → pending_review → published`。上传确认与复核确认分别记录医生姓名及时间；预置验收数据使用上传医生“周明远”和复核医生“许文静”，业务数据保留机构实际填写值。发布事务同时锁档、履约、写事件和通知，之后不可修改或撤回。
- 每个个人预约最多一条投诉，支持机构回复、用户确认或随时升级平台；评论隐藏可附带 7/30 天或永久禁言，处罚仅影响发言并允许一次申诉。
- 趋势页异常提示只汇总当前自然月异常，本月无异常时显示“近期健康状况良好”；机构首页只用匿名聚合数据生成性别、年龄、套餐画像和 AI 建议。
- 体检详情支持当前页面内的健康方向搜索和多选；指标、机构结论、附件及顶部统计随筛选范围同步变化，刷新后恢复全部方向。
- 套餐新增、调整、下架和恢复均生成审核记录；通过后产生或启用正式版本，历史预约继续保留下单时版本事实。
- 趋势以天为粒度：同日有已发布机构指标时优先使用它；报告不含该指标时使用该日最后一次自测。
- 机构 ID 始终从登录账号的数据库绑定取得，前端不能指定其他机构。
- 登录令牌和当前用户按浏览器标签页保存在 `sessionStorage`；同一浏览器的不同标签页可独立登录不同账号，登录或退出不会覆盖其他标签页。
- 机构注销采用软停用，必须先完成未来预约、报告、投诉等全部未结义务；历史预约、报告和投诉保留，管理员可恢复。
- 机构和套餐采用软停用；每家机构最多 8 张 JPEG、PNG 或 WebP 图片，单张不超过 5 MB，排序第一张为封面。
- 用户只有在拥有该机构已发布报告后才能评论；系统管理员隐藏时必须记录原因，可附带发言处罚。
- `/uploads` 只公开数据库登记的机构图片；报告附件必须经业务鉴权访问，孤儿文件和未授权路径不公开。
- Qdrant 只保存批准的公共语料片段及来源元数据，不保存用户 ID、档案 ID、指标值、问题正文或聊天内容。
- 旧 `/api/ai` 对话和分析结果不写入 SQLite；Agent 线程与待确认参数使用 AES-GCM 密文持久化，默认闲置 24 小时清空，工具审计只保存字段名和结果键。浏览器只保存当前 thread ID。
- AI 从工作台顶栏进入；宽屏为独立侧栏，空间不足或移动端切换为焦点可达的遮罩对话框，不再使用自由悬浮球遮挡内容。
- 关怀模式通过字号、控件尺寸和页面断点改善可读性；公开门户和工作台均按真实可用宽度重排，不缩放整张页面。

## AI 与报告识别

离线验收建议：

```env
OCR_USE_MOCK=1
AI_USE_MOCK=1
RAG_ENABLED=0
```

报告识别服务需配置华为云 OCR Endpoint、AK、SK 和 Project ID；AI 服务需配置 `DEEPSEEK_API_KEY`。AI 只结合系统公开机构数据与当前有效账号的档案、历史趋势回答；查看关联账号前必须先建立受控切换会话，健康身份码不会发送给模型。

AI 使用 `meta/status/delta/action/done/error` SSE 事件、持续档案标签、语义自动引用、历史裁剪、速率限制、超时与取消机制。每轮返回 `record_resolution` 和 `next_active_record_context`，前者记录实际引用，后者延续当前对话焦点；上下文排除健康身份码、联系方式、过敏史和既往史。机构推荐直接检索平台数据库且不需要健康档案授权。

报告识别使用 `region-v2` 的多表区域解析、表头语义识别、104 项别名映射、H/L/↑/↓ 提取、冲突/低置信度人工复核及数值/单位安全规则。识别结果生成待复核内容，机构人员确认后归档；临时原文件按生命周期删除。

完整协议见[AI 与 OCR 开发说明](项目文档/AI与OCR开发说明.md)。

## 清理、验证与备份

首次本地启用 RAG 时，在 `backend` 目录执行 `python scripts/rag_sync.py sync`，成功后再设置 `RAG_ENABLED=1`。应用启动和用户请求不会联网更新语料；SSE 只公开检索状态和来源数量，不返回来源正文或 URL。

上传目录孤儿文件脚本默认只预览，确认清单后才使用 `--apply`：

```powershell
.\.venv\Scripts\python.exe .\scripts\cleanup_local_runtime.py
.\.venv\Scripts\python.exe .\scripts\cleanup_local_runtime.py --apply
```

完整本地验证：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
.\.venv\Scripts\python.exe scripts\evaluate_rag.py

Set-Location ..\frontend
npm test
npm run build
npm run test:e2e
npm audit --omit=dev
```

本轮测试、构建、依赖审计、v12 数据强校验、浏览器关键路径和 openGauss 隔离演练已于 2026-08-04 收口，真实统计统一见[测试报告](项目文档/测试报告.md)，README 不重复维护易失效数字。最终 Git SHA 与线上发布结果仍只按真实执行结果回填。

## 备份

停止后端后，至少备份：

- `backend/instance/health_system.db`
- `backend/instance/health_system.before-schema-*.db`
- `backend/instance/health_system.before-demo-v12-*.db`
- `backend/uploads/`
- `backend/.env`（单独加密保存，不进入 Git）

仓库只对 `backend/uploads/` 中清单登记的预置报告附件开放跟踪例外。19 个报告附件按标准槽位归档，文件尺寸、字节数、哈希和机构批注记录在 `backend/report_media_manifest.json`。其余上传和运行时文件仍由 `.gitignore` 排除。

## 服务器状态

仓库包含 Apache、Waitress、API/通知 worker、Agent 清理 timer、可选 MCP systemd 服务、openGauss 和带回滚的发布脚本。每个 release 使用独立 Python venv；发布前连同数据库、上传、环境、Apache、旧 release、systemd unit、通知门闩原状态及服务启用状态建立恢复集并验证冷备 tar 可读。生产通知 worker 先在关闭的 `/var/lib/healthdoc/notification-worker.enabled` 文件门闩后启动；候选 API、真实数据库、同 SHA/schema、静态产物和通知配置预检全部通过后，脚本在 Apache 尚未开放时记录不可回滚的公开提交点，随后启动 Apache 并创建门闩允许领取 Outbox。提交点前失败恢复同一冷备集合，提交点后异常只报告并保留新数据，避免丢弃已确认写入。普通发布执行 v12 增量迁移并保留服务器数据；`-SyncDemoMedia` 只刷新清单素材；仅显式 `-SyncDemoDatabase` 才会在停止写入后导入验收快照。最新线上 release 和灰度状态见[服务器部署与同步](项目文档/服务器部署与同步.md)。

## 文档

- [项目文档索引](项目文档/README.md)：文档职责、阅读顺序和维护规则。
- [项目需求与技术方案](项目文档/项目需求与技术方案.md)：当前完整产品范围、角色边界和技术方案。
- [业务规则与验收契约](项目文档/业务规则与验收契约.md)：schema v13 权限矩阵、资金状态机、迁移和发布门禁。
- [本地运行与验收指南](项目文档/本地运行与验收指南.md)：安装、启动、完整验收、备份和排障。
- [数据库设计说明](项目文档/数据库设计说明.md)与[数据库规范化说明](项目文档/数据库规范化说明.md)：schema v13 物理模型、账本、迁移、约束和范式。
- [AI 与 OCR 开发说明](项目文档/AI与OCR开发说明.md)：保留机制、当前接口、SSE、OCR 流程和数据权限。
- [测试报告](项目文档/测试报告.md)：当前自动化基线和验收矩阵。
- [验收账号与预置数据](项目文档/验收账号与预置数据.md)：全部预置账号、统一密码、数据规模和推荐验收顺序。
- [报告媒体与附件说明](项目文档/报告媒体与附件说明.md)：附件槽位、技术清单、完整性校验和媒体专项发布。
- [服务器部署与同步](项目文档/服务器部署与同步.md)：生产拓扑、schema v13 数据同步、冷备份、回滚和发布验收。

原 2.0 临时计划、3.0 重构方案和实施说明中的有效内容已经按主题合并到以上文档，不再维护版本孤岛。
