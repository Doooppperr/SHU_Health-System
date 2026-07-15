# AI 与 OCR 开发说明

> 适用版本：本地双通道 RAG 发布候选；文档更新于 2026-07-15。

本文描述当前 AI 流式交互、档案智能分析和 OCR 两阶段录入的接口契约。数据库结构与通用权限规则分别见《数据库设计说明》和《项目需求与技术方案》。

## 1. 通用约定

- API 根路径为 `/api`，认证使用现有 JWT Access Token。
- 普通用户档案接口和带档案的 AI 只允许 `role=user`。机构管理员和系统管理员使用各自后台接口，不能复用普通用户档案上下文。
- 数据库和 URL 使用正整数 `id`。档案响应额外返回 `display_id: "health{id}"`，指标响应使用 `record_display_id`；`healthN` 只是展示值，不能作为 URL 参数、OCR `record_id` 或 AI `selected_record_ids`。
- 本人或当前仍授权的亲友档案才可访问。撤权、删除或状态变化后，下一次请求会立即重新鉴权。
- 不存在和无权访问的档案通常统一返回 404，避免泄露档案是否存在。

## 2. AI 接口

### 2.1 按需获取可分析档案

`GET /api/ai/records`

- JWT 必需，仅普通用户。
- 仅返回本人或已授权亲友、`status=confirmed` 且至少有一项指标的档案。
- 只返回元数据，不返回指标值；前端仅在用户主动引用档案或收到选档动作时调用。

响应示例：

~~~json
{
  "items": [
    {
      "id": 12,
      "display_id": "health12",
      "owner_id": 3,
      "owner": { "id": 3, "username": "demo", "label": "本人" },
      "exam_date": "2026-07-13",
      "institution": { "id": 1, "name": "示例机构" },
      "indicator_count": 8,
      "status": "confirmed"
    }
  ],
  "owners": [
    {
      "owner_id": 3,
      "owner": { "id": 3, "username": "demo", "label": "本人" },
      "record_count": 1,
      "date_range": { "first": "2026-07-13", "latest": "2026-07-13" }
    }
  ]
}
~~~

### 2.2 流式对话

`POST /api/ai/chat/stream`

JWT 可选。访客只能使用公开导览，不能附带档案。请求示例：

~~~json
{
  "message": "请解释我这次报告中的异常指标",
  "history": [
    { "role": "user", "content": "上一轮问题" },
    { "role": "assistant", "content": "上一轮回答" }
  ],
  "summary": "",
  "selected_record_ids": [12],
  "consent": true
}
~~~

也可用以下范围替代 `selected_record_ids`；两者互斥：

~~~json
{
  "record_scope": { "owner_id": 3, "mode": "all_confirmed" },
  "consent": true
}
~~~

服务端每次请求重新校验归属人、亲友授权、`confirmed` 状态和指标存在性。稳定错误码包括 `invalid_record_scope`、`record_scope_conflict`、`record_scope_unavailable` 和 `record_consent_required`。SSE 在本地安全规则之后增加 `status.stage=retrieving`；`done` 和非流式结果只公开 `rag_used`、`retrieval_status`、`knowledge_source_count`，不返回来源正文、URL 或内部 grounding ID。

约束：

- `message` 必需、去除首尾空格后非空，最多 2000 字符。
- `history` 可选，最多 `AI_MAX_HISTORY_MESSAGES`（默认 20）条，必须是完整的 user/assistant 交替轮次；单条超过 4000 字符时保留头尾并确定性裁剪。
- `summary` 可选，最多 6000 字符。历史达到 20 条时，服务端将最早一轮确定性并入摘要，不额外发起模型摘要调用。
- `selected_record_ids` 为内部正整数数组，服务端去重并保留首次出现顺序；有档案时必须同时传 `consent: true`。
- 匿名用户或两类管理员附带档案会被拒绝。

`POST /api/ai/chat` 是兼容用非流式接口，复用相同验证、安全规则和错误映射。新前端应优先使用流式接口。

### 2.3 档案智能分析

`POST /api/ai/analyze/stream`

JWT 必需且仅普通用户。请求：

~~~json
{
  "selected_record_ids": [12, 15, 21],
  "consent": true
}
~~~

规则：

