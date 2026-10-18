# 食品包装预审后端

FastAPI + SQLite + 独立 Worker，包含规划中的 15 个 API 路径（16 个 HTTP 操作）。上传、OCR、人工复核、版本化重检、问题决定、JSON/可打印 HTML 报告均已实现。`audit_engine/` 迁自原型，API 请求只排队，OCR 和规则在 Worker 运行。

## Windows 本地启动

在仓库 `src/` 下运行 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `src/.env`：填写 `ACCESS_PASSCODE` 和独立的随机 `SESSION_SECRET`（可用 `python -c "import secrets; print(secrets.token_hex(32))"` 生成）。两者为空时拒绝启动。配置统一经过 `app/config.py`；环境变量优先于 `.env`，`src/.env` 优先于仓库根 `.env`。API 和 Worker 必须使用同一份配置。

终端一：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

终端二（同样在 `src/`）：

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

`python -m app.worker --once` 只领取一个 job，便于调试。无复核项时 extract 会另排 check job；持续运行的 Worker 会继续处理。SQLite 使用 WAL、外键和 30 秒 busy_timeout；只开一个 API worker 和一个任务 Worker。任务锁每 30 秒刷新，超过 5 分钟未刷新时重新排队。

默认 `OCR_MODE=cache`，无需外部密钥：登录后 `GET /api/samples`，以 multipart 表单 `source=sample&sample_id=4260290260010` 创建演示任务。缓存模式拒绝新图，避免把缓存结果当作实时识别。

实时识别须设置 `OCR_MODE=live`、`BAIDU_API_KEY`、`BAIDU_SECRET_KEY`。每张图调用一次高精度全文和一次表格 V2；每任务 1–3 张。额度按实际 REST 调用持久化计数，`DAILY_OCR_LIMIT=100`，UTC 零点切日。创建任务时已知额度不足返回 `429/OCR_QUOTA`；已排队任务仍由 Worker 在每次调用前原子检查，耗尽后任务为不可重试的 `FAILED/OCR_QUOTA`。超时自动重试最多一次，成功的原始响应保留并用于重试，避免重复调用。

## 前端对接

除 `/api/health` 和 `/api/auth/login` 外，全部 API 和图片都校验签名会话，未登录返回 401。登录 JSON 为 `{"passcode":"..."}`，会话有效 12 小时，HttpOnly、SameSite=Lax，HTTPS 下设置 Secure。登录失败按 IP 限速，创建任务限 5 次/分钟；跨站写入被拒。前端开发时代理 `/api` 到本机后端，以同源 cookie 调用。

`GET /api/health` 的 `ocr_configured` 只表示两个配置值存在，不证明密钥、权限、额度有效。上传用 Pillow 实际解码、重编码去 EXIF，PNG/JPG 每张默认不超过 10 MB、4000 万像素，文件名由 UUID 生成；一次批量全部校验成功才落盘。文件和原始 OCR 响应在 `DATA_DIR`，不作为静态目录。

复核调用顺序：

1. `GET /api/audits/{id}/review-items` 返回字段 `items`、表格诊断 `table_items`、`document_version`、文档/产品 ID、图片、阈值。`images[].fileName` 是服务端存储名，`display_name` 是原上传名；提交复核时使用存储名和净化后图片的 SHA-256。
2. `POST /corrections` 带 `base_version`、`image_id` 及旧版复核 JSON 字段，按原值和证据 ID 校验。每次提交只复核一张图的字段，只能处理当前待复核项，不能任意修改已确认字段。旧文档保持不变，成功生成新版本；过期提交返回 `409/VERSION_CONFLICT`。
3. 营养表行列确认还需 `table_checked_all=true`，`tableDecisions` 中给出 `tableId`、`originalCode`、`sourceEvidenceRefs`、`action=confirm_complete`。应先确认全部字段，再确认表格完整性；同一请求中也可同时提交。
4. 完全漏表时使用 `manual_tables` 人工整表录入。必须对应当前图片尚未解决的诊断，携带其原始 code 和证据，不能覆盖另一张表。下面是 `manual_tables` 中一项的格式，外层仍需上述复核身份字段和 `table_checked_all=true`：

```json
{
  "table_id": "manual-table-1",
  "original_code": "TABLE_V2_NOT_FOUND",
  "source_evidence_refs": [],
  "basis": "per_100g",
  "basis_raw": "每100g",
  "rows": [
    {"nutrient": "protein", "amount": "3.2", "unit": "g", "nrv_percent": "5"}
  ]
}
```

`original_code/source_evidence_refs` 取接口实际值。若诊断已有 `table_id`，沿用它；没有则提供新的任务内唯一 ID。每份基准额外提供大于零的 `serving_g`。应按原图录入全部行，数值为十进制字符串；营养素使用 `energy/protein/fat/saturated_fat/carbohydrate/sugar/sodium/dietary_fiber` 等引擎标识。人工证据不伪造像素框，定位沿用已有来源。

