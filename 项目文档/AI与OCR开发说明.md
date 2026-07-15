# 康康健健 HealthDoc AI 与 OCR 开发说明

> 适用版本：当前 schema v4 与本地双通道 RAG，更新于 2026-07-16。本文只描述当前接口和运行边界。

本文说明 AI 流式交互、健康报告分析、OCR 解析和机构报告草稿的当前契约。通用角色与数据库约束分别见《项目需求与技术方案》和《数据库设计说明》。

## 1. 通用约定

- API 根路径为 `/api`，认证使用 JWT Access Token。
- 访客只可使用公开 AI；带健康上下文的 AI、时间线和趋势仅允许 `role=user`。
- 机构 OCR 与报告生产仅允许 `role=institution_admin`。
- 系统管理员没有健康内容接口。
- 数据库和 URL 使用正整数 ID；`reportN` 只是报告展示编号。
- 本人或当前仍授权的亲友健康数据才可访问。撤权、账号停用、报告撤下或删除后，下一次请求立即重新鉴权。
- 不存在和无权访问的健康对象尽量统一返回 404，避免泄露对象是否存在。
- 健康身份码、个人资料、联系方式和草稿原文件不得进入 AI 上下文。

## 2. AI 配置

本地离线模式：

```env
AI_PROVIDER=deepseek
AI_USE_MOCK=1
```

真实模式：

```env
AI_PROVIDER=deepseek
AI_USE_MOCK=0
DEEPSEEK_API_KEY=
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
AI_CONNECT_TIMEOUT_SECONDS=5
AI_READ_TIMEOUT_SECONDS=30
AI_REQUEST_TIMEOUT_SECONDS=60
AI_MAX_HISTORY_MESSAGES=20
AI_SUPPORT_PHONE=
AI_GUEST_RATE_LIMIT_PER_MINUTE=10
AI_AUTH_RATE_LIMIT_PER_MINUTE=30
```

真实密钥只写入被 Git 忽略的 `backend/.env`，不得进入源码、模板、前端、日志或测试输出。

## 3. AI 接口

### 3.1 按需获取可分析报告

`GET /api/ai/records`

- JWT 必需，仅普通用户。
- 只返回本人或已授权亲友的 `published` 机构报告。
- 只提供选择所需元数据，不返回个人资料或健康身份码。
- 前端打开 AI 侧栏时不调用；只有主动引用、收到 `select_records` 或从报告发起分析时才加载。

### 3.2 流式对话

`POST /api/ai/chat/stream`

JWT 可选。访客只能使用公开导览，不能附带报告。请求结构：

```json
{
  "message": "请解释这份报告中的异常指标",
  "history": [
    { "role": "user", "content": "上一轮问题" },
    { "role": "assistant", "content": "上一轮回答" }
  ],
  "summary": "",
  "selected_record_ids": [12],
  "consent": true
}
```

当前字段名 `selected_record_ids` 为 AI 内部兼容命名，数组元素实际对应 `institution_reports.id`。

也可用以下范围替代 `selected_record_ids`；两者互斥：

~~~json
{
  "record_scope": { "owner_id": 3, "mode": "all_confirmed" },
  "consent": true
}
~~~

服务端每次请求重新校验归属人、亲友授权、`published` 状态和指标存在性。稳定错误码包括 `invalid_record_scope`、`record_scope_conflict`、`record_scope_unavailable` 和 `record_consent_required`。SSE 在本地安全规则之后增加 `status.stage=retrieving`；`done` 和非流式结果只公开 `rag_used`、`retrieval_status`、`knowledge_source_count`，不返回来源正文、URL 或内部 grounding ID。

约束：

- `message` 必填，去除首尾空格后非空，最长 2000 字符。
- `history` 可选，最多 `AI_MAX_HISTORY_MESSAGES`（默认 20）条，并保持完整 user/assistant 轮次。
- 单条过长时服务端确定性裁剪；历史达到上限时将早期轮次确定性并入 `summary`，不额外调用模型。
- `selected_record_ids` 为正整数数组，服务端去重并保留首次出现顺序。
- 附带报告时必须同时提交 `consent: true`。
- 报告必须属于同一人，并在请求时仍为本人或授权亲友可见的 `published` 报告。
- 匿名用户、机构账号或管理员附带报告会被拒绝。

