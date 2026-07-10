# 体检评价与健康档案系统

这是一个基于 Flask、Vue 3 和 SQLite 的本地健康档案管理系统，支持用户认证、体检机构浏览、健康档案录入、OCR 报告识别、亲友授权、趋势分析和评论审核。

## 技术栈

| 层级 | 技术 |
|---|---|
| 前端 | Vue 3、Vite、Vue Router、Pinia、Element Plus、Axios、ECharts |
| 后端 | Flask、Flask-SQLAlchemy、Flask-JWT-Extended、Flask-Cors |
| 数据库 | SQLite |
| OCR | 本地 Mock；可选接入华为云 OCR API |
| 本机演示服务 | Waitress、Vite Preview |
| 测试 | Pytest |

## 主要功能

- 注册、登录、图片验证码和 JWT 鉴权。
- 体检机构及套餐浏览。
- 手动创建健康档案和维护体检指标。
- 上传图片或 PDF，通过 OCR 解析并人工确认入档。
- 添加亲友、管理授权、代传亲友档案。
- 按用户和指标查看历史趋势。
- 用户评论、管理员审核和用户管理。

## 项目结构

```text
health system/
├─ backend/
│  ├─ app/                         # Flask 应用
│  ├─ instance/health_system.db    # 本地 SQLite 数据库
│  ├─ tests/                       # 后端测试
│  ├─ .env.example                 # 可选环境变量模板
│  ├─ run.py                       # 开发入口
│  └─ wsgi.py                      # Waitress 入口
├─ frontend/                       # Vue 3 前端
├─ scripts/                        # Windows 一键启动脚本
└─ 项目文档/                       # 设计、测试和运行文档
```

## 环境要求

- Windows 10/11 和 PowerShell 5.1+
- Python 3.10+
- Node.js 18+
- npm 9+

## 首次安装

在项目根目录执行：

```powershell
Set-Location .\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Set-Location ..\frontend
npm install

Set-Location ..
```

如需配置真实 OCR 或修改默认账号：

```powershell
Copy-Item .\backend\.env.example .\backend\.env
```

数据库无需配置。系统固定使用本地文件：

```text
backend/instance/health_system.db
```

如果确实需要换用另一个本地 SQLite 文件，可在 `backend/.env` 设置：

```env
LOCAL_DATABASE_URL=sqlite:///another-local.db
```

数据库仍保留规范化和完整性优化：

- 表结构达到 3NF，核心候选键通过唯一约束落实。
- 22 个命名 CHECK 约束限制角色、状态、评分、价格等字段。
- 3 个命名联合唯一约束防止机构、套餐和指标字典重复。
- 每个 SQLite 连接自动启用外键检查。

模型变化后需要把新约束同步到已有本地数据库时执行：

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
```

脚本会先备份原数据库，再重建表、复制数据并执行完整性验证。

## 本地开发

在项目根目录执行：

```powershell
.\scripts\start-full-dev.ps1
```

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:5050`
- 健康检查：`http://127.0.0.1:5050/api/health`

也可以单独启动：

```powershell
.\scripts\start-backend-dev.ps1
.\scripts\start-frontend-dev.ps1
```

## 本机生产演示

需要在本机验证构建产物或使用 Waitress 时执行：

```powershell
.\scripts\start-full-prod.ps1
```

- 前端：`http://127.0.0.1:4173`
- 后端：`http://127.0.0.1:5050`

该模式仍然读取同一个本地 SQLite 数据库，不需要任何远程服务。

## 默认账号

```text
用户名：admin
密码：admin123
```

该账号仅适合本地开发和演示。可在 `backend/.env` 中通过 `DEFAULT_ADMIN_*` 修改。

## OCR 配置

默认建议使用本地 Mock：

```env
OCR_PROVIDER=huawei
OCR_USE_MOCK=1
```

需要验证真实 OCR 时，将 `OCR_USE_MOCK` 改为 `0`，并填写 `.env.example` 中的 OCR Endpoint、AK、SK 和 Project ID。

## 测试

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m pytest -q

Set-Location ..\frontend
npm run build
```

## 数据备份

停止后端后，复制以下文件即可完成完整数据库备份：

```text
backend/instance/health_system.db
```

上传的报告文件保存在 `backend/uploads/`，如需保留也应一起备份。

数据库结构升级脚本生成的
`health_system.before-normalization-*.db` 是自动备份，可在确认升级后按需保留。

## 文档

- [项目需求与技术方案](项目文档/项目需求与技术方案.md)
- [本地运行与演示指南](项目文档/本地运行与演示指南.md)
- [数据库设计说明](项目文档/数据库设计说明.md)
- [数据库规范化说明](项目文档/数据库规范化说明.md)
- [测试报告](项目文档/测试报告.md)
- [开发记录与上下文归档](项目文档/开发记录与上下文归档.md)
