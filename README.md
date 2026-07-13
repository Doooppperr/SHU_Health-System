# 康迹 HealthHub：健康档案管理系统

康迹 HealthHub 是一个基于 Flask、Vue 3 和 SQLite 的本地 Web 应用。系统以个人健康档案和指标趋势为核心，提供公开门户、独立登录/注册页，以及普通用户、机构管理员、系统管理员三套隔离工作台。

## 当前实现

- 公开门户：根路由 / 展示项目介绍、核心功能、使用流程、隐私提示和关于我们；登录、注册使用独立品牌化页面。
- 普通用户：进入 /dashboard，可管理健康档案、OCR 报告、指标趋势、亲友授权、机构服务、评论和健康 AI。
- 机构管理员：进入 /org/dashboard，只管理所属机构资料、相册和套餐，并只读查看来源于本机构且已确认的脱敏健康数据。
- 系统管理员：进入 /admin/dashboard，管理机构、套餐、邀请码、用户角色、机构管理员、全局档案和评论审核。
- 档案来源可选：手工录入和 OCR 入档均可选择“暂不选取”机构与套餐；未关联机构不影响指标、趋势和健康 AI。
- 档案采用内部数字主键与用户展示编号分离：界面统一显示 `health+数字`（例如 `health12`）；删除不会重排其余现存档案，编号应视为不保证连续的标识。
- 已创建档案可继续通过 OCR 录入报告；解析结果先暂存，确认前不会覆盖原报告、状态或指标，也可以安全放弃。确认时界面默认用新报告替换旧 OCR 指标，但始终保留手工指标；也可显式选择合并模式。
- 健康 AI 使用 SSE 流式响应；档案列表不会在打开侧栏时加载，只有问题确实需要档案、用户点击“引用档案”或从档案列表发起“智能分析”时才按需加载。
- 单档智能分析覆盖全部指标，多档分析不限制用户可见数量，但必须属于同一人；趋势数值由服务端先确定性计算，再交给模型解释。
- 关怀模式使用统一页面倍率放大文字、控件与间距，桌面端最高为 1.12；可用宽度不足时自动降低倍率或回退到原响应式布局，不再通过分别增大字号和侧栏宽度挤压页面。
- SQLite schema v2：继续使用现有 SQLite，不依赖 MySQL、PostgreSQL 或云数据库。

## 技术栈

| 层级 | 技术 |
|---|---|
| 前端 | Vue 3、Vite 6、Vue Router、Pinia、Element Plus、Axios、ECharts 6 |
| 后端 | Flask 3、Flask-SQLAlchemy、Flask-JWT-Extended、Flask-Cors |
| 数据库 | SQLite，PRAGMA user_version=2 |
| 图片处理 | Pillow，服务端解码、重编码并清除 EXIF |
| OCR | 本地 Mock；可选华为云 OCR API |
| AI | DeepSeek V4 Flash、SSE 流式输出、本地 FAQ/安全分流与测试 Mock |
| 本机演示服务 | Waitress、Vite Preview |
| 测试 | Pytest、Vitest、Vue Test Utils、jsdom |

## 项目结构

~~~text
health system/
├─ .github/workflows/ci.yml          # main/PR 自动化回归
├─ backend/
│  ├─ app/
│  │  ├─ admin/                    # 系统管理员接口
│  │  ├─ org/                      # 机构管理员运营接口
│  │  ├─ institution_health/       # 机构健康数据只读接口
│  │  └─ ...
│  ├─ instance/health_system.db    # 本地 SQLite 正式数据库
│  ├─ scripts/upgrade_local_database.py
│  └─ tests/
├─ frontend/
│  └─ src/
│     ├─ layouts/                  # 三角色工作台布局
│     ├─ views/admin/
│     ├─ views/org/
│     └─ ...
├─ scripts/                        # Windows 本地启动脚本
└─ 项目文档/
~~~

## 环境要求

- Windows 10/11 和 PowerShell 5.1+
- Python 3.10+
- Node.js 20+
- npm 10+

## 首次安装

在项目根目录执行：

~~~powershell
Set-Location .\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Set-Location ..\frontend
npm ci

Set-Location ..
~~~

如需配置真实 OCR、AI 或修改默认管理员，复制环境变量模板：

~~~powershell
if (-not (Test-Path .\backend\.env)) {
    Copy-Item .\backend\.env.example .\backend\.env
}
~~~

真实密钥只允许写入被 Git 忽略的 backend/.env。

## SQLite schema v2

正式数据库仍是：

~~~text
backend/instance/health_system.db
~~~

2026-07-11 的 schema v2 迁移验收基线为：

| 项目 | 迁移验收值 |
|---|---:|
| PRAGMA user_version | 2 |
| users | 2 |
| institutions | 3 |
| packages | 15 |
| health_records | 12 |

