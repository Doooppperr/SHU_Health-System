# Health System Backend

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 数据库

后端使用本地 SQLite：

```text
instance/health_system.db
```

开发入口和 Waitress 入口使用同一个数据库。首次使用空数据库时，应用会自动建表并写入机构、套餐、指标字典和默认管理员等种子数据。

SQLite 连接会自动执行：

```sql
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
```

数据库模型包含候选键唯一约束和 22 个命名 CHECK 约束。

如需切换到另一个本地数据库，可在 `.env` 设置：

```env
LOCAL_DATABASE_URL=sqlite:///another-local.db
```

## 升级已有数据库结构

SQLite 无法通过普通 `ALTER TABLE` 添加大部分表级约束。模型约束发生变化后执行：

```powershell
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py
```

只检查当前数据库是否缺少约束：

```powershell
.\.venv\Scripts\python.exe .\scripts\upgrade_local_database.py --check-only
```

升级过程会：

1. 按当前 SQLAlchemy 模型创建临时数据库。
2. 按外键依赖顺序复制所有数据。
3. 校验行数、外键、约束和 SQLite 完整性。
4. 生成 `instance/health_system.before-normalization-*.db` 备份。
5. 原子替换正式数据库文件。

## 开发启动

```powershell
.\.venv\Scripts\python.exe run.py
```

默认地址：`http://127.0.0.1:5050`

## Waitress 演示启动

```powershell
.\.venv\Scripts\python.exe -m waitress --listen=0.0.0.0:5050 --threads=8 wsgi:app
```

项目根目录也提供：

```powershell
.\scripts\start-backend-dev.ps1
.\scripts\start-backend-prod.ps1
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试使用独立的内存 SQLite，不会修改 `instance/health_system.db`。

## OCR

本地开发建议：

```env
OCR_USE_MOCK=1
```

如需真实 OCR，在 `.env` 中配置 `HUAWEI_OCR_ENDPOINT`、`HUAWEI_OCR_AK`、`HUAWEI_OCR_SK` 和 `HUAWEI_PROJECT_ID`。
