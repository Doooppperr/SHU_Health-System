# 康康健健 HealthDoc：健康管理、体检预约与机构履约系统

康康健健 HealthDoc 是一个基于 Flask 与 Vue 3 的 B/S 健康服务平台，本地开发默认使用 SQLite schema v7。普通用户通过健康总览、健康时间线、健康数据和趋势管理跨来源资料，可为本人或已授权亲友一次安排最多五人的同行体检；机构围绕接待、到检、健康数据整理和提交开展工作。平台同时提供套餐版本审核、空位提醒、报告识别导入、私有检查附件、健康 AI、关怀模式和三角色工作台。

## 当前实现

- 公开门户：`/` 展示项目介绍、核心能力、使用流程、隐私提示和关于我们；登录、注册使用独立页面并保留图片验证码。注册必须填写通知邮箱，但邮箱不是登录账号键，家庭成员或演示账号可以共用同一收件箱。
- 普通用户：进入 `/dashboard`，通过“记录今日测量”抽屉录入六类日常数据；健康时间线同时呈现一次体检一张旅程卡和按自然日聚合的本人记录。体检预约使用分步引导选择日期、机构、套餐和受检者。
- 机构账号：进入 `/org/dashboard` 查看今日接待、待整理健康数据、套餐审核和容量提醒；从已到检预约手工整理或识别导入健康数据，复核后提交。套餐全部变更先提交审核。
- 系统管理员：进入 `/admin/dashboard`，管理机构、相册、邀请码、账号状态与评论，并审批机构套餐变更；不能直接修改套餐，也不能读取健康数据。
- 亲友授权：支持关系建立、授权、撤权与删除；获得授权后只读查看对方的时间线、报告和趋势，但不会获得真实姓名、生日、性别、联系方式、健康身份码、过敏史或既往史。
- 机构服务：提供机构列表、详情、套餐、相册封面、停用/恢复和审核后公开评论。
- 报告识别：机构可将已有体检报告导入为待复核健康数据；识别结果必须人工确认，锁定时删除临时原文件，用户、亲友和管理员都没有原文件入口。
- 健康 AI：使用 SSE 流式响应、公开 FAQ、急症分流、取消/重试、按需引用和逐请求同意；私人健康上下文来自已发布机构报告与每日有效指标，公共科普知识通过 FastEmbed 与 Qdrant Local 检索，私人数据不进入向量库。
- 关怀与响应式界面：AI 从工作台顶栏进入，宽屏使用侧栏、窄屏使用遮罩；主内容按真实可用宽度响应式重排，不通过缩放整页规避布局问题。

## 技术栈

| 层级 | 技术 |
|---|---|
| 前端 | Vue 3、Vite 6、Vue Router、Pinia、Element Plus、Axios、ECharts 6 |
| 后端 | Flask 3、Flask-SQLAlchemy、Flask-JWT-Extended、Flask-Cors、bcrypt |
| 数据库 | 本地 SQLite schema v7；GaussDB/openGauss 使用 Alembic v7 迁移 |
| 图片处理 | Pillow，服务端解码、重编码并清除 EXIF |
| OCR | 本地 Mock；可选华为云通用表格 OCR |
| AI/RAG | DeepSeek V4 Flash、SSE、FastEmbed `BAAI/bge-small-zh-v1.5`、Qdrant Local、本地 FAQ/安全分流与测试 Mock |
| 本机演示 | Waitress、Vite Preview |
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
│  ├─ instance/health_system.db        # 合成演示 SQLite 快照（Git 跟踪）
│  ├─ uploads/                         # 9 个 demo-v7 水印素材受控跟踪，其余运行时文件忽略
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

`local-assets/` 不参与构建、测试或发布。仓库只跟踪 `backend/instance/health_system.db` 这一份合成演示快照；时间戳数据库备份、RAG 运行目录和上传文件不进入 Git。

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

## 本地 SQLite schema v7

正式本地数据库为：

```text
backend/instance/health_system.db
```

新空库首次启动会直接创建 v7。已有非 v7 数据库不会被 `db.create_all()` 半升级；应用会提示执行升级脚本。v6→v7 会完整复制既有业务数据，并回填健康领域、套餐版本、预约组、履约事件和指标来源快照：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
```

升级脚本会：

1. 校验源 SQLite 完整性；
2. 保留当前系统管理员的主键、用户名、密码哈希及可兼容账号字段；
3. 在同目录临时文件创建完整 v7 结构；
4. 验证表、列、约束、外键和 `integrity_check`；
5. 生成带时间戳的升级前完整备份；
6. 原子替换正式数据库。

当前本地库为 `PRAGMA user_version=7`，`integrity_check=ok`、外键违规为 0。升级前备份仍需保留到人工验收结束。

重建本地演示数据时，先只读检查，再显式确认覆盖业务演示记录。脚本保留 13 个演示账号及密码哈希：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\reset_v7_demo_data.py --check-only
.\.venv\Scripts\python.exe .\scripts\reset_v7_demo_data.py --apply --yes
```