`backend/instance/*.db` 已从 Git 跟踪中排除，实际业务行数会随本机演示数据变化；上表只用于说明历史迁移保留结果，不是新克隆仓库的固定数据。

v2 增加三角色约束、机构管理员绑定、机构与套餐停用状态、邀请码和机构相册。新安装会直接创建 v2 空库并执行种子初始化；旧版非空数据库不会被 create_all 半升级，而会提示先执行迁移脚本。

检查或迁移已有数据库：

~~~powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
~~~

迁移会先创建 health_system.before-schema-v2-时间戳.db 备份，在临时库重建并复制数据，验证主键、行数、外键、唯一约束、角色约束和 integrity_check 后再原子替换正式文件。重复运行对已是 v2 的数据库不会再次迁移。

如需改用另一个本地 SQLite 文件，可在 backend/.env 设置：

~~~env
LOCAL_DATABASE_URL=sqlite:///another-local.db
~~~

## 本地启动

开发模式：

~~~powershell
.\scripts\start-full-dev.ps1
~~~

- 前端：http://127.0.0.1:5173
- 后端：http://127.0.0.1:5050
- 健康检查：http://127.0.0.1:5050/api/health

本机生产演示：

~~~powershell
.\scripts\start-full-prod.ps1
~~~

项目生产脚本默认只监听 `127.0.0.1`。本机首次运行时，脚本会自动在被 Git 忽略的 `backend/.env` 生成随机 JWT 密钥，并启用回环地址专用演示模式，因此无需手工配置即可继续使用本地演示账号。若改为 `0.0.0.0` 或其他对外地址，则必须设置至少 12 字符的 `DEFAULT_ADMIN_PASSWORD`；JWT 密钥可由脚本生成，也可显式提供至少 32 字符的安全值。

- 前端：http://127.0.0.1:4173
- 后端：http://127.0.0.1:5050

两个模式均读取同一个本地 SQLite 文件，不需要远程数据库。

如果终端出现 `Production startup requires an explicit JWT_SECRET_KEY`，说明使用的是旧启动脚本或直接调用了 Waitress。关闭失败进程后，从项目根目录重新运行 `.\scripts\start-full-prod.ps1`；新版脚本会在本机回环模式下自动初始化随机密钥。直接调用 Waitress 时则必须手工配置 `.env`。

## 三角色入口与账号

| 角色 | 登录后入口 | 说明 |
|---|---|---|
| 普通用户 user | /dashboard | 可直接注册，不需要邀请码 |
| 机构管理员 institution_admin | /org/dashboard | 由系统管理员签发所属机构邀请码后，在注册页选择工作人员注册并填写邀请码 |
| 系统管理员 admin | /admin/dashboard | 管理全局机构、邀请码、角色、档案和评论 |

开发模式默认本地管理员：

~~~text
用户名：admin
密码：admin123
~~~

该密码只允许用于开发或仅监听回环地址的本机演示，不能用于局域网或公网监听。机构邀请码单次使用、永不过期，数据库只保存哈希，明文仅在签发时显示一次。每个机构最多一个机构管理员；撤销后账号降级为普通用户，个人数据保留。

## 关键业务与隐私规则

- 健康档案的机构和套餐均可为空；选择套餐时会推导所属机构，清空机构时同步清空套餐。
- 只有来源机构明确且状态为 confirmed 的标准化数据，才会向该机构管理员只读开放。
- 机构管理员接口不返回邮箱、手机号、上传人或原始报告，也不能修改、补录或删除用户档案。
- 原始报告通过鉴权接口 /api/records/{id}/file 访问；公共 /uploads 只允许数据库登记的机构图片，孤儿文件和 reports/ 路径均返回 404。
- 机构和套餐采用软停用，保留历史档案来源。
- 每家机构最多上传 8 张 JPEG、PNG 或 WebP 图片；单张不超过 5 MB，排序第一张为封面。
- 普通用户的机构列表和详情页直接展示该封面；无图片或图片加载失败时显示中性占位，不影响机构资料与套餐访问。
- 只有普通用户可以向健康 AI 提交档案 ID；两类管理员工作台不显示健康 AI。
- AI 初始界面不加载档案；每次引用档案都要重新选择并单独同意，选择、同意和健康数据不会自动附加到后续无关消息。
- AI 只接受本人或已授权亲友、状态为 `confirmed` 且至少有一项指标的档案；一次请求中的档案必须属于同一所有者，不设置用户可见数量上限。
- AI 对话和分析结果不写入 SQLite；浏览器只在当前标签页 `sessionStorage` 中保存最多 40 条界面消息，发送给模型的历史最多 20 条并在本地确定性裁剪。
- AI 面板在桌面端打开时按比例缩放主页面，最低缩放比例为 0.7；空间不足或移动端切换为遮罩对话框，不再挤压导航文字。
- 关怀模式与 AI 面板共用同一画布尺寸计算：两者同时开启时会合并倍率并保持主页面恰好落在侧栏之外；公开门户导航项保持单行，窄屏自动回退而不产生横向滚动。

