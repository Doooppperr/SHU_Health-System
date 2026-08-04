# 康康健健 HealthDoc AI 与 OCR 开发说明

> 文档状态：融合历史 AI/Agent/OCR/RAG 与第六轮业务验收的当前说明。运行基线为 schema v12，更新于 2026-07-31。

硬边界：机构推荐只使用启用机构/分院/在售套餐等平台事实；健康上下文只属于当前有效账号，关联亲友必须先切换账号，不能用 `owner_id` 绕过切换；健康码参与人先在服务端换成一次性令牌，真实 bearer 只进入加密 slot 映射，模型只见非秘密 slot 与脱敏摘要，工具日志只见字段名。联系方式、原始健康身份码、bearer、过敏史和既往史始终不发给模型。

OCR 字典已扩充至 104 项并同步中文全称、英文缩写、旧称、单位和领域。解析表头同时提取项目、值、单位、参考范围和 H/L/↑/↓；值冲突、别名歧义、单位不兼容及低置信度均标为人工复核。机构原始参考范围优先，系统性别/年龄规则其次，通用范围再次；无可靠规则时内部状态为 `unknown`，用户界面和 AI 答案不展示“未判定”标签。身高、体重和臀围属于描述性测量值，不单独输出正常/异常，体重相关风险通过 BMI 等派生指标表达。

本文先说明能力如何演进，再给出当前 AI 流式交互、健康报告分析、OCR 解析和机构报告生产契约。通用角色与数据库约束分别见《项目需求与技术方案》和《数据库设计说明》；本文件出现的接口、状态和数据权限均以当前实现为准。

### 版本演进

| 阶段 | AI/OCR 目标 | 延续到 3.0 的结果 |
|---|---|---|
| 1.0 | 提供基础智能问答和报告信息录入辅助 | 明确 AI 是辅助入口，不能替代医疗判断 |
| 2.0 | 建立 SSE 流式对话、报告选择与权限校验、OCR 草稿/锁定/归档，以及公共知识与私人档案双通道 | 私人数据按请求鉴权，确定性代码计算健康事实，OCR 结果必须人工复核 |
| 3.0 | 按健康领域约束套餐与报告生产，支持文本结论、检查附件和图片辅助分析，并适配分院协作 | AI 仅分析本人或已授权档案；兄弟分院只读已归档报告；草稿、OCR 临时件和私人附件不会进入公共 RAG |

历史 schema v10 没有建立“AI 诊断”实体；schema v11/v12 的 Agent 状态只保存加密运行数据和脱敏工具审计，也不会把模型回答写成医疗档案。

## 1. 通用约定