- 至少选择一份档案，不设置用户可见数量上限；后端每 400 个 ID 分批查询。
- 所有档案必须属于同一所有者，并且均为本人或已授权亲友、`confirmed` 且至少有一项指标。
- 单档事实包含全部指标的编码、名称、值、单位、类型、参考范围和状态。
- 多档先按 `(exam_date, id)` 排序，再对全量数据计算每项指标的首次/最新值、异常次数、缺失数和同日多记录；存在数值观察时才提供极值，只有 `comparable=true` 时才提供绝对变化，且首值非 0 时才提供百分比变化。
- 只有数值型、至少两个数值、至少两个日期且该指标不存在同日多记录时才标记为可比较。非数值、缺失和同日多记录不会强行生成趋势。
- 全量数据参与确定性计算；传给模型的上下文按预算压缩，优先异常、变化和最新状态。模型只解释事实，不重新计算趋势。

### 2.4 SSE 协议

响应类型为 `text/event-stream`，使用：

~~~http
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
~~~

不得添加 `Connection` 等 WSGI 禁止应用设置的 hop-by-hop 响应头，否则 Waitress 会拒绝请求。

| 事件 | 主要字段 | 用途 |
|---|---|---|
| `meta` | `request_id`、`mode`、`model` | 请求标识；`mode` 为 `guest/authenticated/analysis` |
| `status` | `stage`、`message` | 校验、分析、安全判断和生成进度 |
| `delta` | `text` | 可安全展示的正文增量 |
| `action` | `action`、`message` | 当前支持 `select_records`，前端应内联打开选档器 |
| `done` | `request_id`、`decision`、`source`、`summary`、`model` | 正常结束；人工支持时可含 `support_phone` |
| `error` | `request_id`、`code`、`message`、`retryable` | 流内失败；流已开始后 HTTP 状态通常仍为 200 |

认证对话和分析使用 decision-first JSON：服务端完整接收并解析安全决策后才释放正文，因此安全规则不会在已经输出一半正文后再反转。`source` 可能为 `safety_rule`、`selection_rule`、`faq` 或 `model`。

### 2.5 AI 错误与运行边界

HTTP 预校验错误结构：

~~~json
{
  "message": "explicit consent is required before sending record data",
  "error": {
    "code": "record_consent_required",
    "message": "explicit consent is required before sending record data",
    "retryable": false
  }
}
~~~

常见业务码：

| HTTP | code | 含义 |
|---:|---|---|
| 400 | `message_required`、`message_too_long` | 消息缺失或过长 |
| 400 | `invalid_history`、`invalid_summary`、`invalid_record_ids` | 上下文格式不合法 |
| 400 | `record_consent_required` | 带档案但未明确同意 |
| 400 | `records_required` | 分析未选择档案 |
| 400 | `record_not_confirmed`、`record_has_no_indicators`、`mixed_record_owners` | 档案不满足分析条件 |
| 403 | `login_required`、`regular_user_required` | 身份或角色不允许 |
| 404 | `record_unavailable` | 档案不存在、无权访问或已撤权 |
| 429 | `rate_limited` | 请求过于频繁，可稍后重试 |
| 503 | `ai_not_configured` | 兼容非流式 `/chat` 的 AI 未配置错误 |

流内还可能返回 `ai_not_configured`、`provider_rate_limited`、`provider_timeout`、`provider_http_error`、`provider_unavailable`、`provider_invalid_response`、`provider_empty_response` 和 `internal_error`。其中流式接口已经开始 SSE 后，`ai_not_configured` 也通过 `error` 事件返回，HTTP 通常仍为 200。

AI 限流是当前进程内的分钟桶：访客按 IP，登录用户按用户 ID；默认分别为 10/30 次。对话和分析计数，按需读取档案元数据不计数。多进程或多实例部署若需要全局限流，应改用共享存储。

默认连接、无数据读取和总截止分别为 5、30、60 秒。仅在首个模型内容到达前，连接中断或首轮 502/503/504 才自动重试一次；429、500、坏 JSON 不自动重放。客户端取消或断开时会关闭上游响应。

结构化日志字段为 `request_id/operation/mode/record_count/prompt_chars/first_delta_ms/total_ms/status/usage`。`first_delta_ms` 表示从请求生成器开始到首次可展示正文 `delta` 的耗时（认证对话和分析需先完成安全判断）；日志不记录问题、回复、指标值或密钥。

## 3. OCR 接口

所有 `/api/records/*` 接口都要求 JWT 普通用户权限。

### 3.0 解析、映射与安全边界

真实华为通用表格 OCR 当前返回 `parser_version=region-v2`。解析流程不依赖某一张固定报告模板：

