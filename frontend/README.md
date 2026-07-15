# 康康健健 HealthDoc 前端

Vue 3 + Vite 6 前端采用“公开门户 + 三角色工作台”的页面结构，完整提供公开认证、个人健康中心、机构服务、亲友、评论、AI、关怀模式和响应式交互。

## 环境与安装

要求 Node.js 20+、npm 10+。

```powershell
Set-Location .\frontend
npm ci
```

## 页面与路由

### 公开页面

| 路由 | 页面 |
|---|---|
| `/` | 项目介绍、核心能力、流程、隐私提示和关于我们 |
| `/login` | 带图片验证码的独立登录页 |
| `/register` | 普通用户注册或机构工作人员邀请码注册 |
| `/403` | 无权访问 |
| 未知路径 | 404 页面 |

登录用户访问登录/注册页会按真实角色返回所属工作台。

### 普通用户工作台

入口为 `/dashboard`：

| 路由 | 功能 |
|---|---|
| `/dashboard` | 健康总览 |
| `/timeline` | 自测与自动归档机构报告统一时间线 |
| `/measurements` | 六类日常测量新增、修改、删除 |
| `/trends` | 本人或授权亲友每日有效趋势 |
| `/reports` | 已发布机构报告列表 |
| `/reports/:id` | 报告和标准化指标只读详情 |
| `/friends` | 亲友关系与授权管理 |
| `/institutions`、`/institutions/:id` | 机构、封面、套餐与评论 |
| `/comments/mine` | 我的评论 |
| `/profile` | 账号、健康身份和个人健康资料 |

普通用户只能只读查看机构体检报告，不能创建、OCR 上传、补录、确认、修改或删除报告。

### 机构账号工作台

入口为 `/org/dashboard`：

- 机构运营总览；
- 体检报告生产；
- 机构资料维护；
- 机构相册管理；
- 体检套餐管理。

`/org/reports` 支持手工创建草稿或上传 OCR，编辑受检者快照和指标，身份校验通过后锁定，提交后自动归档，以及撤下已发布报告。身份不一致时拒绝锁定并保留可编辑草稿；内容离开草稿后界面和服务端都禁止修改。

### 系统管理员工作台

入口为 `/admin/dashboard`：

- 系统运营总览，仅显示账号、机构、邀请码、评论等非健康统计；
- 机构、套餐与相册管理；
- 邀请码管理；
- 账号停用、恢复和删除；
- 评论审核。

系统管理员没有报告、指标、自测、时间线或个人健康资料入口。

## 公共交互与基础能力

- 公开门户、独立登录/注册和图片验证码；
- 三角色路由、菜单和工作台隔离；
- 机构列表、详情、套餐、相册封面和加载失败占位；
- 亲友关系、只读授权和脱敏健康视图，不提供代传或代记能力；
- 用户评论、我的评论与管理员审核；
- AI 悬浮助手、SSE、取消/重试和逐请求同意；
- 关怀模式、AI 侧栏缩放和窄屏 overlay。

## 角色路由保护

路由元数据限定 `user`、`institution_admin`、`admin`：

- `user → /dashboard`
- `institution_admin → /org/dashboard`
- `admin → /admin/dashboard`

未登录访问工作台会跳转 `/login`；跨角色进入 `/403`。登录、刷新令牌或重新获取当前用户后，前端使用后端返回的实时角色重建导航。后端仍执行独立鉴权，前端守卫不承担唯一安全责任。

## 机构相册与用户机构页

机构账号和系统管理员页面继续支持 JPEG、PNG、WebP 上传、每张 5 MB、每机构 8 张、拖拽排序和删除。第一张是封面。普通用户列表和详情优先读取 `cover_image_url`，兼容 `logo_url` 与 `images[0].image_url`；无图片或加载失败时显示可访问占位，不影响资料、套餐和评论。

## 健康时间线、趋势和报告

- 时间线响应是后端专用 DTO，不在浏览器自行拼接跨权限数据。
- 趋势页面选择指标与查看对象后请求服务端每日有效序列，并继续使用 ECharts 按需模块和路由懒加载。
- 同日机构发布指标优先于自测；缺少机构指标时可回退到当天最后自测。
- 报告列表和详情完全只读，不显示健康身份码、创建机构账号、临时原文件或内部匹配用户 ID。
- 亲友视图不展示对方真实姓名、联系方式和健康资料。

## AI 智能助手

- 公开门户、登录和注册页显示访客 AI，仅回答公开导览。
- 普通用户工作台显示健康 AI；打开侧栏时不立即请求报告列表。
- 用户主动“引用报告”或收到 `select_records` 动作时才按需加载可分析报告。
- 报告选择支持精确选择与“该归属人的全部已发布报告”两种范围；检索阶段显示独立状态。
- 选择内容后需明确同意发送指标；选择和同意只对当前请求生效，完成、取消或改变选择后重置。
- 用户消息先显示，AI 使用 SSE 逐步拼接；发送中防重复，支持 AbortController 取消、失败保留和按 `retryable` 重试。
- 会话只保留在当前标签页 `sessionStorage`，退出或结束会话清理；悬浮球位置与侧栏宽度可存 `localStorage`，不包含健康数据。
- 机构账号和系统管理员工作台不显示健康 AI。
- 桌面端侧栏打开时主页面按比例缩放，最低 0.7；空间不足或移动端切换为有焦点管理的遮罩对话框。

## 关怀模式与响应式布局

关怀模式在桌面端以最高 1.12 倍统一放大文字、控件和间距，并根据可用宽度自动降低倍率。它与 AI 侧栏共用画布尺寸计算；安全宽度不足时回退为原响应式布局，避免导航换行和横向滚动。

主要实现：

- `src/components/AiAssistant.vue`
- `src/stores/aiChat.js`
- `src/utils/aiStageLayout.js`
- `src/stores/appearance.js`
- `src/layouts/WorkspaceLayout.vue`
- `src/components/InstitutionCoverImage.vue`

## API 模块

`src/api/http.js` 统一注入 JWT，并处理刷新流程。当前主要模块：

- `auth.js`、`users.js`：认证与账号；
- `profile.js`：本人个人资料；
- `health.js`：自测、时间线和趋势；
- `examReports.js`：用户只读报告；
- `org.js`：机构运营与报告生产；
- `admin.js`、`dashboards.js`：系统管理；
- `institutions.js`、`friends.js`、`comments.js`、`indicators.js`：延续功能；
- `ai.js`：报告选择和 SSE AI。

## 开发、构建与预览

```powershell
npm run dev
npm test
npm run build
npm run preview
```

- 开发地址：<http://127.0.0.1:5173>
- production preview：<http://127.0.0.1:4173>
- Vite 默认将 `/api` 与 `/uploads` 代理到 <http://127.0.0.1:5050>

根目录也提供 `scripts/start-frontend-dev.ps1`、`scripts/start-frontend-prod.ps1` 和完整前后端启动脚本。

## 测试与依赖审计

```powershell
npm test
npm run build
npm audit --omit=dev
```

当前 14 个测试文件、79 项测试覆盖认证/角色映射、公开与工作台路由、机构封面、AI 按需加载、RAG 检索状态与流式交互、关怀模式/布局、账号注册载荷、图表外观和 HTTP 行为。生产构建与依赖审计结果见 [`../项目文档/测试报告.md`](../项目文档/测试报告.md)。