5. `POST /resume` 使用最新版本排 check job；仍有待复核项返回 `409/REVIEW_PENDING`，错误内带 `review_pending` 数量。重复 resume 返回同一活动 job。前端轮询任务，`stale=true` 时提示结果过期。`GET /fields?version=0` 可查询原始 OCR，字段 `version` 表示最后变更版本。

问题接口支持 status/layer/rule_id 过滤；PUT decision 仅对当前检查版本记录确认或误报。`GET /report?format=json|html` 的字段对应实际检查版本，附修正历史、未检查项、人工确认字段、规则版本与条款状态；HTML 内嵌缩略图，可用浏览器打印。公开样本列表与报告保留 Open Food Facts 链接和 CC BY-SA 来源标注。

平台新增字段使用 `snake_case`；复核请求中源自旧 `apply_decisions` 格式的字段保留 `camelCase` alias（如 `originalRaw`、`documentId`）。这是原型 JSON 的兼容边界，前端以 OpenAPI 生成的字段名为准。底层文档只有 ready/needs_review/missing；API 的 human_confirmed 从 manual 证据投影得出。

## 可选视觉兜底与审核范围

默认 `VLM_ENABLED=0`。启用时须同时设置 HTTPS `VLM_BASE_URL`、`VLM_MODEL`、`VLM_API_KEY`。默认魔搭端点 `https://api-inference.modelscope.cn/v1`，经 OpenAI SDK 的兼容接口发送图片和营养表转录提示；图片长边缩放至不超过 2000 像素，坐标由服务端还原。模型无工具权限，严格校验 JSON，候选一律待人工确认，仅允许替换带 needs_vision 诊断的表。原 OCR 为 v0，视觉结果另存 v1；review-items 的 `ocr_fields` 可供前端并排显示原值。

只对单图上传任务启用视觉兜底，多图使用人工路径。额度 `DAILY_VLM_LIMIT=20`，默认超时 60 秒、不自动重试。超时、坏响应或额度耗尽时保留人工路径，任务进入 WAITING_REVIEW，`error` 记录降级原因。前端上传页须按规划显示“识别时可能调用外部视觉模型”。

门禁按规则依赖生效：未确认值不会进入计算，独立已确认字段仍可检查。每100mL、多张营养表、无法计算的配料顺序等明确返回 NOT_CHECKED/PARTIAL。营养表不推断为完整；GB 7718 只覆盖有限字段有无与可计算的配料顺序。条款种子全部待核验，仅 `verified=true` 且有条款号时展示；课程要求单独标注。AI 预审完成不等于人工签核。

前端构建到 `frontend/dist` 后由 FastAPI 同端口托管，支持 SPA 回退（API 404 不回退 HTML）。可用 `FRONTEND_DIST` 指定构建目录。仓库当前未提供实际前端构建。本机联调完成后可运行 `cloudflared tunnel --url http://127.0.0.1:8000`，再验收 HTTPS 登录 cookie 与手机网络访问。

## 验证与维护

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q app audit_engine scripts
.\.venv\Scripts\python.exe -m scripts.export_openapi --output data/openapi.json
.\.venv\Scripts\python.exe -m scripts.list_rules
```

包含确定性规则迁移、32g→3.2g 后重算/旧版保留、401、409、幂等、原子领取/崩溃恢复、真实解析入口、视觉 SDK MockTransport、整表手工降级、上传回滚/限额与独立 API/Worker 进程测试。历史 OCR 测试中依赖未提交图片或 `market_labels_50` 缓存的用例明确跳过；不能据此宣称真实 OCR 准确率。本地没有配置外部服务密钥，百度/魔搭真实调用及公网端到端仍需部署环境验收。

`httpx2/httpcore2` 来自 D0 环境的 `pip freeze`，随当前 Starlette 1.8.0 测试依赖进入锁定清单，业务代码未主动选用它们；已检查本机 Starlette 依赖元数据/TestClient 导入并运行测试。没有据此确认其发行方身份。新增 OpenAI SDK 使用的是另行锁定的 `httpx/httpcore`，两套名称互不替代。

path-guard 只允许后端 PR 修改 `src/`。`shared/openapi.json` 由负责人生成并推到 main，本分支不提交它；本地契约请导出到被忽略的 `src/data/openapi.json`。`app/data/rule_clauses.seed.json` 是源码种子，提交时须对这一文件单独 `git add -f`，其余 `data/` 文件均不提交。

删除单个任务前停止 API/Worker 并备份 DATA_DIR。脚本默认只预览；明确指定同一个 UUID 才删除该任务库记录、净化图片和原始 OCR 响应，其他任务不受影响。运行中的任务拒绝删除；停止 Worker 后应先恢复超过 5 分钟的锁再删除。

```powershell
.\.venv\Scripts\python.exe -m scripts.delete_audit <任务UUID>
.\.venv\Scripts\python.exe -m scripts.delete_audit <任务UUID> --confirm <同一个任务UUID>
```

目录：`app/` 平台服务，`audit_engine/` 纯 Python 原型引擎，`samples_public/` 可提交公开缓存，`scripts/` 运维与契约工具，`tests/` 验证，`data/` 本地运行数据（不入库）。