1. 先识别响应中的每个 `table` 区域，并在区域内部独立收集行列坐标。不同表格即使都从第 0 行、第 0 列开始，也不会被合并或互相覆盖。
2. 每个区域分别识别语义表头；如果没有标准表头，则根据代码/名称/数值特征推断标签列和结果列。表头不再限制只能出现在最前几行。
3. 表格候选和表格之外的文本行并行解析。已经找到表格字段不会关闭文本兜底，因此双栏基本资料、无边框检验列表和英文 `label: value (reference ...)` 可以与表格共存。
4. 相同 `label + value` 去重时优先保留结构化表格来源；跨页仍按相同规则汇总。

候选映射只面向当前 `indicator_dicts` 中已经配置的代码、名称和别名：

- 精确代码/别名优先。纯英文短代码不允许任意子串命中，避免把其他项目误映射为 `TG`、`UA`、`ALT` 等短代码指标。
- 同一指标出现多个 OCR 候选时，先用该指标的数值与单位规则规范化后比较。规范值冲突、无效数值、低置信度或不安全匹配都会返回 `requires_review=true`，并使分数低于自动确认阈值。
- 数值必须只有一个明确数字。带逗号的 OCR 数字可能表示小数或千分位，服务端不会把 `5,6` 猜成 `56`；无法消歧时要求人工编辑。
- `μmol/L`、`µmol/L`、`umol/L` 以及常见 `mol·L⁻¹` 写法可按等价字形规范化，但系统不进行指标字典未声明的单位换算。
- 只有带明确 `reference/ref/参考范围` 标记的结果尾缀会在规范化时剥离；任意多数字文本仍会被拒绝。
- 姓名、性别、日期、医生、机构等报告元数据进入 `filtered_fields`；未配置或无法安全匹配的医疗项目进入 `unmatched_fields`。两者都不会自动写入健康指标。

因此，三份示例报告得到 10/9/8 个候选，只表示这些报告中出现的项目恰好已在当前字典配置。遇到字典外指标时，正确行为是保留原 OCR 字段供人工检查或先扩展字典，而不是猜成最相似的现有指标。

### 3.1 上传并解析

`POST /api/records/upload`，`multipart/form-data`，请求总大小上限 20 MiB。允许 `.pdf`、`.png`、`.jpg`、`.jpeg`、`.webp`。

新建档案模式：

- 必需：`file`、`exam_date`（`YYYY-MM-DD`）。
- 可选：`owner_id`、`institution_id`、`package_id`。
- 成功为 201，创建 `status=parsed` 的档案；报告成为当前报告，候选指标尚未入库。
- `ocr.pending_confirmation=false`，`attachment_id=null`。这里的 `false` 只表示它不是“既有档案的待确认附件”；新档案仍处于 `parsed`，必须调用 confirm 才成为 `confirmed`，也不能通过 `/ocr-pending` 恢复。

既有档案补传模式：

- 必需：`file`、`record_id`（内部正整数）。
- 成功为 200；体检日期、归属人、机构、套餐、当前状态、正式报告和指标在确认前保持不变。
- 新结果作为待确认附件，返回 `ocr.pending_confirmation=true` 和随机、不透明的 `attachment_id`。
- 再次上传会替换旧待确认版本；只有新事务成功后才删除旧暂存文件。

成功响应中的 `ocr` 包含 `provider`、`parser_version`、映射/未匹配/过滤数量、`candidate_mappings`、`unmatched_fields`、`filtered_fields`、`diagnostics`、`pending_confirmation`、`attachment_id`，以及 `pages_total/processed/succeeded/failed/empty/truncated`、`replacement_safe`。`mapped_count` 表示候选映射数量（可能含待复核项），不能作为已确认数量或业务主键。

候选可包含 `source`、`requires_review`、`conflict`、`conflict_values` 和 `value_error`。`diagnostics` 至少给出总字段、过滤、原始候选、唯一候选、未匹配、冲突和需复核数量。前端必须把 `requires_review=true` 或分数低于 `OCR_AUTO_CONFIRM_MIN_SCORE` 的行默认设为忽略，等待用户编辑和勾选。

### 3.2 确认候选映射

`PUT /api/records/{record_id}/confirm`

