# HealthDoc 第六轮 Agent 升级实施说明（schema v11）

> 实施日期：2026-07-29。本文是从“AI 智能问答客服”升级到可审计、可确认、可恢复 Agent 的代码、接口、数据、安全、测试和部署基线。

## 1. 最终架构决策

现有生产机只有 4GB 内存，因此不常驻 0.6B—7B 通用小语言模型。系统采用四类职责边界：

- Python/SQL 领域服务负责数值计算、权限、预约容量、状态转换、幂等写入和结果核验。
- `BAAI/bge-small-zh-v1.5`、中文词法 BM25 和 RRF 负责公共知识召回与融合；混合索引由功能开关控制。
- DeepSeek V4 Flash 以非思考模式选择 Pydantic 类型化工具、组织多步计划并解释证据。
- 确定性紧急风险规则在模型调用前执行；命中后终止普通 Agent 流程并提示拨打 120。

预留 `AGENT_ROUTER_ENABLED`，但本轮不下载、不部署通用本地小 LLM。未来有独立推理节点和离线评测结果后才允许启用。

## 2. Agent 执行模型

后端新增 `app/agent/`，使用 LangGraph 1.2.10。当前图的稳定路径是：

`输入校验 → 紧急风险闸门 → DeepSeek 工具规划 → 动态工具白名单 → 类型化参数校验 → 权限检查 → 工具执行 → 证据包 → 回复或确认草稿 → 用户决定 → 重新校验 → 幂等提交 → 回执`

每轮最多 6 次模型决策和 10 次工具调用。工具参数采用 Pydantic `extra=forbid`，DeepSeek 不能调用未注册工具、SQL、任意 URL 或内部文件。

### 2.1 SSE 事件

`POST /api/agent/threads/{thread_id}/runs/stream` 使用：

- `meta`：线程、运行标识；
- `plan`：当前规划阶段；
- `tool_started` / `tool_completed`：脱敏工具轨迹；
- `evidence`：允许展示给当前用户的工具结果；
- `approval_required`：待确认 Action、摘要和失效时间；
- `status`：审批或提交阶段；
- `delta`：最终回答增量；
- `done` / `error`：稳定终态。

### 2.2 类型化工具

只读工具：

- `list_reports`
- `get_report_facts`
- `compute_indicator_trend`
- `search_institutions`
- `compare_packages`
- `check_availability`
- `get_appointment_status`

草稿工具：

- `create_booking_draft`
- `create_cancellation_draft`
- `create_waitlist_draft`
- `create_support_handoff_draft`

草稿工具没有副作用。确认后才调用共享预约领域入口或创建人工客服工单；Action ID 同时作为幂等键。预约、整组取消和候补继续使用原页面相同的容量、授权、身高体重、预约须知、邮箱和状态规则。

## 3. 数据与隐私

schema v11 新增：

- `agent_threads`：用户线程和 AES-GCM 加密状态；
- `agent_runs`：运行终态、模型、提示版本和用量；
- `agent_tool_events`：只记录字段名、结果键、耗时和状态；
- `agent_pending_actions`：AES-GCM 加密操作参数及可展示摘要；
- `agent_action_executions`：唯一 Action/幂等键和执行回执；
- `support_handoffs`：人工工单生命周期；
- `oauth_clients`、`oauth_authorization_codes`、`oauth_access_tokens`、`oauth_refresh_tokens`：外部 MCP OAuth 边界。

生产启用 Agent 时必须显式设置至少 32 字符的 `AGENT_DATA_ENCRYPTION_KEY`。数据库不保存明文 OAuth token；只保存 SHA-256 摘要。工具日志不保存提示词、完整参数、报告正文、健康身份码或联系方式。

线程默认闲置 24 小时，操作草稿默认 10 分钟。清空线程会覆盖加密状态并使待确认草稿失效；生产 `healthdoc-agent-cleanup.timer` 每小时执行清理，重启期间错过的周期由 systemd `Persistent=true` 补跑。

## 4. 安全规则

- Agent 只向普通用户开放；机构和管理员不能获得个人健康 Agent 上下文。
- 每次档案读取重新查询本人及 `auth_status=true` 的亲友授权。
- 预约代办使用独立的 `booking_auth_status`，不能由档案查看权推导。
- DeepSeek 最终回复只能解释工具证据，不参与数值计算和数据库写入。
- 胸痛、呼吸困难、疑似卒中、严重过敏、意识丧失、抽搐、严重创伤和自伤风险先由确定性规则处理。
- 预约、取消、候补和人工工单必须在 HealthDoc 第一方界面确认。
- 提交时重新检查当前用户、授权、套餐版本、容量、预约组状态和草稿有效期。
- 重复确认返回原回执，不产生第二个业务对象。

## 5. 混合 RAG

`RAG_HYBRID_ENABLED=1` 时，索引脚本创建新的指纹集合并在全部写入成功后原子切换 alias：

- Dense：`BAAI/bge-small-zh-v1.5`，默认各取 24；
- Sparse：`Qdrant/bm25`，中文字符及双字词稳定切分，各取 24；
- Fusion：Qdrant RRF，默认合并 12；
- 最终按指标命中、权威发布方和每来源最多两段进行确定性重排。

关闭开关时继续读取原 Dense 集合。私人档案、聊天状态和预约信息不得写入 Qdrant。

## 6. OAuth 与 MCP

