# 体检评价与健康档案系统（Health System）

基于 `Flask + Vue3` 的课程项目，覆盖“体检机构浏览 -> 档案录入（手动/OCR）-> 亲友授权代传 -> 趋势分析 -> 评论审核”完整闭环。

## 1. 核心能力

- 用户注册、登录、图片验证码、JWT 鉴权与刷新。
- 体检机构与套餐浏览。
- 健康档案管理：手动录入指标 + OCR 上传解析 + 确认入档。
- 亲友关系管理：添加关系、授权开关、代传档案。
- 指标趋势分析：按归属人 + 指标查看历史曲线和统计摘要。
- 评论系统：用户提交评论、管理员审核可见性、前台展示。
- 管理员用户管理：用户列表、修改、删除。

## 2. 技术栈（含数据栈）

| 层 | 技术 |
|---|---|
| 前端 | Vue 3、Vite、Vue Router、Pinia、Element Plus、Axios、ECharts |
| 后端 | Flask、Flask-SQLAlchemy、Flask-JWT-Extended、Flask-Migrate、Flask-Cors |
| 数据库 | 华为云 GaussDB（openGauss 生态，当前运行库）；SQLite 仅用于自动化测试和历史迁移源 |
| OCR | 华为云 OCR（支持 Mock 与真实模式切换） |
| 生产服务 | Waitress（后端）、Vite Preview（前端演示） |
| 自动化测试 | Pytest（后端） |

## 3. 项目架构

```text
health system/
├─ backend/                    # Flask 后端
│  ├─ app/
│  │  ├─ auth/users/friends/.../routes.py
│  │  ├─ models/               # SQLAlchemy 数据模型
│  │  ├─ services/             # OCR 与文件存储服务
│  │  ├─ config.py             # 配置与环境变量映射
│  │  ├─ seed.py               # 种子数据与默认管理员初始化
│  │  └─ __init__.py           # app factory + blueprint 注册
│  ├─ tests/                   # 后端单元测试
│  ├─ .env.example             # 环境变量模板
│  ├─ run.py                   # 开发启动入口
│  └─ wsgi.py                  # 生产启动入口
├─ frontend/                   # Vue 前端
│  ├─ src/api/                 # 按业务拆分的接口层
│  ├─ src/views/               # 页面视图
│  ├─ src/stores/              # Pinia 状态管理
│  ├─ src/router/index.js      # 路由与鉴权守卫
│  └─ vite.config.js           # 代理与打包分包配置
├─ scripts/                    # PowerShell 一键启动脚本
└─ 项目文档/                     # 开发记录、测试报告、演示脚本
```

## 4. 数据模型与关系

核心表：`users`、`friend_relations`、`institutions`、`packages`、`indicator_categories`、`indicator_dicts`、`health_records`、`health_indicators`、`comments`。

关键关系：

- `health_records.owner_id`：档案归属人；`uploader_id`：上传人（支持代传）。
- `health_indicators` 通过 `(record_id, indicator_dict_id)` 唯一约束避免重复指标。
- `institutions(name, branch_name)`、`packages(institution_id, name)`、`indicator_dicts(category_id, name)` 作为候选键约束，强化 3NF/BCNF 规范化。
- `friend_relations.auth_status=true` 才允许亲友代传与查看趋势。
- 评论提交依赖“用户已上传该机构档案”门槛，`comments.is_visible` 由管理员审核控制。

## 5. 配置文件说明

| 文件 | 作用 |
|---|---|
| `backend/.env` | 本地运行环境变量（由 `.env.example` 复制） |
| `backend/app/config.py` | 默认配置、开发/测试/生产配置类 |
| `frontend/vite.config.js` | 前端端口、`/api` 代理、构建分包策略 |
| `backend/requirements.txt` | 后端 Python 依赖 |
| `frontend/package.json` | 前端 npm 依赖与脚本 |
| `scripts/*.ps1` | 开发/生产一键启动脚本 |
| `.gitignore` | 忽略虚拟环境、node_modules、上传目录、本地 `.env` 与本地数据库备份文件 |

