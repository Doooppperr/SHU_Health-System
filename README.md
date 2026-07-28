# 康康健健 HealthDoc：健康管理、体检预约与机构履约系统

康康健健 HealthDoc 是一个基于 Flask 与 Vue 3 的 B/S 健康服务平台，本地开发默认使用 SQLite schema v10。普通用户通过健康总览、健康时间线、体检数据和趋势管理跨来源资料，可使用健康身份码授权亲友并为本人或亲友预约；机构分院围绕接待、预约快照、规范附件和报告交付开展工作。平台同时提供站内/邮件通知、约 100 项成人体检指标、OCR 复核、系统数据优先的健康 AI、管理员账号治理和三角色工作台。

## 1.0—3.0 演进概览

| 阶段 | 产品重点 | 当前状态 |
|---|---|---|
| 1.0 | 三角色、机构与套餐浏览、基础预约和健康记录 | 基础账号、角色和服务入口继续保留 |
| 2.0 | 自主测量、时间线、趋势、亲友授权、机构报告履约和 AI/OCR | 已融入当前健康中心与机构任务流 |
| 3.0 | 平台化 UI、健康领域、套餐版本、多人预约、候补通知、图文报告和分院协作 | 历史数据库基线为 schema v8 |
| 4.0 | 账号安全、全站中文化、跨来源趋势、机构评价回复和套餐详情 | 历史数据库基线为 schema v9 |
| 5.0 | 健康身份码亲友授权、预约预约资料副本、规范附件、104 项指标、系统数据 AI、全站分页与通知中心 | 当前运行版本；数据库基线为 schema v10 |

版本演进不是三套并存系统。当前代码、测试、验收数据和文档共同描述一套融合后的 3.0 平台，旧版本能力只在仍然有效时继续保留。

## 当前实现（5.0/schema v10）

- 公开门户：`/` 展示项目介绍、核心能力、使用流程、隐私提示和关于我们；登录、注册使用独立页面并保留图片验证码。注册必须填写通知邮箱，但邮箱不是登录账号键，家庭成员或验收账号可以共用同一收件箱。
- 普通用户：进入 `/dashboard`，通过“记录今日测量”抽屉录入六类日常数据；健康时间线同时呈现一次体检一张旅程卡和按自然日聚合的本人记录。健康趋势直接标注每个数据点；只有具备可靠上下限的指标才展示简洁“参考范围”卡，身高、体重、腰围等描述性项目不展示判定提示。体检预约使用分步引导选择日期、机构、套餐和受检者，预约历史支持日期筛选并固定每页 10 组。
- 机构账号：仍绑定具体分院，进入 `/org/dashboard` 查看本院今日接待、待整理体检数据、套餐审核和容量提醒；“机构共享档案”只读展示同主体兄弟分院的已归档报告，草稿、预约、容量、候补和套餐不跨院共享。
- 系统管理员：进入 `/admin/dashboard`，按“创建机构主体—添加分院—配置分院—签发分院邀请码”治理机构结构，并管理账号、评论和套餐审核；不能读取体检报告正文或附件。
- 亲友授权：只允许使用健康身份码建立关系；待授权显示脱敏姓名，授权后在亲友、预约、报告、趋势和 AI 中统一使用真实姓名。代预约人只填写亲友身高、体重，病史由服务端生成只交付预约机构的预约资料副本。
- 机构服务：提供机构主体/分院模糊搜索、分组列表、详情、套餐、相册封面、停用/恢复和审核后公开评论；目录可按机构名、分院名、地区、地址或交通信息筛选，清空搜索恢复全部启用机构。
- 报告识别：机构按标准附件槽上传心电图、超声、胸片/CT 等检查材料并填写批注；OCR 同时识别项目、值、单位、参考范围和异常标志，冲突、歧义、单位不兼容及低置信度必须人工复核。结果判定按“机构原始范围→性别/年龄规则→通用范围”执行；没有可靠规则时不展示状态标签，身高、体重、臀围等描述性测量值不单独判定正常或异常。
- 健康 AI：使用 SSE 流式响应、系统机构数据优先检索、会话级自动引用和手动引用；机构推荐只使用平台内启用机构、分院、套餐和可约数据，无匹配时明确说明。个人数据遵循最少必要与单成员原则。
- 关怀与响应式界面：AI 从工作台顶栏进入，宽屏使用侧栏、窄屏使用遮罩；主内容按真实可用宽度响应式重排，不通过缩放整页规避布局问题。

## 技术栈