`POST /api/ai/chat` 是非流式兼容接口，复用相同验证、安全规则和错误映射；前端优先使用流式接口。

### 3.3 报告智能分析

`POST /api/ai/analyze/stream`

JWT 必需且仅普通用户：

```json
{
  "selected_record_ids": [12, 15, 21],
  "consent": true
}
```

规则：

- 至少选择一份报告；后端对大量 ID 分批查询。
- 所有报告必须属于同一用户，并且仍为 `published`。
- 单报告事实包含标准指标的编码、名称、值、单位、类型、参考范围和异常状态。
- 多报告事实按 `(exam_date, id)` 排序。
- 服务端确定性计算每项指标的首次/最新值、异常次数、缺失、极值和可比较变化。
- 个人每日有效趋势同日优先机构报告；该指标无机构值时使用当天最后一次自测。
- 非数值、缺失或不可比较数据不强行生成变化。
- 全量有效数据参与计算；传给模型的上下文按预算压缩，优先异常、变化和最新状态。
- 模型只解释服务端事实，不自行补算趋势。

### 3.4 AI 数据边界

允许进入上下文：

- 报告日期、机构名称；
- 标准指标编码、名称、规范值、单位、参考范围和异常状态；
- 服务端计算的每日有效序列与确定性摘要。

禁止进入上下文：

- 健康身份码；
- 真实姓名、生日、性别、邮箱、手机号；
- 过敏史、既往史（当前 AI 未授权使用）；
- 机构账号用户名、内部匹配用户 ID；
- OCR 原文件、完整云端响应或未复核候选；
- 未发布、已撤下或已过期报告。

## 4. SSE 协议

响应类型 `text/event-stream`：

```http
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
```

应用不得设置 `Connection` 等 hop-by-hop 响应头，避免 Waitress/WSGI 拒绝。

| 事件 | 主要字段 | 用途 |
|---|---|---|
| `meta` | `request_id`、`mode`、`model` | 请求标识和运行模式 |
| `status` | `stage`、`message` | 校验、分析、安全判断和生成进度 |
| `delta` | `text` | 可展示正文增量 |
| `action` | `action`、`message` | 当前支持 `select_records` |
| `done` | `request_id`、`decision`、`source`、`summary`、`model` | 正常结束 |
| `error` | `request_id`、`code`、`message`、`retryable` | 流内失败 |

认证对话和分析采用 decision-first 安全输出：服务端完成安全决策后才释放正文。流已经开始后发生的错误通常仍是 HTTP 200，通过 `error` 事件表达。

## 5. AI 错误与运行边界

HTTP 预校验错误：

```json
{
  "message": "explicit consent is required before sending record data",
  "error": {
    "code": "record_consent_required",
    "message": "explicit consent is required before sending record data",
    "retryable": false
  }
}
```

常见业务码：

| HTTP | code | 含义 |
|---:|---|---|
| 400 | `message_required`、`message_too_long` | 消息缺失或过长 |
| 400 | `invalid_history`、`invalid_summary`、`invalid_record_ids` | 上下文格式不合法 |
| 400 | `record_consent_required` | 未明确同意发送报告数据 |
| 400 | `records_required` | 分析未选择报告 |
| 400 | `report_not_published`、`record_has_no_indicators`、`mixed_record_owners` | 报告不满足分析条件 |
| 403 | `login_required`、`regular_user_required` | 身份或角色不允许 |
| 404 | `record_unavailable` | 报告不存在、无权访问或当前不可用 |
| 429 | `rate_limited` | 请求过于频繁 |
| 503 | `ai_not_configured` | AI 未配置（非流式接口） |

流内还可能返回 `provider_rate_limited`、`provider_timeout`、`provider_http_error`、`provider_unavailable`、`provider_invalid_response`、`provider_empty_response` 和 `internal_error`。

默认连接、无数据读取和总截止为 5、30、60 秒。只有首个模型内容到达前的连接中断或首轮 502/503/504 最多自动重试一次。客户端取消或断开会关闭上游流。