## 6. 环境变量（`backend/.env`）

先复制：

```powershell
Copy-Item .\backend\.env.example .\backend\.env
```

主要变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | 无，必须配置 | 数据库连接串；当前云库示例为 `opengauss+psycopg2://health_app:<password>@127.0.0.1:15432/health_system?client_encoding=utf8` |
| `TARGET_DATABASE_URL` | 同 `DATABASE_URL` | 数据迁移脚本目标库连接串 |
| `JWT_SECRET_KEY` | 开发默认值 | JWT 签名密钥，生产必须替换 |
| `CAPTCHA_TTL_SECONDS` | `300` | 登录/注册图片验证码有效期秒数 |
| `OCR_PROVIDER` | `huawei` | OCR 供应商标识 |
| `OCR_USE_MOCK` | `1` | `1` 用 Mock，`0` 用真实华为云 OCR |
| `HUAWEI_OCR_ENDPOINT` | 空 | 华为云 OCR Endpoint |
| `HUAWEI_OCR_AK` / `HUAWEI_OCR_SK` | 空 | 华为云 AK/SK |
| `HUAWEI_PROJECT_ID` | 空 | 华为云项目 ID |
| `OCR_API_PATH` | `/v2/{project_id}/ocr/general-table` | OCR API 路径模板 |
| `OCR_PDF_MAX_PAGES` | `8` | PDF 最多解析页数 |
| `OCR_AUTO_CONFIRM_MIN_SCORE` | `0.92` | OCR 自动确认阈值 |
| `UPLOAD_DIR` | `backend/uploads` | 上传文件目录（运行目录相关） |
| `UPLOAD_URL_BASE` | `/uploads` | 上传文件访问前缀 |
| `DB_POOL_SIZE` | `10` | SQLAlchemy 数据库连接池基础连接数 |
| `DB_MAX_OVERFLOW` | `20` | 连接池临时溢出连接数 |
| `DB_POOL_TIMEOUT` | `30` | 获取数据库连接的等待秒数 |
| `DB_POOL_RECYCLE` | `1800` | 连接回收秒数，降低长连接失效风险 |
| `DEFAULT_ADMIN_USERNAME` | `admin` | 默认管理员用户名 |
| `DEFAULT_ADMIN_PASSWORD` | `admin123` | 默认管理员密码（生产务必修改） |
| `DEFAULT_ADMIN_EMAIL` | `admin@example.com` | 默认管理员邮箱 |

## 7. 运行环境要求

- Windows + PowerShell（项目脚本基于 `.ps1`）。
- Python `3.10+`（后端使用现代类型标注语法）。
- Node.js `18+`、npm `9+`（前端 Vite 6）。

## 8. 本地安装与启动

### 8.1 首次安装

```powershell
# 1) 后端依赖
Set-Location .\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2) 前端依赖
Set-Location ..\frontend
npm install

# 3) 回到项目根目录
Set-Location ..
```

### 8.2 开发模式（推荐）

当前数据库部署在华为云 GaussDB 内网，前后端仍在本机运行。启动项目前，先单独打开一个 PowerShell 窗口并保持 SSH 隧道：

```powershell
ssh -N -L 15432:192.168.0.31:8000 root@<ECS公网IP>
```

`backend/.env` 中的 `DATABASE_URL` 指向 `127.0.0.1:15432`，由隧道转发到 GaussDB 内网地址。

```powershell
.\scripts\start-full-dev.ps1
```

- 后端：`http://127.0.0.1:5050`
- 前端：`http://127.0.0.1:5173`

### 8.3 生产演示模式

同样需要先保持 SSH 隧道窗口打开。

```powershell
.\scripts\start-full-prod.ps1
```

- 后端（Waitress）：`http://127.0.0.1:5050`
- 前端预览：`http://127.0.0.1:4173`