| 层级 | 技术 |
|---|---|
| 前端 | Vue 3、Vite 6、Vue Router、Pinia、Element Plus、Axios、ECharts 6 |
| 后端 | Flask 3、Flask-SQLAlchemy、Flask-JWT-Extended、Flask-Cors、bcrypt |
| 数据库 | 本地 SQLite schema v10；GaussDB/openGauss 使用 Alembic v10 增量迁移 |
| 图片处理 | Pillow，服务端解码、重编码并清除 EXIF |
| OCR | 本地 Mock；可选华为云通用表格 OCR |
| AI/RAG | DeepSeek V4 Flash、SSE、FastEmbed `BAAI/bge-small-zh-v1.5`、Qdrant Local、本地 FAQ 与测试 Mock |
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

真实 OCR、AI、JWT 密钥和管理员密码只允许写入被 Git 忽略的 `backend/.env`。

## 本地 SQLite schema v10

正式本地数据库为：

```text
backend/instance/health_system.db
```

新空库首次启动会直接创建 v10。已有旧数据库不会被 `db.create_all()` 半升级；应用会提示执行原子升级脚本。v9→v10 增加通知、预约快照与取消责任、标准附件槽、参考规则和异常方向；旧 v4–v9 SQLite 可由同一脚本逐级保留升级：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
```

升级脚本会：

1. 校验源 SQLite 完整性；
2. 保留当前系统管理员的主键、用户名、密码哈希及可兼容账号字段；
3. 在同目录临时文件创建完整 v10 结构；
4. 验证表、列、约束、外键和 `integrity_check`；
5. 生成带时间戳的升级前完整备份；
6. 原子替换正式数据库。

当前本地库为 `PRAGMA user_version=10`，`integrity_check=ok`、外键违规为 0。升级前备份仍需保留到人工验收结束。

重建本地验收数据时，先只读检查，再显式确认覆盖业务验收记录。脚本逐字段保留原 13 个账号及密码哈希，并补齐 12 个新增分院账号：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\reset_v10_demo_data.py --check-only
.\.venv\Scripts\python.exe .\scripts\reset_v10_demo_data.py --apply --yes
.\.venv\Scripts\python.exe .\scripts\validate_v10_demo.py
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
| 普通用户 `user` | `/dashboard` | 个人资料、自测、时间线、趋势、自动归档报告、亲友只读查看、机构、评论和健康 AI |
| 机构账号 `institution_admin` | `/org/dashboard` | 所属机构、相册、预约履约、报告生产和套餐审核申请 |
| 系统管理员 `admin` | `/admin/dashboard` | 机构、邀请码、账号和评论；无健康内容 |

本地验收数据包括：

- 普通用户：`test1` 至 `test5`
- 机构账号：`institution1_staff1/2`、`institution2_staff1/2`、`institution3_staff1/2`
- 专用验收管理员：`demo_admin`
- 上述 12 个专用验收账号统一密码：`Shuhealthdoc！`（末尾为全角感叹号）
- 环境默认管理员：`admin` 由环境配置负责，不作为人工验收账号记录明文密码

验收快照包含 25 个账号、5 个机构主体、15 家分院、25 个套餐、40 个预约组、56 位预约参与者及 66 份已归档体检报告。全部 191 个“报告—健康方向”组合均有机构医生审核结论。`test1` 保持 28 份报告，对全部 104 个活动指标均至少有 9 条，核心趋势指标均有 16 条，覆盖 1458 天（5 次综合体检、11 次专项复查），并包含正常、偏高、偏低、阳性/阴性、OCR/手工、多机构、同日优先级和规范附件批注。完整账号矩阵见[验收账号与预置数据](项目文档/验收账号与预置数据.md)。

## 当前业务与隐私规则

- 普通用户注册时由服务端随机生成唯一健康身份码；它不能由前端指定或修改。
- 个人资料包括真实姓名、生日、性别、过敏史和既往史，仅本人资料接口可读写。
- 自测只允许身高、体重、心率、体温、血氧和空腹血糖；同日可保存多次。
- 用户可为本人或同行家人建立体检安排；服务端统一校验参与者、适用人群、日期冲突和机构名额，任一人不满足条件时整组不创建。
- 预约进度按创建、取消、到检、报告归档等现实服务节点更新；满额时可订阅空位提醒，释放名额后由通知 outbox 触发可追踪投递。
- 机构从到检任务进入报告录入与复核，身份、机构、套餐版本和日期来自预约。归档与预约转为已履约同步完成，归档内容永久只读。
- 报告归档前必须为实际包含指标或附件的每个健康方向填写机构结论；缺失时接口返回具体方向并阻止归档。
- 体检详情支持当前页面内的健康方向搜索和多选；指标、机构结论、附件及顶部统计随筛选范围同步变化，刷新后恢复全部方向。
- 套餐新增、调整、下架和恢复均生成审核记录；通过后产生或启用正式版本，历史预约继续保留下单时版本事实。
- 趋势以天为粒度：同日有已发布机构指标时优先使用它；报告不含该指标时使用该日最后一次自测。
- 机构 ID 始终从登录账号的数据库绑定取得，前端不能指定其他机构。
- 登录令牌和当前用户按浏览器标签页保存在 `sessionStorage`；同一浏览器的不同标签页可独立登录不同账号，登录或退出不会覆盖其他标签页。
- 机构账号删除后，历史报告保留创建者用户名快照，创建者外键置空；普通用户确认删除后，其健康数据按外键规则级联清理。
- 机构和套餐采用软停用；每家机构最多 8 张 JPEG、PNG 或 WebP 图片，单张不超过 5 MB，排序第一张为封面。
- 用户只有在拥有该机构已归档报告后才能评论；系统管理员负责公开状态审核。
- 亲友关系只通过健康身份码建立；授权后展示真实姓名但不暴露用户名。任何关系都不能代记自主测量、编辑报告或修改对方资料。
- `/uploads` 只公开数据库登记的机构图片；报告附件必须经业务鉴权访问，孤儿文件和未授权路径不公开。
- Qdrant 只保存批准的公共语料片段及来源元数据，不保存用户 ID、档案 ID、指标值、问题正文或聊天内容。
- AI 对话和分析结果不写入 SQLite；浏览器只在当前标签页 `sessionStorage` 中保存最多 40 条界面消息及当前对话的 `active_record_context`，发送给模型的历史最多 20 条并在本地确定性裁剪。档案引用是对话级持续上下文，不会在单条消息后自动清空。
- AI 从工作台顶栏进入；宽屏为独立侧栏，空间不足或移动端切换为焦点可达的遮罩对话框，不再使用自由悬浮球遮挡内容。
- 关怀模式通过字号、控件尺寸和页面断点改善可读性；公开门户和工作台均按真实可用宽度重排，不缩放整张页面。

## AI 与报告识别

离线验收建议：

```env
OCR_USE_MOCK=1
AI_USE_MOCK=1
RAG_ENABLED=0
```

报告识别服务需配置华为云 OCR Endpoint、AK、SK 和 Project ID；AI 服务需配置 `DEEPSEEK_API_KEY`。AI 可结合系统机构数据、当前成员档案和历史趋势直接回答，并通过会话级上下文保持连续追问。

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
npm audit --omit=dev
```