~~~json
{
  "attachment_id": "服务端返回的不透明版本标识",
  "ocr_update_mode": "replace_ocr",
  "confirmed_mappings": [
    { "indicator_dict_id": 2, "value": "5.6", "score": 1.0 },
    { "indicator_dict_id": 9, "value": "365", "score": 1.0 },
    { "indicator_dict_id": 10, "value": "忽略值", "ignored": true }
  ]
}
~~~

- 确认既有档案的待确认附件时，`attachment_id` 必需且必须与当前版本匹配。
- `confirmed_mappings` 可选；省略时仅自动确认达到 `OCR_AUTO_CONFIRM_MIN_SCORE`（默认 0.92）的候选。
- 同一 `indicator_dict_id` 最后一个有效、未忽略且值非空的映射生效；`ignored:true` 直接跳过，不会删除该 ID 之前已经出现的有效映射。
- 指标按字典 ID upsert，来源记为 `ocr` 并重新计算异常标记。
- 既有档案的 `ocr_update_mode` 可为 `replace_ocr` 或 `merge`。确认页面默认 `replace_ocr`，但服务端省略字段时按 `merge` 处理，以兼容旧客户端。
- `replace_ocr` 先写入本次已确认映射，再删除该档案中没有出现在本次映射里的旧 `source=ocr` 指标；`source=manual` 指标始终保留。为防止误清空，`replace_ocr` 至少需要一个有效确认映射，且只能用于既有档案的待确认附件。
- `replace_ocr` 还要求 `replacement_safe=true` 且本次快照中的每一个候选都在请求中显式确认；否则分别返回 `OCR_REPLACE_UNSAFE`（409）或 `OCR_REPLACE_REVIEW_REQUIRED`（400）。失败页、空结果页、页数截断和缺少新版完整性信息都不能作为安全替换依据。
- `merge` 只 upsert 本次映射，所有未出现在新报告中的旧 OCR 指标继续保留，适合真正的补充报告。
- 新建 OCR 档案在 `replacement_safe=false` 时必须显式传 `accept_incomplete_ocr=true`，否则返回 `OCR_INCOMPLETE_CONFIRMATION_REQUIRED`（400）；前端应先提示重新上传，并通过二次确认获取用户选择。
- 既有档案只有确认成功后才原子替换正式报告和上传人，随后清理旧正式报告。
- 成功响应的 `ocr` 返回 `update_mode` 和 `removed_ocr_count`，前端可据此提示本次删除了多少个旧 OCR 指标。

### 3.3 恢复或放弃待确认版本

- `GET /api/records/{record_id}/ocr-pending`：恢复当前候选和 `attachment_id`；没有待确认版本时返回 404。
- `DELETE /api/records/{record_id}/ocr-pending`：JSON 必须包含当前 `attachment_id`。成功后恢复补传前 OCR 快照，保留正式报告、状态和指标，并删除暂存文件。

### 3.4 OCR 并发控制

既有档案补传上传、补传版本确认、补传取消和档案删除共享基于 `ocr_raw_text` 快照的乐观并发控制。直接新建 OCR 档案使用 insert/commit，其首次确认没有待确认附件版本：

| HTTP | code | 处理方式 |
|---:|---|---|
| 409 | `OCR_ATTACHMENT_CONFLICT` | OCR 处理期间档案已变化；新文件已清理，重新上传 |
| 409 | `OCR_ATTACHMENT_STALE` | 确认/取消使用了旧 `attachment_id` 或快照；重新加载待确认结果 |
| 409 | `RECORD_DELETE_CONFLICT` | 删除时 OCR 快照已变化；刷新档案后重试 |

删除档案成功后同时清理正式报告和待确认报告。若删除先获得写锁，并发上传的 CAS 会失败并清理新文件；若上传先提交，删除返回 409。该协议用于避免旧页面覆盖新结果和遗留敏感孤儿文件。

## 4. 前端集成要求