可单独启动：

- `.\scripts\start-backend-prod.ps1`
- `.\scripts\start-frontend-prod.ps1`

### 8.4 Linux ECS 公网演示部署

如果需要让合作伙伴直接通过浏览器访问，把前后端部署到能访问 GaussDB 内网地址的 Linux ECS 上：

```bash
sudo git clone https://github.com/Doooppperr/SHU_DB_1-health-system.git /opt/health-system
cd /opt/health-system
sudo bash scripts/linux/setup-public-demo.sh
```

脚本会：

- 校验 `git`、`python3`、`Node.js 18+`、`npm`、`curl` 等运行环境。
- 创建 `health` 系统用户。
- 安装后端虚拟环境依赖与前端 npm 依赖。
- 首次运行时生成 `backend/.env`，默认连接 `192.168.0.31:8000`，并提示输入 `health_app` 数据库密码。
- 构建前端并安装 `health-backend.service`、`health-frontend.service` 两个 `systemd` 服务。

默认访问地址：

- 合作伙伴访问：`http://<ECS公网IP>:4173`
- 后端健康检查（仅服务器本机）：`curl http://127.0.0.1:5050/api/health`

云安全组只需要放行公网入站 TCP `4173`；不要把 `5050` 或 GaussDB 端口暴露到公网。

后续更新代码时，在服务器上重新执行：

```bash
cd /opt/health-system
sudo bash scripts/linux/setup-public-demo.sh
```

脚本会 `git pull`、重新安装依赖、重新构建前端并重启两个服务，已有 `backend/.env` 不会被覆盖。如需强制重写环境文件，可设置 `FORCE_ENV=1`。

日常开发同步流程：

```powershell
# 1) 本地完成修改和测试后提交
git add .
git commit -m "描述本次修改"
git push origin main

# 2) 登录服务器，让公网演示环境同步到最新 main
ssh root@190.92.227.58
cd /opt/health-system
sudo bash scripts/linux/setup-public-demo.sh
```

服务器重启后的访问说明：

- ECS 和 GaussDB 都处于运行中时，等待系统服务自动启动后即可访问 `http://190.92.227.58:4173`。
- 不需要手动启动前端或后端；`health-backend.service` 与 `health-frontend.service` 已设置开机自启。
- 如无法访问，先在服务器检查：

```bash
systemctl status health-backend.service
systemctl status health-frontend.service
curl http://127.0.0.1:5050/api/health
curl -I http://127.0.0.1:4173
```

## 9. 默认账号

- 管理员（启动后自动种子）：`admin / admin123`

建议仅用于本地演示，生产环境请在 `.env` 覆盖默认管理员配置。

## 10. 测试与质量检查

```powershell
# 后端测试
Set-Location .\backend
.\.venv\Scripts\python.exe -m pytest -q

# 前端构建检查
Set-Location ..\frontend
npm run build
```

说明：后端单元测试使用内存 SQLite 测试配置，不依赖云数据库；真实演示运行依赖 SSH 隧道和 GaussDB。

本地历史 SQLite 文件不再作为运行数据库使用，已从仓库移除；如需重新迁移旧数据，请使用仓库外备份文件配合迁移脚本。

数据库级规则更新脚本：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\apply_gaussdb_rules.py
```

当前云端 GaussDB 已完成数据库级规则落库：`22` 个 `CHECK` 约束、`3` 个触发器。日常运行仍使用 `health_app` 应用账号；管理员账号只用于偶发 DDL 维护。

## 11. 补充文档

- 项目需求与技术方案：`项目文档/项目需求与技术方案.md`
- 部署演示与云数据库指南：`项目文档/部署演示与云数据库指南.md`
- 数据库规范化说明：`项目文档/数据库规范化说明.md`
- 全量测试报告：`项目文档/测试报告.md`
- 开发记录与上下文归档：`项目文档/开发记录与上下文归档.md`