## 本地启动

开发模式：

```powershell
.\scripts\start-full-dev.ps1
```

- 前端：<http://127.0.0.1:5173>
- 后端：<http://127.0.0.1:5050>
- 健康检查：<http://127.0.0.1:5050/api/health>

一键启动会在后端健康检查通过后，同时启动隐藏的通知 worker；它每 5 秒领取一次 Outbox，自动发送预约、约满和空位提醒。邮件正文会按事件转换为连续、可读的中文说明，不暴露 JSON 字段或内部键名。关闭前端启动命令后，该 worker 会随之停止。仅需单独运行邮件处理时可执行 `./scripts/start-notification-worker.ps1`。

本机生产演示：

```powershell
.\scripts\start-full-prod.ps1
```

- 前端：<http://127.0.0.1:4173>
- 后端：<http://127.0.0.1:5050>

生产演示脚本默认只监听回环地址，并在缺失时向 `backend/.env` 生成随机 JWT 密钥。改为 `0.0.0.0` 或其他对外地址前，必须配置至少 12 字符的 `DEFAULT_ADMIN_PASSWORD`、至少 32 字符的 `JWT_SECRET_KEY`，并限制防火墙范围。

## 三角色入口与演示账号

| 角色 | 登录后入口 | 当前权限 |
|---|---|---|
| 普通用户 `user` | `/dashboard` | 个人资料、自测、时间线、趋势、自动归档报告、亲友只读查看、机构、评论和健康 AI |
| 机构账号 `institution_admin` | `/org/dashboard` | 所属机构、相册、预约履约、报告生产和套餐审核申请 |
| 系统管理员 `admin` | `/admin/dashboard` | 机构、邀请码、账号和评论；无健康内容 |

本地演示数据包括：

- 普通用户：`test1` 至 `test5`
- 机构账号：`institution1_staff1/2`、`institution2_staff1/2`、`institution3_staff1/2`
- 专用演示管理员：`demo_admin`
- 上述 12 个专用演示账号统一密码：`Shuhealthdoc！`（末尾为全角感叹号）
- 环境默认管理员：`admin` 由环境配置负责，不作为人工演示账号记录明文密码

演示快照包含 13 个账号、3 家机构、9 个套餐、10 个套餐版本、15 个预约组、17 条预约、9 份已归档健康数据、70 条日常测量、3 条候补提醒、9 条文字结论、3 个私有附件和 3 条评价。5 个普通用户分别承担不同现实生活场景，登录后即可看到第三轮重构后的总览、时间线、健康资料、预约和机构服务。完整账号矩阵见[测试账号与演示数据](项目文档/测试账号与演示数据.md)。

## 当前业务与隐私规则

- 普通用户注册时由服务端随机生成唯一健康身份码；它不能由前端指定或修改。
- 个人资料包括真实姓名、生日、性别、过敏史和既往史，仅本人资料接口可读写。
- 自测只允许身高、体重、心率、体温、血氧和空腹血糖；同日可保存多次。
- 用户可为本人或同行家人建立体检安排；服务端统一校验参与者、适用人群、日期冲突和机构名额，任一人不满足条件时整组不创建。
- 预约进度按创建、取消、到检、报告归档等现实服务节点更新；满额时可订阅空位提醒，释放名额后由通知 outbox 触发可追踪投递。
- 机构从到检任务进入报告录入与复核，身份、机构、套餐版本和日期来自预约。归档与预约转为已履约同步完成，归档内容永久只读。
- 套餐新增、调整、下架和恢复均生成审核记录；通过后产生或启用正式版本，历史预约继续保留下单时版本事实。
- 趋势以天为粒度：同日有已发布机构指标时优先使用它；报告不含该指标时使用该日最后一次自测。
- 机构 ID 始终从登录账号的数据库绑定取得，前端不能指定其他机构。
- 登录令牌和当前用户按浏览器标签页保存在 `sessionStorage`；同一浏览器的不同标签页可独立登录不同账号，登录或退出不会覆盖其他标签页。
- 机构账号删除后，历史报告保留创建者用户名快照，创建者外键置空；普通用户确认删除后，其健康数据按外键规则级联清理。
- 机构和套餐采用软停用；每家机构最多 8 张 JPEG、PNG 或 WebP 图片，单张不超过 5 MB，排序第一张为封面。
- 用户只有在拥有该机构已归档报告后才能评论；系统管理员负责公开状态审核。
- 亲友授权只允许查看对方的脱敏时间线和趋势；任何关系都不能代记自主测量、编辑报告或修改对方数据。
- `/uploads` 只公开数据库登记的机构图片；报告附件必须经业务鉴权访问，孤儿文件和未授权路径不公开。
- Qdrant 只保存批准的公共语料片段及来源元数据，不保存用户 ID、档案 ID、指标值、问题正文或聊天内容。
- AI 对话和分析结果不写入 SQLite；浏览器只在当前标签页 `sessionStorage` 中保存最多 40 条界面消息，发送给模型的历史最多 20 条并在本地确定性裁剪。
- AI 从工作台顶栏进入；宽屏为独立侧栏，空间不足或移动端切换为焦点可达的遮罩对话框，不再使用自由悬浮球遮挡内容。
- 关怀模式通过字号、控件尺寸和页面断点改善可读性；公开门户和工作台均按真实可用宽度重排，不缩放整张页面。

