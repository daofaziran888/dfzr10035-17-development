# 网关工坊 · API接口采集与数据网关平台

面向第三方API接入注册、接口鉴权编排、限流熔断、响应解析、schema映射、结果缓存、重放恢复与交付订阅的API接口采集与数据网关平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/imports.py   字段映射文件导入/确认/更正/交付回查接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/parse_import.py  字段映射导入领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   ├── app/domain_seed.py       样本、历史映射版本、旧缓存与旧任务种子
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 接口提供方 | `api_provider` | API提供方 | 提供方编号、提供方名称、认证方式 |
| 鉴权配置 | `auth_profile` | 鉴权方案 | 方案编号、提供方、鉴权类型 |
| 接口定义 | `endpoint_def` | 接口端点 | 端点编号、提供方、请求路径 |
| 限流熔断 | `rate_limit` | 限流策略 | 策略编号、目标端点、QPS上限 |
| 请求批次 | `request_batch` | 调用批次 | 批次编号、关联端点、参数模板 |
| 响应解析 | `response_parse` | 解析规则 | 规则编号、目标端点、JSON路径 |
| Schema差异 | `schema_diff` | 差异记录 | 记录编号、关联端点、变更字段 |
| 结果缓存 | `data_cache` | 缓存策略 | 策略编号、目标端点、缓存TTL |
| 重放队列 | `retry_queue` | 重放任务 | 任务编号、失败端点、失败原因 |
| 数据投递 | `data_delivery` | 投递管道 | 管道编号、数据来源、目标地址 |
| 订阅管理 | `subscriber` | 订阅方 | 订阅编号、订阅方、数据主题 |
| 接口监控 | `api_monitor` | 监控指标 | 指标编号、目标端点、监控类型 |
| Webhook路由 | `webhook_router` | 回调规则 | 规则编号、触发事件、目标URL |
| 传输加密 | `data_encrypt` | 加密配置 | 配置编号、数据范围、加密算法 |
| 调用日志 | `call_log` | 调用记录 | 记录编号、接口端点、请求参数 |
| 数据版本 | `data_version` | 数据快照 | 版本编号、数据来源、行数统计 |
| 管道审计 | `pipeline_audit` | 审计记录 | 记录编号、管道名称、操作人 |
| 治理报告 | `govern_report` | 治理报告 | 报告编号、评估范围、合规指标 |
| 增量追踪 | `incr_tracker` | 增量标记 | 标记编号、目标端点、增量字段 |
| 抽样校验 | `sample_check` | 抽样计划 | 计划编号、目标端点、抽样比例 |
| 用量计费 | `cost_account` | 用量账单 | 账单编号、提供方、计费周期 |
| 任务调度 | `task_schedule` | 调度计划 | 计划编号、关联端点、Cron表达式 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据采集、爬虫与RPA

## 响应解析：字段映射文件导入

响应解析模块（`/response_parse` 页面底部）支持从同一端点的字段映射文件导入解析配置，
不再需要逐条登记规则。链路如下：

```text
上传映射文件 → 同端点响应样本预检 → 确认启效 → 刷新结果缓存 → 同步投递待办 → 实际投递取列
                     ↓ 同名冲突
              保留全部路径与样本原值 → 人工更正 → 产出新映射版本
```

- 映射文件支持 JSON（`{"endpoint": "...", "mappings": [{"field","path","type","default"}]}`）
  或 CSV（`field,path,type,default`，可用 `#endpoint: ENDP-...` 声明端点）。
- 预检只使用同一端点的响应样本核对；路径不存在、类型不符都记为“未验证”，
  未验证字段不会覆盖已生效定义。
- 同名冲突（例如 `$.gross` 与 `$.net` 同时映射到 `total`）保留两个路径与样本原值，
  冲突处理结论记录在 `schema_diff` 与冲突记录里，可由交付版本回查。
- 每次启效生成不可变的映射版本与缓存条目；历史任务只使用领用时领用的快照，
  不受后续确认/更正影响。
- 同一文件重复提交只登记一次，也只会启效一次；确认中断后重提同一文件从断点恢复，
  并重新核对已确认范围。

主要接口（服务启动后可直接调用）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/parse-imports/state` | 样本、导入、映射版本、缓存、待办、冲突、交付对比 |
| POST | `/api/parse-imports/upload` | 上传映射文件并预检 |
| POST | `/api/parse-imports/{id}/confirm` | 确认启效（`interrupt_after` 可模拟中断） |
| POST | `/api/parse-imports/{id}/conflicts/{cid}/resolve` | 更正同名冲突 |
| POST | `/api/parse-imports/deliveries/run` | 按缓存结果运行一笔投递 |
| GET | `/api/parse-imports/deliveries/{id}` | 回查交付使用的字段来源与冲突结论 |
| GET | `/api/parse-imports/deliveries/compare` | 比较各交付的字段来源 |

种子数据：`ENDP-CSV-0001` 的响应样本为 `{"gross":100,"net":80,"currency":"CNY"}`，
旧任务 `TASK-CSV-LEGACY-01` 领用的是 `total <- $.gross`（版本 1），
旧缓存 `CACHE-CSV-V1` 仍在服务该任务。