限流是当前进程内分钟桶：访客按 IP，登录用户按用户 ID；默认 10/30 次。多进程或多实例若需要全局限流，应使用共享存储。

结构化日志只记录请求 ID、操作、模式、报告数量、提示长度、首个正文耗时、总耗时、状态和 token 用量，不记录问题、回复、指标值、健康身份或密钥。

## 6. OCR 配置与入口

本地离线模式：

```env
OCR_PROVIDER=huawei
OCR_USE_MOCK=1
```

真实模式需要：

```env
OCR_USE_MOCK=0
HUAWEI_OCR_ENDPOINT=
HUAWEI_OCR_AK=
HUAWEI_OCR_SK=
HUAWEI_PROJECT_ID=
OCR_API_PATH=/v2/{project_id}/ocr/general-table
OCR_PDF_MAX_PAGES=8
OCR_AUTO_CONFIRM_MIN_SCORE=0.92
```

当前 OCR 入口：

`POST /api/org/reports/ocr`

- JWT 必需，仅机构账号。
- `multipart/form-data`；请求总大小上限 20 MiB。
- 支持 `.pdf`、`.png`、`.jpg`、`.jpeg`、`.webp`。
- 表单同时提交 `subject_name`、`subject_health_id`、`exam_date`，可选 `package_id`。
- 机构 ID 始终取当前账号绑定，不接收客户端指定。
- 成功创建 `draft` 报告并返回报告、已安全映射指标、全部候选和诊断。

## 7. OCR 解析与映射

华为通用表格 OCR 使用 `region-v2`：

1. 每个表格区域独立收集行列，避免多个表格从第 0 行开始时互相覆盖；
2. 每个区域分别识别表头；无标准表头时根据代码、名称和数值特征推断列；
3. 表格候选与表格外文本并行解析，兼容双栏资料、无边框列表和英文行式结果；
4. 相同标签和值去重时优先结构化表格来源；
5. 所有候选只与当前 `indicator_dicts` 的代码、名称和别名比较。

映射安全规则：

- 精确代码/别名优先；纯英文短代码不做任意子串匹配。
- 同一指标多候选先按规范值比较；值冲突时 `requires_review=true`。
- 数值必须只有一个明确数字；歧义逗号、多个数字或不安全文本不能静默修正。
- 常见微摩尔单位字形可规范化，但不做字典未声明的单位换算。
- 低置信度、非法数值、冲突和不安全模糊匹配不自动写入指标。
- 姓名、性别、日期、医生、机构等报告元数据进入过滤结果。
- 未配置医疗项目进入未匹配结果，不猜成最相似指标。

成功响应中的 `ocr` 包含：

- `candidate_mappings`：候选字典 ID、名称、值、分数、来源、冲突和复核标志；
- `diagnostics`：引擎、解析器版本、字段数、候选数、未匹配数、冲突数和需复核数；
- 最多 30 项未匹配字段摘要。

自动写入只处理 `requires_review=false` 且能通过指标规范化的候选，来源记为 `ocr`。需复核候选由机构人员在草稿页手工补充或修正。

## 8. OCR 草稿、锁定与文件生命周期

OCR 上传成功后：

- 创建 `status=draft` 的机构报告；
- 保存自动确认的 `report_indicators`；
- 保存必要 `ocr_diagnostics`；
- 原文件暂存在上传目录，并由 `temporary_file_url` 引用；
- 机构人员可在草稿阶段修改基本信息和指标。

`POST /api/org/reports/{id}/lock`：

- 仅草稿可锁定；
- 至少需要一项指标；
- 受检者姓名和健康身份码必须对应启用注册用户；
- 设置 `status=locked` 与 `locked_at`；
- 清空 `temporary_file_url`；
- 从诊断中移除原始文本、字段和 provider response 等大块敏感内容；
- 事务提交后物理删除临时原文件。

锁定后所有报告与指标写接口返回 409。用户、亲友和系统管理员都没有原文件读取接口；公共 `/uploads/reports/...` 返回 404。

## 9. 锁定后的提交与自动归档

`POST /api/org/reports/{id}/submit`：