## AI 与报告识别

离线演示建议：

```env
OCR_USE_MOCK=1
AI_USE_MOCK=1
RAG_ENABLED=0
```

真实报告识别服务需配置华为云 OCR Endpoint、AK、SK 和 Project ID；真实 AI 需配置 `DEEPSEEK_API_KEY`。系统只提供指标科普、一般生活建议、产品导览和安全分流，不做诊断、处方或急症替代处理。

AI 使用 `meta/status/delta/action/done/error` SSE 事件、逐请求同意、历史裁剪、速率限制、超时与取消机制。可分析对象仅为本人或已授权亲友的已发布机构报告；上下文排除健康身份码、个人资料和联系方式，并使用服务端确定性计算的每日有效序列。

报告识别使用 `region-v2` 的多表区域解析、表格与文本并行、精确别名优先、冲突/低置信度人工复核及数值/单位安全规则。识别结果生成待复核内容，机构人员确认后归档；临时原文件按生命周期删除。

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

当前验收基线：后端 52 项、前端 100 项测试通过，Vite production build 通过；SQLite/openGauss v7 结构、演示数据预检与覆盖流程已验证。

## 备份

停止后端后，至少备份：

- `backend/instance/health_system.db`
- `backend/instance/health_system.before-schema-*.db`
- `backend/instance/health_system.before-demo-refresh-*.db`
- `backend/uploads/`
- `backend/.env`（单独加密保存，不进入 Git）

仓库只对 `backend/uploads/` 中 9 个带 `demo-v7` 水印的 PNG 开放跟踪例外，其中 3 个是私有健康附件、6 个是机构展示图，用于保证 fresh clone 与演示数据库引用一致；其余上传和运行时文件仍由 `.gitignore` 排除。

## 服务器状态

仓库包含 Apache、Waitress、API/通知 worker 双 systemd 服务、openGauss 和带回滚的发布脚本。普通发布保留服务器数据库；显式 `-SyncDemoDatabase` 会先离线备份 openGauss 与上传目录，再全量导入仓库中的合成演示快照及 9 个水印附件并核对完整性。`-SyncMailSettings` 可经 SSH 临时文件把本地 SMTP 参数写入服务器 root-only 环境，临时文件随发布清除且不进入 Git。开发文档不进入服务器运行目录，但完整保留在 GitHub。详见[服务器部署与同步](项目文档/服务器部署与同步.md)。

## 文档

- [项目文档索引](项目文档/README.md)：文档职责、阅读顺序和维护规则。
- [项目需求与技术方案](项目文档/项目需求与技术方案.md)：当前完整产品范围、角色边界和技术方案。
- [本地运行与演示指南](项目文档/本地运行与演示指南.md)：安装、启动、完整演示、备份和排障。
- [数据库设计说明](项目文档/数据库设计说明.md)与[数据库规范化说明](项目文档/数据库规范化说明.md)：schema v7 物理模型、迁移、约束和范式。
- [AI 与 OCR 开发说明](项目文档/AI与OCR开发说明.md)：保留机制、当前接口、SSE、OCR 流程和安全边界。
- [测试报告](项目文档/测试报告.md)：当前自动化基线和验收矩阵。
- [测试账号与演示数据](项目文档/测试账号与演示数据.md)：全部合成账号、统一密码、数据规模和推荐验收顺序。
- [2.0 重构计划](项目文档/2.0重构计划（临时文件，实现后统一整理再删除）.md)：按要求暂时保留，不由本次文档整理删除。