当前验收基线：后端 95 项、前端 130 项测试通过，Vite production build 通过，Python 生产依赖清单与前端生产依赖审计均无已知漏洞；SQLite schema v10、v9→v10 原子升级、openGauss v10 迁移、预置业务数据预检与覆盖流程均已验证。

## 备份

停止后端后，至少备份：

- `backend/instance/health_system.db`
- `backend/instance/health_system.before-schema-*.db`
- `backend/instance/health_system.before-demo-refresh-*.db`
- `backend/uploads/`
- `backend/.env`（单独加密保存，不进入 Git）

仓库只对 `backend/uploads/` 中清单登记的预置报告附件开放跟踪例外。19 个报告附件按标准槽位归档，文件尺寸、字节数、哈希和机构批注记录在 `backend/report_media_manifest.json`。其余上传和运行时文件仍由 `.gitignore` 排除。

## 服务器状态

仓库包含 Apache、Waitress、API/通知 worker 双 systemd 服务、openGauss 和带回滚的发布脚本。普通发布执行 v10 增量迁移并保留服务器数据；`-SyncDemoMedia` 只刷新清单素材；仅显式 `-SyncDemoDatabase` 才会在完整备份后导入新版验收快照。本轮发布保留服务器 root-only SMTP 配置，不从本机同步 `.env`。详见[服务器部署与同步](项目文档/服务器部署与同步.md)。

## 文档

- [项目文档索引](项目文档/README.md)：文档职责、阅读顺序和维护规则。
- [项目需求与技术方案](项目文档/项目需求与技术方案.md)：当前完整产品范围、角色边界和技术方案。
- [本地运行与验收指南](项目文档/本地运行与验收指南.md)：安装、启动、完整验收、备份和排障。
- [数据库设计说明](项目文档/数据库设计说明.md)与[数据库规范化说明](项目文档/数据库规范化说明.md)：schema v10 物理模型、迁移、约束和范式。
- [AI 与 OCR 开发说明](项目文档/AI与OCR开发说明.md)：保留机制、当前接口、SSE、OCR 流程和数据权限。
- [测试报告](项目文档/测试报告.md)：当前自动化基线和验收矩阵。
- [验收账号与预置数据](项目文档/验收账号与预置数据.md)：全部预置账号、统一密码、数据规模和推荐验收顺序。
- [报告媒体与附件说明](项目文档/报告媒体与附件说明.md)：附件槽位、技术清单、完整性校验和媒体专项发布。
- [服务器部署与同步](项目文档/服务器部署与同步.md)：生产拓扑、schema v10 数据同步、备份、回滚和发布验收。

原 2.0 临时计划、3.0 重构方案和实施说明中的有效内容已经按主题合并到以上文档，不再维护版本孤岛。