- 仅接受 `locked` 状态；
- 按受检者姓名与健康身份码查找唯一启用注册普通用户；
- 匹配成功返回 `match_result=matched`、写入归属人并进入 `published`；
- 提交时账号不再启用则返回 409，报告保持 `locked`；
- 不返回候选用户或近似匹配详情。

`POST /api/org/reports/{id}/withdraw`：

- 允许从 `locked/published` 撤下；
- 报告进入 `withdrawn`，报告正文不再提供；用户和亲友时间线仅保留撤下状态事件，趋势和 AI 排除报告指标。

## 10. 前端集成要求

- 打开 AI 侧栏时不预加载报告；只有主动引用或动作触发时加载。
- 用户消息先显示，再按 `delta` 追加 AI 内容；收到 `error` 后保留已显示内容。
- 每次 AI 请求完成、失败或取消后重置报告选择与同意；切换选择必须重新同意。
- 使用 AbortController 取消，发送中禁止重复提交。
- 机构 OCR 页必须展示解析器版本、候选分数、冲突、未匹配和需复核状态。
- 不得把 `unmatched` 或 `requires_review=true` 候选自动提交为正式指标。
- 锁定前要求机构人员确认至少一项指标；锁定后界面完全只读。
- 报告展示使用 `display_id`，所有 API 参数继续使用整数 `id`。

## 11. 本地双通道 RAG

- 私人档案继续从 SQLite/GaussDB 按请求权限读取，趋势和异常由确定性代码计算；档案值、用户问题、聊天、OCR 原文和用户 ID 不进入 Qdrant。
- 公共知识使用 `BAAI/bge-small-zh-v1.5` 的 512 维本地向量和 Qdrant Local。访客过滤为 `public`，登录普通用户可额外检索 `authenticated` 医学白名单；管理员不能附带健康档案。
- 固定顺序为急症规则、选档/FAQ、同意与鉴权、档案事实、知识检索、DeepSeek 安全决策。无命中、索引缺失或模型失败会以 `no_match`、`unavailable` 或 `disabled` 降级。
- 语料在 prompt 中作为不可信 user 数据，模型返回的 grounding ID 只接受本次检索集合内编号。日志仅记录耗时、数量、稳定 source ID、分数和状态。
- `backend/rag_sources/manifest.json` 是精确 URL 与批准 SHA-256 清单。应用启动和请求不联网，只有 `scripts/rag_sync.py sync` 抓取；扫描 PDF 的本地 OCR 也只在该命令运行。
- 同步生成版本化 collection 并原子切换 `healthdoc_knowledge_current`。哈希变化进入运行目录隔离，审核后执行 `approve-change` 更新批准清单，再次 sync 才切换。
- `scripts/evaluate_rag.py` 运行中文黄金查询；可回答查询的 Top-5 命中率必须至少 90%。

本地运行目录 `backend/instance/rag/` 被 Git 忽略。Qdrant Local 仅支持单后端进程；设置 `RAG_QDRANT_URL` 后可切换独立服务而不改变业务调用方。

## 12. 相关实现与验证

主要实现文件：

- `backend/app/ai/routes.py`
- `backend/app/ai/service.py`
- `backend/app/ai/rag.py`
- `backend/app/ai/ingestion.py`
- `backend/scripts/rag_sync.py`
- `backend/scripts/seed_rag_demo.py`
- `backend/app/org/routes.py`
- `backend/app/services/ocr.py`
- `backend/app/services/reports.py`
- `backend/app/services/indicator_values.py`
- `frontend/src/components/AiAssistant.vue`
- `frontend/src/stores/aiChat.js`
- `frontend/src/views/org/OrgReportsView.vue`
- `frontend/src/utils/aiStageLayout.js`

验证命令：

```powershell
Push-Location .\backend
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\evaluate_rag.py
Pop-Location

Push-Location .\frontend
npm test
npm run build
npm audit --omit=dev
Pop-Location
git diff --check
```

当前自动化覆盖 AI 同意与隐私边界、角色隔离、RAG 稳定切分/同步/重排/安全降级、OCR Mock、草稿指标、锁定删除临时文件、机构范围隔离及发布后数据可见性。真实外部服务凭据不进入自动化测试。