MCP 使用 Python SDK 2.0.0 的无状态 Streamable HTTP，默认监听 `127.0.0.1:5051/mcp`。Apache 代理 `/mcp`；MCP 进程不直接打开 Qdrant Local，而是通过带共享密钥的 loopback Flask 工具网关调用同一套类型、权限和领域服务。

OAuth 支持：

- `/.well-known/oauth-protected-resource/mcp`
- `/.well-known/oauth-authorization-server`
- `POST /oauth/register`
- `GET/POST /oauth/authorize`
- `POST /oauth/token`
- `POST /oauth/revoke`

动态注册客户端状态固定从 `pending` 开始，管理员批准后才能授权。只支持 Authorization Code + PKCE S256 和公开客户端；redirect URI 必须精确匹配，只接受 HTTPS 或 loopback HTTP，禁止通配符、fragment 和用户信息。

Access Token 有效期 10 分钟；Refresh Token 30 天并逐次轮换。旧 Refresh Token 重放会撤销整个 token family。MCP 每次请求验证 token、客户端、用户、scope、audience、Host 和 Origin。

Scopes：

- `knowledge.read`
- `catalog.read`
- `records.read`
- `booking.read`
- `booking.write`
- `support.write`

MCP 的写工具只返回第一方 `/agent-actions/{action_id}` 确认链接。

## 7. 可观测性

设置 `OTEL_ENABLED=1` 后，通过 OTLP/HTTP 导出 Flask、HTTP 客户端及下列 Agent spans：

- `agent.run`
- `model.generate`
- `tool.execute`

生产 span 只包含运行 ID、线程 ID、工具名、模型和提示版本等低敏元数据，不采集提示词、报告内容、工具参数或结果。

## 8. 前端

前端启动后读取 `/api/agent/capabilities`：服务端 Agent 已启用时，普通用户顶栏入口打开新的 Agent 控制台；未启用或请求失败时继续使用旧 `AiAssistant`。`VITE_AGENT_ENABLED=false` 只作为紧急前端回退：

- 展示计划、工具执行状态、证据、确认卡片和提交回执；
- 线程状态由服务端加密持久化，浏览器只保存当前 thread ID；
- 刷新页面可恢复线程和待确认操作；
- MCP 授权使用 `/oauth-consent`；
- 外部写操作使用 `/agent-actions/:id` 完成第一方确认。

## 9. 配置

```dotenv
AGENT_ENABLED=0
AGENT_WRITE_ENABLED=0
AGENT_ROUTER_ENABLED=0
AGENT_DATA_ENCRYPTION_KEY=
AGENT_THREAD_TTL_HOURS=24
AGENT_ACTION_TTL_SECONDS=600
AGENT_MAX_TOOL_CALLS=10
AGENT_MAX_MODEL_CALLS=6

RAG_HYBRID_ENABLED=0
RAG_SPARSE_MODEL=Qdrant/bm25
RAG_DENSE_PREFETCH_K=24
RAG_SPARSE_PREFETCH_K=24
RAG_FUSION_K=12

OAUTH_ENABLED=0
OAUTH_ISSUER=
MCP_ENABLED=0
MCP_RESOURCE_URL=
MCP_INTERNAL_KEY=

OTEL_ENABLED=0
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
OTEL_SERVICE_NAME=healthdoc-backend
```

生产启动门禁要求：Agent 有独立加密密钥；OAuth issuer 必须是 HTTPS；MCP 必须同时启用 OAuth 且内部密钥至少 32 字符。

## 10. 测试、评测与发布

自动测试覆盖会话密文、跨用户隔离、紧急风险短路、类型化读工具、人工工单确认、预约领域服务复用、重复确认幂等、PKCE、精确 redirect、授权码一次性、Refresh Token 轮换/重放和内部 MCP 验签。

真实 DeepSeek 路由评测：

```powershell
$env:HEALTHDOC_EVAL_ACCESS_TOKEN="专用评测账号的短期令牌"
python backend/scripts/evaluate_agent.py --confirm-live --max-cost-usd 10
```

脚本生成并执行 200 个非紧急路由场景，拒绝 Mock，且只有预期工具执行成功才计为通过；达到 95% 才成功。根据返回 token 用量执行 10 美元硬上限。`--workers` 可在 openGauss 环境受控并发；本地 SQLite 建议 `--workers 1`。紧急风险另由确定性测试覆盖，不能为追求“真实模型调用”而绕过安全短路。

发布顺序：

1. 全量测试、Vite build、迁移脚本和 shell/Apache 语法检查；
2. 备份 openGauss、上传、RAG、环境文件和当前 release；
3. 执行 `migrate_schema_v11.py`；
4. 发布后端、前端和 systemd；
5. 保持所有新开关关闭，验证旧 `/api/ai`；
6. 依次灰度开启 Agent 只读、写草稿、混合 RAG、OAuth/MCP 和 OTel；
7. 任一门禁失败恢复数据库、环境和上一 release。

当前仅服务器 IP 试运行时，自签名 IP-SAN 证书只允许显式信任该证书的测试客户端使用；不视为正式可信公网 OAuth。正式开放必须绑定域名和受信任证书。

### 10.1 真实模型验收结果

2026-07-29 使用 `deepseek-v4-flash` 完成 200 个真实场景：8 类目标各 25 个，200/200 工具选择与执行成功，准确率 100%。正式轮使用 574843 input tokens、50776 output tokens，按评测器上限价格估算 1.978409 美元，耗时 190.98 秒。只有 `tool_completed.ok=true` 才计为通过；前期调优和正式轮合计未触发 10 美元预算上限。
