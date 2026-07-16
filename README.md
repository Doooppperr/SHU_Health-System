# 康康健健 HealthDoc：健康管理、体检预约与机构履约系统

康康健健 HealthDoc 是一个基于 Flask 与 Vue 3 的 B/S 健康管理项目。本地开发默认使用 SQLite schema v6。普通用户维护个人健康资料和日常自测，并在体检前预约机构与已审核套餐；机构确认到检、录入标准化报告并永久归档。项目同时提供机构套餐变更审核、亲友只读授权、评论、OCR、健康 AI、关怀模式和三角色工作台。

## 当前实现

- 公开门户：`/` 展示项目介绍、核心能力、使用流程、隐私提示和关于我们；登录、注册使用独立页面并保留图片验证码。
- 普通用户：进入 `/dashboard`，维护个人资料、录入六类日常测量，在独立“体检预约”页选择机构、已审核套餐和未来 30 天日期；预约创建后立即进入健康时间线，并随未履约、待上传报告、已履约、已失效或已取消状态同步更新。
- 机构账号：进入 `/org/dashboard`，设置每日预约上限，处理未履约、待上传报告、已履约和已失效预约，从已到检预约手工或 OCR 建立报告；套餐全部变更先提交审核。
- 系统管理员：进入 `/admin/dashboard`，管理机构、相册、邀请码、账号状态与评论，并审批机构套餐变更；不能直接修改套餐，也不能读取健康数据。
- 亲友授权：支持关系建立、授权、撤权与删除；获得授权后只读查看对方的时间线、报告和趋势，但不会获得真实姓名、生日、性别、联系方式、健康身份码、过敏史或既往史。
- 机构服务：提供机构列表、详情、套餐、相册封面、停用/恢复和审核后公开评论。
- OCR：机构在草稿阶段上传报告，解析结果必须人工复核；锁定报告时删除临时原文件，用户、亲友和管理员都没有原报告文件入口。
- 健康 AI：使用 SSE 流式响应、公开 FAQ、急症分流、取消/重试、按需引用和逐请求同意；私人健康上下文来自已发布机构报告与每日有效指标，公共科普知识通过 FastEmbed 与 Qdrant Local 检索，私人数据不进入向量库。
- 关怀与响应式界面：保留统一页面倍率、AI 侧栏自适应、移动端遮罩和机构封面失败占位。

## 技术栈

| 层级 | 技术 |
|---|---|
| 前端 | Vue 3、Vite 6、Vue Router、Pinia、Element Plus、Axios、ECharts 6 |
| 后端 | Flask 3、Flask-SQLAlchemy、Flask-JWT-Extended、Flask-Cors、bcrypt |
| 数据库 | 本地 SQLite schema v6；代码保留 GaussDB/openGauss 连接配置 |
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
│  ├─ uploads/                         # 机构图片与草稿临时文件（Git 忽略）
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

## 本地 SQLite schema v6

正式本地数据库为：

```text
backend/instance/health_system.db
```