- 打开 AI 侧栏时不要调用 `/api/ai/records`。只有手动引用、`select_records` 动作或档案列表智能分析需要加载档案。
- 用户消息应先显示，再按 `delta` 追加 AI 内容。收到 `error` 后保留用户消息和已有正文，并按 `retryable` 决定是否显示重试。
- 每次请求结束或取消后清空档案选择和同意；切换选择必须重新同意。
- AbortController 取消请求，发送中禁止重复提交。
- OCR 页面切换目标档案时应使未完成的上传、确认、取消和弹窗结果失效；响应写回前同时核对目标、上下文序号和 `attachment_id`。
- OCR 确认页显示 `parser_version`，允许编辑候选值，并把低置信度、冲突、非法值和 `requires_review` 候选默认设为忽略；不能把 `unmatched_fields` 当作已确认指标静默提交。
- 给既有档案确认时应显式提交 `ocr_update_mode`。界面默认 `replace_ocr` 并解释“仅清理旧 OCR、保留手工指标”，同时保留 `merge` 选项。
- 页面必须显式展示有效解析页数和 `replacement_safe` 警告；不完整的既有档案附件禁用替换，不完整的新建档案需二次确认并提交 `accept_incomplete_ocr=true`。
- 档案界面使用 `display_id`/`record_display_id` 展示，所有 API 参数继续使用整数 `id`。

## 5. 相关实现与验证

### 5.1 本地双通道 RAG

- 私人档案继续从 SQLite/GaussDB 按请求权限读取，趋势和异常由确定性代码计算；档案值、用户问题、聊天、OCR 原文和用户 ID 不进入 Qdrant。
- 公共知识使用 `BAAI/bge-small-zh-v1.5` 的 512 维本地向量和 Qdrant Local。访客过滤为 `public`，登录普通用户可额外检索 `authenticated` 医学白名单；管理员不能附带健康档案。
- 固定顺序为急症规则、选档/FAQ、同意与鉴权、档案事实、知识检索、DeepSeek 安全决策。无命中、索引缺失或模型失败会以 `no_match`、`unavailable` 或 `disabled` 降级。
- 语料在 prompt 中作为不可信 user 数据，模型返回的 grounding ID 只接受本次检索集合内编号。日志仅记录耗时、数量、稳定 source ID、分数和状态。
- `backend/rag_sources/manifest.json` 是精确 URL 与批准 SHA-256 清单。应用启动和请求不联网，只有 `scripts/rag_sync.py sync` 抓取；扫描 PDF 的本地 OCR 也只在该命令运行。
- 同步生成版本化 collection 并原子切换 `healthdoc_knowledge_current`。哈希变化进入运行目录隔离，审核后执行 `approve-change` 更新批准清单，再次 sync 才切换。
- `scripts/evaluate_rag.py` 运行 44 条中文黄金查询；36 条可回答查询的 Top-5 命中率必须至少 90%。
- 2026-07-15 本机热启动 50 次检索实测 p50 15.73 ms、p95 19.97 ms、最大 75.03 ms；模型冷启动单独计时，不计入热检索 p95。

本地运行目录 `backend/instance/rag/` 被 Git 忽略。Qdrant Local 仅支持单后端进程；设置 `RAG_QDRANT_URL` 后可切换独立服务而不改变业务调用方。

主要实现文件：

- `backend/app/ai/routes.py`
- `backend/app/ai/service.py`
- `backend/app/ai/rag.py`
- `backend/app/ai/ingestion.py`
- `backend/scripts/rag_sync.py`
- `backend/scripts/seed_rag_demo.py`
- `backend/app/records/routes.py`
- `backend/app/services/ocr.py`
- `backend/app/services/indicator_values.py`
- `backend/app/models/record.py`
- `frontend/src/components/AiAssistant.vue`
- `frontend/src/stores/aiChat.js`
- `frontend/src/views/RecordOcrUploadView.vue`
- `frontend/src/utils/aiStageLayout.js`
- `frontend/src/utils/recordDisplayId.js`

完整回归命令：

~~~powershell
Push-Location .\backend
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location

Push-Location .\frontend
npm audit --omit=dev
npm test
npm run build
Pop-Location

git diff --check
~~~

2026-07-15 当前本地基线为后端 161 项、前端 19 个测试文件共 110 项，前端生产构建、`pip check`、`pip-audit` 和 npm 依赖审计通过。RAG 专项覆盖稳定切分、提示注入隔离、grounding 白名单、URL/私网限制、哈希变化隔离、原子别名切换、无检索降级、急症跳过检索、owner scope 鉴权与合成数据幂等清理；OCR 专项继续覆盖独立表区域、表格/文本并行、保守映射和分页完整性保护。

此外，用三份不含真实健康信息的合成报告调用真实华为云通用表格 OCR 完成 smoke：中文有表头表格、中文无边框列表、英文行式布局分别准确映射 10/9/8 个已配置指标，冲突和需复核数量均为 0。该 smoke 不进入 CI，也不保存或输出真实凭据；CI 继续使用 Mock 与伪造响应验证接口契约。
