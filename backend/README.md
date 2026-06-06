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

```bash
python run.py
```

服务默认运行在 `http://127.0.0.1:5000`。

## 生产模式启动（Waitress）

```bash
python -m waitress --listen=0.0.0.0:5000 wsgi:app
```

项目根目录也提供脚本：

```powershell
.\scripts\start-backend-prod.ps1
```

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
```

- `OCR_USE_MOCK=1`：使用本地 Mock OCR（开发联调用）
- `OCR_USE_MOCK=0`：启用华为云真实 OCR
- 上传 PDF 时会自动按页转图片后调用 OCR（最多 `OCR_PDF_MAX_PAGES` 页，默认 8）
- 默认自动创建管理员账号（账号密码可通过 `DEFAULT_ADMIN_*` 覆盖）
