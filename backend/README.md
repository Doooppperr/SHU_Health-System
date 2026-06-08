# Health System Backend

## 安装依赖

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 启动服务

当前后端数据库为华为云 GaussDB。启动后端前，先保持 SSH 隧道窗口打开：

```powershell
ssh -N -L 15432:192.168.0.31:8000 root@<ECS公网IP>
```

`.env` 中使用应用账号连接本机隧道端口：

```env
DATABASE_URL=opengauss+psycopg2://health_app:<HEALTH_APP_PASSWORD>@127.0.0.1:15432/health_system?client_encoding=utf8
```

后端不再默认回退到本地 SQLite；未配置 `DATABASE_URL` 时会直接报错，避免误生成新的本地数据库。

并发连接池可通过 `.env` 中的 `DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_POOL_TIMEOUT`、`DB_POOL_RECYCLE` 调整。

```bash
python run.py
```

服务默认运行在 `http://127.0.0.1:5050`。

## 生产模式启动（Waitress）

```bash
python -m waitress --listen=0.0.0.0:5050 wsgi:app
```

项目根目录也提供脚本：

```powershell
.\scripts\start-backend-prod.ps1
```

如需把模型中的数据库级约束和触发器应用到现有 GaussDB：

```powershell
python .\scripts\apply_gaussdb_rules.py
```

当前云端 GaussDB 已执行该脚本并验证通过，包含 `22` 个 `CHECK` 约束和 `3` 个触发器。后端运行账号仍为 `health_app`。

## Linux ECS 公网演示说明

公网演示环境中，后端由 `health-backend.service` 在服务器本机运行，监听 `127.0.0.1:5050`，不直接暴露公网端口。前端 `health-frontend.service` 监听 `0.0.0.0:4173`，并把 `/api` 与 `/uploads` 代理到本机后端。

服务器与 GaussDB 位于同一 VPC 时，`backend/.env` 应使用内网数据库地址：

```env
DATABASE_URL=opengauss+psycopg2://health_app:<HEALTH_APP_PASSWORD>@192.168.0.31:8000/health_system?client_encoding=utf8
```

这种部署模式不需要每个访问者 clone 项目或建立 SSH 隧道；合作伙伴只需访问 `http://190.92.227.58:4173`。

## OCR 配置

复制 `.env.example` 为 `.env` 后填写华为云配置：

```env
OCR_PROVIDER=huawei
OCR_USE_MOCK=1
HUAWEI_OCR_ENDPOINT=
HUAWEI_OCR_AK=
HUAWEI_OCR_SK=
HUAWEI_PROJECT_ID=
OCR_API_PATH=/v2/{project_id}/ocr/general-table
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=admin123
DEFAULT_ADMIN_EMAIL=admin@example.com
CAPTCHA_TTL_SECONDS=300
```

- `OCR_USE_MOCK=1`：使用本地 Mock OCR（开发联调用）
- `OCR_USE_MOCK=0`：启用华为云真实 OCR
- 上传 PDF 时会自动按页转图片后调用 OCR（最多 `OCR_PDF_MAX_PAGES` 页，默认 8）
- 默认自动创建管理员账号（账号密码可通过 `DEFAULT_ADMIN_*` 覆盖）