- API 根路径为 `/api`，认证使用 JWT Access Token。
- 访客只可使用公开 AI；带健康上下文的 AI、时间线和趋势仅允许 `role=user`。
- 机构 OCR 与报告生产仅允许 `role=institution_admin`。
- 系统管理员没有健康内容接口。
- 数据库和 URL 使用正整数 ID；`reportN` 只是报告展示编号。
- 只访问当前有效账号的健康数据；关联会话链任一授权撤销、账号停用或安全版本变化后立即失效。
- 不存在和无权访问的健康对象尽量统一返回 404，避免泄露对象是否存在。
- 健康身份码、个人资料、联系方式和草稿原文件不得进入 AI 上下文。
- 同一机构主体的兄弟分院只能读取已经归档且通过权限检查的报告；跨院权限不会让机构账号获得普通用户 AI 上下文。

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
- 只返回当前有效账号的 `published` 机构报告。
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
  "active_record_context": {
    "owner_id": 3,
    "anchor_record_ids": [12],
    "scope_mode": "selected_records",
    "indicator_codes": []
  }
}
```

`active_record_context` 是对话级持续档案焦点；前端在同一会话中保存并在后续消息继续提交。当前轮实际使用的档案会在 `record_resolution` 中返回。旧客户端仍可使用 `selected_record_ids` 或 `record_scope`，数组元素实际对应 `institution_reports.id`。

也可用以下范围替代 `selected_record_ids`；两者互斥：

~~~json
{
  "record_scope": { "owner_id": 3, "mode": "all_confirmed" },
  "consent": true
}
~~~

服务端每次请求重新校验归属人等于当前有效账号、`published` 状态和指标存在性。旧客户端携带其他 `owner_id` 时拒绝，而不是直接读取关联亲友。SSE 只公开检索状态和脱敏引用元数据，不返回来源正文、URL 或内部 grounding ID。

约束：

- `message` 必填，去除首尾空格后非空，最长 2000 字符。
- `history` 可选，最多 `AI_MAX_HISTORY_MESSAGES`（默认 20）条，并保持完整 user/assistant 轮次。
- 单条过长时服务端确定性裁剪；历史达到上限时将早期轮次确定性并入 `summary`，不额外调用模型。
- `selected_record_ids` 为正整数数组，服务端去重并保留首次出现顺序。
- 发送 `active_record_context` 后，档案在当前对话中持续有效；用户可以在前端“更换”或“清除”。
- 报告必须属于同一人，且该人就是当前有效账号，状态仍为 `published`。
- 匿名用户、机构账号或管理员附带报告会被拒绝。

`POST /api/ai/chat` 是非流式兼容接口，复用相同的数据权限、输入验证和错误映射；前端优先使用流式接口。

### 3.3 对话级档案上下文

服务端返回的 `record_resolution` 是本轮审计快照，包含 `source`、成员真实姓名、范围模式、锚点报告、实际报告数量、日期范围、指标和最多 10 条机构摘要；`records_truncated` 明确是否只展示了部分摘要。`next_active_record_context` 是下一轮可直接提交的最小状态。

- “分析我上一次的体检报告”自动选择本人最新已发布报告；
- “这个报告/其中/刚才那份”继承锚点报告；
- “这个指标的趋势/历史变化”在同一成员内扩展为包含该指标的历史报告；
- 明确年份、成员或新报告会替换上下文，不能混预置员；
- 普通系统功能问题不发送档案，但不会清除上下文；
- 清除、结束对话、退出登录和切换账号时清除浏览器 `sessionStorage`；
- 后端每轮重新检查当前有效账号和报告状态；关联链失效或报告不可用时返回 `record_unavailable`。

前端每条回答展示“本次参考”，包括成员、数量、日期、机构以及“系统自动引用/继承当前档案/自动扩展趋势”标签。

### 3.4 报告智能分析

`POST /api/ai/analyze/stream`

JWT 必需且仅普通用户：

```json
{
  "selected_record_ids": [12, 15, 21]
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

### 3.5 当前趋势自动解读

`POST /api/ai/trends/stream` 接收 `domain_id`、日期范围和指标多选；如兼容字段出现 `owner_id`，也必须等于当前有效账号。服务端从数据库构造数据点、单位、来源和参考范围，不接受浏览器上传可伪造图表事实。变更日期、健康方向或指标会取消旧请求并生成新解读。

趋势输出解释整体变化、重点数据、参考范围适用条件、不同来源的可比性和健康管理建议。系统直接结合本轮选定指标生成回答，不再插入固定拦截模板或重复授权步骤。

### 3.6 AI 数据边界

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
- 未发布或已过期报告。

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
| `status` | `stage`、`message` | 校验、档案解析、分析和生成进度 |
| `delta` | `text` | 可展示正文增量 |
| `action` | `action`、`message` | 当前支持 `select_records` |
| `done` | `request_id`、`decision`、`source`、`summary`、`model`、`record_resolution`、`next_active_record_context` | 正常结束并返回本轮引用审计和下一轮持续上下文 |
| `error` | `request_id`、`code`、`message`、`retryable` | 流内失败 |

认证对话和分析采用 decision-first 输出：服务端先完成档案解析和上下文构建，再释放回答正文。流已经开始后发生的错误通常仍是 HTTP 200，通过 `error` 事件表达。

## 5. AI 错误与运行边界

HTTP 预校验错误：

```json
{
  "message": "当前档案不可用或授权已失效，请重新选择",
  "error": {
    "code": "record_unavailable",
    "message": "当前档案不可用或授权已失效，请重新选择",
    "retryable": false
  }
}
```

常见业务码：

| HTTP | code | 含义 |
|---:|---|---|
| 400 | `message_required`、`message_too_long` | 消息缺失或过长 |
| 400 | `invalid_history`、`invalid_summary`、`invalid_record_ids` | 上下文格式不合法 |
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
- 成功创建 `draft` 报告并返回报告、已映射指标、全部候选和诊断。

## 7. OCR 解析与映射

华为通用表格 OCR 使用 `region-v2`：

1. 每个表格区域独立收集行列，避免多个表格从第 0 行开始时互相覆盖；
2. 每个区域分别识别表头；无标准表头时根据代码、名称和数值特征推断列；
3. 表格候选与表格外文本并行解析，兼容双栏资料、无边框列表和英文行式结果；
4. 相同标签和值去重时优先结构化表格来源；
5. 所有候选只与当前 `indicator_dicts` 的代码、名称和别名比较。

映射校验规则：

- 精确代码/别名优先；纯英文短代码不做任意子串匹配。
- 同一指标多候选先按规范值比较；值冲突时 `requires_review=true`。
- 数值必须只有一个明确数字；歧义逗号、多个数字或不兼容文本不能静默修正。
- 常见微摩尔单位字形可规范化，但不做字典未声明的单位换算。
- 低置信度、非法数值、冲突和歧义模糊匹配不自动写入指标。
- 姓名、性别、日期、医生、机构等报告元数据进入过滤结果。
- 未配置医疗项目进入未匹配结果，不猜成最相似指标。

成功响应中的 `ocr` 包含：

- `candidate_mappings`：候选字典 ID、名称、值、分数、来源、冲突和复核标志；
- `diagnostics`：引擎、解析器版本、字段数、候选数、未匹配数、冲突数和需复核数；
- 最多 30 项未匹配字段摘要。

自动写入只处理 `requires_review=false` 且能通过指标规范化的候选，来源记为 `ocr`。需复核候选由机构人员在草稿页手工补充或修正。

## 8. OCR 草稿、待复核与文件生命周期

OCR 上传成功后：

- 创建 `status=draft` 的机构报告；
- 保存自动确认的 `report_indicators`；
- 保存必要 `ocr_diagnostics`；
- 原文件暂存在上传目录，并由 `temporary_file_url` 引用；
- 机构人员可在草稿阶段修改基本信息和指标。

上传确认接口：

- 仅 `draft` 可上传确认；
- 至少需要一项指标；
- OCR 报告必须来自状态为“待上传报告”的预约，受检者、健康身份码、机构、套餐和日期由预约快照提供；
- 必须填写上传医生姓名，写入 `upload_doctor_name` 和提交复核时间，并设置 `status=pending_review`；
- 待复核阶段仍可修正指标、结论和附件，用户及兄弟分院不可见。

用户、关联账号、兄弟分院和系统管理员都没有草稿原文件读取接口；公共 `/uploads/reports/...` 返回 404。

## 9. 复核确认与原子发布

复核确认接口：

- 仅接受 `pending_review`，必须填写复核医生姓名；上传医生和复核医生允许同一人；
- 再次校验受检者仍启用、已实名以及报告方向/内容完整性；
- 同一事务写入复核医生和时间、锁定报告、进入 `published`、预约履约、预约事件和用户通知，并清理临时文件；
- 并发或状态冲突返回 `REPORT_STATE_CONFLICT`，事务整体回滚；
- 发布后永久只读，不提供撤下、删除或回到待复核/草稿的接口。

## 10. 前端集成要求

- 打开 AI 侧栏时不预加载报告；只有主动引用或动作触发时加载。
- 用户消息先显示，再按 `delta` 追加 AI 内容；收到 `error` 后保留已显示内容。
- AI 请求完成、失败或取消后保留当前 `active_record_context`；只有用户清除、更换、结束对话、退出登录或切换账号才会移除。
- 使用 AbortController 取消，发送中禁止重复提交。
- 机构 OCR 页必须展示解析器版本、候选分数、冲突、未匹配和需复核状态。
- 不得把 `unmatched` 或 `requires_review=true` 候选自动提交为正式指标。
- 上传确认前要求至少一项指标和上传医生；复核确认要求复核医生；只有发布后界面完全只读。
- 报告展示使用 `display_id`，所有 API 参数继续使用整数 `id`。

## 11. 本地双通道 RAG

- 私人档案继续从 SQLite/GaussDB 按请求权限读取，趋势和异常由确定性代码计算；档案值、用户问题、聊天、OCR 原文和用户 ID 不进入 Qdrant。
- 公共知识使用 `BAAI/bge-small-zh-v1.5` 的 512 维本地向量和 Qdrant Local。`RAG_EMBEDDING_THREADS` 默认 `1`，同时约束 FastEmbed 与扫描 PDF 的 RapidOCR/ONNX 线程；FastEmbed 关闭 ONNX CPU 内存池，每份扫描 PDF 在独立子进程完成 OCR并在结束后由操作系统回收模型内存，避免 4GB 服务器因连续处理产生内存交换。资源充足时可显式调高线程数。访客过滤为 `public`，登录普通用户可额外检索 `authenticated` 医学白名单；管理员不能附带健康档案。
- 固定顺序为系统数据/选档/FAQ、鉴权、档案事实、知识检索和 DeepSeek 回答。无命中、索引缺失或模型失败会以 `no_match`、`unavailable` 或 `disabled` 降级。
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

当前自动化覆盖 AI 当前有效账号边界、趋势扩展、重试与角色隔离、RAG 稳定切分/同步/重排/缓存降级、OCR Mock、草稿/待复核/原子发布、机构范围隔离及发布后数据可见性。最终数量以《测试报告》的真实门禁记录为准。

## 13. schema v11 Agent 历史补充与 v12 适配

schema v11 新增 `/api/agent`、严格 Pydantic 工具和逐次确认；schema v12 继续沿用，但健康读取只允许当前有效账号，预约参与人类型为 `self/linked_account/health_code_token`。原始健康身份码由第一方安全输入在模型调用前换成短时令牌；真实 bearer 保存在独立加密 slot 映射中，模型消息、模型历史和出站请求只包含非秘密 slot，工具执行前才做字段级解析，工具审计不记录参数值。

公共知识可通过 `RAG_HYBRID_ENABLED` 切换为 Dense + 中文词法 BM25 + RRF。现有 4GB 服务器不常驻通用小 LLM，`AGENT_ROUTER_ENABLED` 仅保留接口。完整协议、OAuth/MCP、OTel 和评测门禁见《第六轮Agent升级实施说明》。