## OCR 与 AI

离线演示建议：

~~~env
OCR_USE_MOCK=1
AI_USE_MOCK=1
~~~

真实 OCR 需配置华为云 Endpoint、AK、SK 和 Project ID。真实 AI 需在 backend/.env 中配置 DEEPSEEK_API_KEY；系统只提供指标科普和一般生活建议，不做诊断、处方或急症替代处理。

OCR 有两种入口：直接上传会创建 `parsed` 档案；从档案列表或详情页进入时携带 `record_id`，只为现有档案创建待确认附件。待确认版本使用随机 `attachment_id`，上传、确认、取消和删除都通过乐观并发控制避免旧页面覆盖新结果或遗留报告文件。

华为通用表格 OCR 的当前解析器版本为 `region-v2`。它按每个表格区域独立解释行列，避免多个表格的行号从 0 重新开始时互相覆盖；同时并行解析表格与非表格文本，因此页眉、双栏资料或无边框检验列表不会阻断真正的指标行。候选映射采用精确别名优先的保守策略，低置信度、重复冲突、无效数值和单位不匹配默认进入人工复核，不会自动确认。

现有档案确认页默认提交 `ocr_update_mode=replace_ocr`：写入本次已确认指标，并只删除没有出现在本报告中的旧 `source=ocr` 指标，`source=manual` 的手工指标不受影响。只有全部页面成功提取且全部候选均经确认时才允许替换；失败页、空结果页、页数截断或旧版完整性信息缺失时自动强制 `merge`。新建档案遇到不完整结果会醒目提示，并要求用户二次确认后才可入档。服务端在未显式传 `ocr_update_mode` 时仍按 `merge` 处理，以兼容旧客户端。

OCR 只会映射当前指标字典已经配置的编码、名称和别名。未知项目会保留在“未作为指标/报告信息字段”中供人工检查，系统不会凭相似字符串猜成其他医疗指标。真实华为云 smoke 使用三份不含真实健康信息的合成报告，分别覆盖中文表格、中文列表和英文行式布局，准确映射 10、9、8 个已配置指标。

AI 主要接口为 `GET /api/ai/records`、`POST /api/ai/chat/stream` 和 `POST /api/ai/analyze/stream`。SSE 事件固定为 `meta`、`status`、`delta`、`action`、`done`、`error`；Waitress 响应不发送 WSGI 禁止的 `Connection` hop-by-hop 头。完整契约见[AI 与 OCR 开发说明](项目文档/AI与OCR开发说明.md)。

## 验证

~~~powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check

Set-Location ..\frontend
npm test
npm run build
npm audit --omit=dev
~~~

当前验证结果：

- 后端：143 passed。
- 前端：19 个测试文件、109 个测试通过。
- 前端生产构建：通过。
- 生产依赖审计：0 vulnerabilities。
- SQLite：user_version=2，外键检查无违规，integrity_check=ok。
- GitHub Actions：使用 Python 3.12 与 Node.js 20，在 push/PR 到 `main` 时重复执行后端测试、前端测试和生产构建。

持续集成定义在 `.github/workflows/ci.yml`，push 到 `main` 或面向 `main` 的 pull request 会触发。CI 使用 Mock，不读取本机 `.env` 或真实外部服务密钥；SQLite 正式库完整性、真实服务 smoke、密钥扫描和 `git diff --check` 仍属于本地发布检查。

## 备份

停止后端后，至少备份：

- backend/instance/health_system.db
- backend/uploads/ 整个目录（机构相册、兼容旧 logo 与原始报告）

迁移脚本产生的 health_system.before-schema-v2-*.db 是升级前自动备份。恢复时应先停止后端，核对目标文件后再替换正式数据库。

## 文档

- [项目需求与技术方案](项目文档/项目需求与技术方案.md)
- [本地运行与演示指南](项目文档/本地运行与演示指南.md)
- [数据库设计说明](项目文档/数据库设计说明.md)
- [数据库规范化说明](项目文档/数据库规范化说明.md)
- [测试报告](项目文档/测试报告.md)
- [AI 与 OCR 开发说明](项目文档/AI与OCR开发说明.md)
- [开发记录与上下文归档](项目文档/开发记录与上下文归档.md)
- [PDF 交付物待更新清单](项目文档/PDF交付物待更新清单.md)