新空库首次启动会直接创建 v6。已有非 v6 数据库不会被 `db.create_all()` 半升级；应用会提示执行升级脚本。v5→v6 会保留全部原数据并新增预约、套餐审核及报告预约关联：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
```

升级脚本会：

1. 校验源 SQLite 完整性；
2. 保留当前系统管理员的主键、用户名、密码哈希及可兼容账号字段；
3. 在同目录临时文件创建完整 v5 结构；
4. 验证表、列、约束、外键和 `integrity_check`；
5. 生成 `health_system.before-schema-v5-时间戳-随机串.db` 备份；
6. 原子替换正式数据库。

当前本地库已完成迁移，`PRAGMA user_version=6`、`integrity_check=ok`、外键违规为 0。升级前备份仍保留，不能在确认不需要前删除。

## 本地启动

开发模式：

```powershell
.\scripts\start-full-dev.ps1
```

- 前端：<http://127.0.0.1:5173>
- 后端：<http://127.0.0.1:5050>
- 健康检查：<http://127.0.0.1:5050/api/health>

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

演示快照包含 13 个账号、138 条日常测量、11 份已归档机构报告和 71 条标准化报告指标。5 个普通用户都拥有丰富时间线，覆盖同日多次自测、机构指标优先、部分指标回退和亲友只读授权。完整账号矩阵见[测试账号与演示数据](项目文档/测试账号与演示数据.md)。

## 当前业务与隐私规则

- 普通用户注册时由服务端随机生成唯一健康身份码；它不能由前端指定或修改。
- 个人资料包括真实姓名、生日、性别、过敏史和既往史，仅本人资料接口可读写。
- 自测只允许身高、体重、心率、体温、血氧和空腹血糖；同日可保存多次。
- 预约成功后状态为 `未履约`；用户可取消，机构可标记不可恢复的 `已失效`，或确认到检进入 `待上传报告`。
- 只有 `待上传报告` 预约能够建立报告；身份、机构、套餐和日期来自预约快照。报告状态仍为 `draft → locked → published`，发布与预约转为 `已履约`在同一事务完成。
- 已发布报告永久归档，不提供撤回、撤下或删除操作；schema v5 既有报告保留为历史归档。
- 机构每日上限为空表示不限量，只能设置正整数限制；用户可约今天起 30 天，同一用户同一天全平台仅一条有效预约，满额时前端禁用且后端并发复核。
- 套餐新增、修改、下架和恢复均生成审核记录；通过后自动生效，驳回不修改当前套餐，待审申请可撤回重提。
- 趋势以天为粒度：同日有已发布机构指标时优先使用它；报告不含该指标时使用该日最后一次自测。
- 机构 ID 始终从登录账号的数据库绑定取得，前端不能指定其他机构。
- 登录令牌和当前用户按浏览器标签页保存在 `sessionStorage`；同一浏览器的不同标签页可独立登录不同账号，登录或退出不会覆盖其他标签页。
- 机构账号删除后，历史报告保留创建者用户名快照，创建者外键置空；普通用户确认删除后，其健康数据按外键规则级联清理。
- 机构和套餐采用软停用；每家机构最多 8 张 JPEG、PNG 或 WebP 图片，单张不超过 5 MB，排序第一张为封面。
- 用户只有在拥有该机构已归档报告后才能评论；系统管理员负责公开状态审核。
- 亲友授权只允许查看对方的时间线、报告和趋势；任何关系都不能代传报告、代记日常测量或修改对方数据。
- `/uploads` 只公开数据库登记的机构图片。草稿报告文件、孤儿文件和 `reports/` 路径不公开。
- Qdrant 只保存批准的公共语料片段及来源元数据，不保存用户 ID、档案 ID、指标值、问题正文或聊天内容。
- AI 对话和分析结果不写入 SQLite；浏览器只在当前标签页 `sessionStorage` 中保存最多 40 条界面消息，发送给模型的历史最多 20 条并在本地确定性裁剪。
- AI 面板在桌面端打开时按比例缩放主页面，最低缩放比例为 0.7；空间不足或移动端切换为遮罩对话框，不再挤压导航文字。
- 关怀模式与 AI 面板共用同一画布尺寸计算：两者同时开启时会合并倍率并保持主页面恰好落在侧栏之外；公开门户导航项保持单行，窄屏自动回退而不产生横向滚动。

## AI 与 OCR

离线演示建议：

```env
OCR_USE_MOCK=1
AI_USE_MOCK=1
RAG_ENABLED=0
```

真实 OCR 需配置华为云 Endpoint、AK、SK 和 Project ID；真实 AI 需配置 `DEEPSEEK_API_KEY`。系统只提供指标科普、一般生活建议、产品导览和安全分流，不做诊断、处方或急症替代处理。

AI 使用 `meta/status/delta/action/done/error` SSE 事件、逐请求同意、历史裁剪、速率限制、超时与取消机制。可分析对象仅为本人或已授权亲友的已发布机构报告；上下文排除健康身份码、个人资料和联系方式，并使用服务端确定性计算的每日有效序列。

OCR 使用 `region-v2` 的多表区域解析、表格与文本并行、精确别名优先、冲突/低置信度人工复核及数值/单位安全规则。OCR 创建草稿和指标候选，机构人员复核后锁定；临时原文件随锁定删除。

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

当前验收基线：后端 31 项、前端 15 个文件 88 项通过；Vite production build、Python/npm 依赖审计、44 条 RAG 黄金查询、SQLite 完整性/外键与全量快照迁移冒烟均通过。

## 备份

停止后端后，至少备份：

- `backend/instance/health_system.db`
- `backend/instance/health_system.before-schema-v5-*.db`
- `backend/instance/health_system.before-demo-refresh-*.db`
- `backend/uploads/`
- `backend/.env`（单独加密保存，不进入 Git）

## 服务器状态

仓库包含 Apache、Waitress、systemd、openGauss 和带回滚的发布脚本。普通发布保留服务器数据库；显式 `-SyncDemoDatabase` 会先离线备份 openGauss，再全量导入仓库中的合成演示快照并核对逐表行数。开发文档不进入服务器发布包，临时 SQLite 在导入后删除。详见[服务器部署与同步](项目文档/服务器部署与同步.md)。

## 文档

- [项目文档索引](项目文档/README.md)：文档职责、阅读顺序和维护规则。
- [项目需求与技术方案](项目文档/项目需求与技术方案.md)：当前完整产品范围、角色边界和技术方案。
- [本地运行与演示指南](项目文档/本地运行与演示指南.md)：安装、启动、完整演示、备份和排障。
- [数据库设计说明](项目文档/数据库设计说明.md)与[数据库规范化说明](项目文档/数据库规范化说明.md)：schema v6 物理模型、迁移、约束和范式。
- [AI 与 OCR 开发说明](项目文档/AI与OCR开发说明.md)：保留机制、当前接口、SSE、OCR 流程和安全边界。
- [测试报告](项目文档/测试报告.md)：当前自动化基线和验收矩阵。
- [测试账号与演示数据](项目文档/测试账号与演示数据.md)：全部合成账号、统一密码、数据规模和推荐验收顺序。
- [2.0 重构计划](项目文档/2.0重构计划（临时文件，实现后统一整理再删除）.md)：按要求暂时保留，不由本次文档整理删除。
