# 食品包装预审后端

FastAPI + SQLite + 独立 Worker，包含规划中的 15 个 API 路径（16 个 HTTP 操作）。上传、OCR、人工复核、版本化重检、问题决定、JSON/可打印 HTML 报告均已实现。`audit_engine/` 迁自原型，API 请求只排队，OCR 和规则在 Worker 运行。

## 现行上传流水线：VLM 分区优先（2026-10-19）

现行实现位于 `app/services/zoning.py` 和 `app/services/regional_ocr.py`，覆盖下文历史“视觉转录兜底”流程。每张图先请求一次 VLM 仅返回区域类别和像素框；严格拒绝携带字段转录值、无效坐标或不完整分区。随后裁剪区域调用 OCR：普通文字区一次 accurate，营养表区 accurate + table。accurate 的概率用于96%门槛，Table V2 仅提供结构，不编造置信度。

分区失败自动降级整图 accurate + table，不再用 VLM 二次转录。OCR与结构校验正常的输入直接进入规则检查；结构问题保留表级诊断，避免将清晰字段全部降为待复核。单位不符、重复营养项、低置信度、缺少概率、无法解析或跨区/跨图冲突仍拦截。规则本身与标准覆盖范围未变。

新文档 `_platform.pipeline=vlm_zones_ocr_v1`，包含区域和降级路径元数据。`review-items` 增加 pipeline/extraction 以兼容展示；旧任务默认 legacy_ocr_vision，不重解释旧记录。原始OCR响应和布局计划保存于 `DATA_DIR/ocr/<task_id>/`，z1/r1短文件名适配Windows路径长度，记录内保留完整指纹，重试不会混用旧整图缓存。创建任务仅预检最小OCR额度，分区后按实际未缓存请求数预检，再逐次原子计数。新VLM分区请求无自动重试；失败直接走整图OCR。

## 当前本机工作台（2026-10-19）

默认 `LOCAL_ONLY=1`，本机访问不再要求口令或 Cookie。根目录运行 `./start-local.ps1` 启动真实 API 和 Worker，浏览器打开 http://127.0.0.1:5182/ 直接上传；仅监听127.0.0.1，保留Origin校验，拒绝非本机连接与异常Host。已有 `.env` 模型配置保持不变。前端不提供缓存样本或虚构数据入口，外部识别由用户实际上传触发。

下文历史认证流程只适用于显式 `LOCAL_ONLY=0` 的兼容部署；当前本机版本不需要 ACCESS_PASSCODE / SESSION_SECRET。普通开发启动时使用 `--host 127.0.0.1 --no-proxy-headers`。本轮验证记录见 `frontend/VALIDATION.md`。

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

实时识别须设置 `OCR_MODE=live`、`BAIDU_API_KEY`、`BAIDU_SECRET_KEY` 和有效的 `VLM_BASE_URL/VLM_MODEL/VLM_API_KEY`；live 模式自动启用视觉兜底，视觉配置不完整时拒绝启动。cache 模式无需外部配置。每张图调用一次高精度全文和一次表格 V2；每任务 1–3 张。额度按实际 REST 调用持久化计数，`DAILY_OCR_LIMIT=100`，UTC 零点切日。创建任务时已知额度不足返回 `429/OCR_QUOTA`；已排队任务仍由 Worker 在每次调用前原子检查，耗尽后任务为不可重试的 `FAILED/OCR_QUOTA`。超时自动重试最多一次，成功的原始响应保留并用于重试，避免重复调用。

## 前端对接

除 `/api/health` 和 `/api/auth/login` 外，全部 API 和图片都校验签名会话，未登录返回 401。登录 JSON 为 `{"passcode":"..."}`，会话有效 12 小时，HttpOnly、SameSite=Lax，HTTPS 下设置 Secure。登录失败按 IP 限速，创建任务限 5 次/分钟；跨站写入被拒。前端开发时代理 `/api` 到本机后端，以同源 cookie 调用。

`GET /api/health` 的 `ocr_configured` 只表示两个配置值存在，不证明密钥、权限、额度有效。上传用 Pillow 实际解码，先按 EXIF 方向归一化像素再重编码去 EXIF，PNG/JPG 每张默认不超过 10 MB、4000 万像素，文件名由 UUID 生成；一次批量全部校验成功才落盘。文件和原始 OCR 响应在 `DATA_DIR`，不作为静态目录。

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

cache 模式默认 `VLM_ENABLED=0`；live 模式必须配置 HTTPS `VLM_BASE_URL`、`VLM_MODEL`、`VLM_API_KEY`，自动启用视觉兜底。先完成全部图片的 OCR 并合并，再选择有 `needs_vision` 诊断或待复核字段证据的图片；某个字段全局缺失时选择全部图片。每张选中图片独立调用，营养表和四个基础字段（商品名、净含量、配料、过敏原）一并转录；在单图文档中导入后才加图片 ID 前缀，避免多图证据 ID 冲突。内置样本始终不调用模型。

模型无工具权限，返回 JSON 必须通过严格校验，不修补坏响应。已有表只允许替换 `needs_vision` 表；已就绪表不会因其他字段触发视觉而被替换。OCR 与视觉一致时保留 OCR 原状态；数值按 Decimal 比较，单位支持克/g等别名。不一致时保留 OCR 活动值并降为待复核；仅视觉识别到的值为待复核。`vision`、`agreement`、`candidates` 同时出现在 fields 和 review-items 中；视觉候选必须通过人工决定才可成为人工确认值。多图同字段冲突保留第一个值，所有候选及证据进入复核队列。

基础字段先按归一化文本相似度（阈值 0.72，最多连续 12 条 OCR 行）匹配 OCR 框；匹配失败使用模型估计框。营养表视觉字段始终使用整表框。所有坐标均映射到净化后图片的像素尺寸；前端应对视觉估计位置作明确标注。

原始 OCR 存 v0，视觉合入另存 v1，人工修正继续追加版本。视觉对照数据存在文档顶层 `_platform` 元数据中，与事实字段分开；不会向严格的 `FieldValue` 添加未知属性，也不进入规则计算。修正和多图合并保留这些元数据。review-items 的 `ocr_fields` 仍提供 v0 投影；`GET /fields?version=0` 可回读原始 OCR。

每日视觉上限默认 `DAILY_VLM_LIMIT=100`。超时默认 60 秒，仅超时自动重试一次，每次发送前独立计数；坏 JSON、鉴权失败等不重试。失败逐图降级到人工复核，写入任务 `warnings[]`（含 `image_id`），不写入 `error`，不使任务 FAILED。旧数据库自动补充 warnings 列，并迁移非失败任务中历史 VLM 警告。上传页须显示“识别时可能调用外部视觉模型”。

模型可以给出 `no_nutrition_table=true`，但只会作为 `table_items[].vision_no_table` 提示；门禁仍等待人工。复核请求 `no_table_images: [image_id]` 只允许确认当前指纹匹配图片尚未解决、未关联已检测表的诊断，不能删除已有表，不能与整表录入/整表确认混用。确认后允许 resume；如果整个任务仍没有营养表，营养计算仍为 NOT_CHECKED/PARTIAL。相关人工决定保存在修正历史和人工证据中。

门禁按规则依赖生效：未确认值不会进入计算，独立已确认字段仍可检查。每100mL、多张营养表、无法计算的配料顺序等明确返回 NOT_CHECKED/PARTIAL。营养表不推断为完整；GB 7718 只覆盖有限字段有无与可计算的配料顺序。条款种子全部待核验，仅 `verified=true` 且有条款号时展示；课程要求单独标注。AI 预审完成不等于人工签核。

前端构建到 `frontend/dist` 后由 FastAPI 同端口托管，支持 SPA 回退（API 404 不回退 HTML）。可用 `FRONTEND_DIST` 指定构建目录。前端构建与页面实现由前端会话交付。本机联调完成后可运行 `cloudflared tunnel --url http://127.0.0.1:8000`，再验收 HTTPS 登录 cookie 与手机网络访问。

## 验证与维护

问题处理接口 `PUT /api/audits/{id}/issues/{issue_id}/decision` 支持 `confirmed`、`false_positive` 和 `open`。`open` 撤销当前检查版本的人工标记，重复撤销保持 `open`；结果已过期时仍返回 `409/STALE_RESULT`，历史版本标记不会套用到新版本报告。损坏 PNG（包括 CRC 校验失败）返回 `415/INVALID_IMAGE`。

离线浏览器全链路验证见 `frontend/README.md` 的 `npm run test:offline`。`tests.browser_server` 是带合成提取器的测试进程，只监听 127.0.0.1:5193，使用独立临时数据库，不能用于真实包装图识别。

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q app audit_engine scripts
.\.venv\Scripts\python.exe -m scripts.export_openapi --output data/openapi.json
.\.venv\Scripts\python.exe -m scripts.list_rules
```

包含确定性规则迁移、32g→3.2g 后重算/旧版保留、401、409、幂等、原子领取/崩溃恢复、真实解析入口、视觉 SDK MockTransport、整表手工降级、上传回滚/限额与独立 API/Worker 进程测试。历史 OCR 测试中依赖未提交图片或 `market_labels_50` 缓存的用例明确跳过；不能据此宣称真实 OCR 准确率。自动化测试不使用真实密钥；真实百度 OCR 和公网端到端仍需单独验收。

`httpx2/httpcore2` 来自 D0 环境的 `pip freeze`，随当前 Starlette 1.8.0 测试依赖进入锁定清单，业务代码未主动选用它们；已检查本机 Starlette 依赖元数据/TestClient 导入并运行测试。没有据此确认其发行方身份。新增 OpenAI SDK 使用的是另行锁定的 `httpx/httpcore`，两套名称互不替代。

path-guard 只允许后端 PR 修改 `src/`。本轮按负责人交接范围同步根 `shared/openapi.json` 和项目规划，前端从共享契约生成类型；本地临时契约也可导出到被忽略的 `src/data/openapi.json`。`app/data/rule_clauses.seed.json` 是源码种子；根 .gitignore 仅忽略根 data/ 和 src/data/，不会再误忽略 app/data/。

删除单个任务前停止 API/Worker 并备份 DATA_DIR。脚本默认只预览；明确指定同一个 UUID 才删除该任务库记录、净化图片和原始 OCR 响应，其他任务不受影响。运行中的任务拒绝删除；停止 Worker 并等待锁超过 5 分钟后，可用 `python -c "from app.worker import recover_jobs; recover_jobs()"` 恢复锁再删除。

```powershell
.\.venv\Scripts\python.exe -m scripts.delete_audit <任务UUID>
.\.venv\Scripts\python.exe -m scripts.delete_audit <任务UUID> --confirm <同一个任务UUID>
```

目录：`app/` 平台服务，`audit_engine/` 纯 Python 原型引擎，`samples_public/` 可提交公开缓存，`scripts/` 运维与契约工具，`tests/` 验证，`data/` 本地运行数据（不入库）。

### 有界真实视觉验证

`python -m scripts.verify_vision <图片1> <图片2>` 每次最多接受两张已授权图片，各发送一次，不自动重试；结果写入被忽略的 `data/vision-smoke/`，发送前写入 SHA-256 标记，同目录重跑不会重复调用。它显式不调用百度，不生成伪造 OCR，使用空事实文档验证视觉候选、坐标校验及人工门禁，不能作为 OCR+视觉完整链路验收。

OpenAI Next 的 OpenAI 兼容接口为 `https://api.openai-next.com/v1`，配置依据为负责人提供的 [Quickstart](https://credits.openai-next.com/guide/quickstart) 和 [模型列表](https://credits.openai-next.com/guide/resources)。本地选择 `gpt-6.1-sol`；该模型请求使用 `max_completion_tokens` 与 `reasoning_effort=low`，省略不支持的 temperature 参数。模型规则依据 [OpenAI 官方指南](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6.1-sol)，实际网关兼容性以真实调用结果为准。

2026-10-19 本轮证据：全量测试 123 passed / 38 skipped / 26 subtests passed；真实调用后补充营养素别名回归，相关 44 条全部通过。两张真实包装稿各发送一次：原味稿约 62.85 秒超时，龙井厚乳稿约 21.82 秒成功，1 张营养表、26 个待复核项，未确认候选被门禁阻止进入计算。只读模型列表返回 200 且包含目标模型。图片和结果仅保存在 src/data/vision-smoke/，不提交；当时尚未配置百度 OCR，该轮仅为视觉单项验证。

随后完成百度真实调用：两张稿各调用一次高精度全文和一次表格接口，共 4 次成功；全文分别返回 94/57 条，表格接口分别检出 0/1 张表。原味稿由全文 OCR 恢复营养表候选但保持结构待复核；龙井稿检出表但仍有结构疑点，均未自动放行。复用这些真实响应及此前的视觉结果，在独立 integration-runtime 数据库通过 HTTP API 和 Worker 验证上传、门禁、脚本复核、整表人工降级、版本重检、JSON/HTML 报告；两任务均为 PARTIAL，原始 OCR v0 保留。脚本决定仅用于接口事务验证，不是人工签核，也未验证配料等 OCR 内容的正确性。本轮未新增视觉调用，累计视觉 2 次、百度 OCR 4 次。记录见本地 data/backend-verification.json 和 data/ocr-live-validation/workflow-results.json。

## 未覆盖声称的模型初审

`save_check` 在确定性检查成功后，为当前版本的 ready/人工确认、固定规则未覆盖声称创建独立持久队列。`app.worker` 常驻模式启动独立初审执行线程；不阻塞 OCR/规则任务，初审失败不会改变主任务状态或人工决定。`python -m app.claim_worker --once` 可单独领取一批；无需同时启动第二个常驻执行器。`app.worker --once` 只执行主任务，不隐式产生付费初审。历史任务不回填；内置样本不调用模型。

配置：`CLAIM_PRECHECK_ENABLED=1`、`CLAIM_PRECHECK_MODEL=gpt-6.1-sol`、`DAILY_CLAIM_PRECHECK_LIMIT=10`。base URL、密钥和总 VLM 额度复用既有配置；独立每日 10 次与 VLM 总上限同时原子计数，失败请求也计入发送次数。每次发送前只读核实网关是否提供精确模型 ID；不可用不换模型。超时或中断不自动重试，实际请求与返回标识、原始意见、验证结果和失败原因分别留存。

- `GET /api/audits/{id}/claim-prechecks?version=N`：纯读取；返回 pending/running/completed/unavailable、items、stale、error_code/error_message。省略 version 为当前文档版本，无未覆盖项返回 completed/空 items。当前版本尚未检查排队时返回 unavailable/NOT_SCHEDULED；GET 不产生调用。
- `POST /api/audits/{id}/claim-prechecks/retry?version=N`：显式重试，仅当前已做过确定性检查的版本可用。pending/running/completed 幂等返回，只有 unavailable 重新排队。支持原有鉴权与同源限制，另限每分钟 5 次。
- `AuditIssue.claim_id` 将 CLAIM-UNKNOWN 与稳定的文档声称 ID 关联；只携带该声称的原文证据。该类规则依据为 `not_applicable`，无“请修正”默认建议；不代表发现违规。原有 L1 结果与统计不变。

缓存绑定文档版本、确认数据、原图 SHA、资料清单指纹、指定模型/网关和提示词版本；结果额外记录实际模型与结果指纹。修正后不能把旧意见当新版本结果；排队后输入已变化则不发送模型。引用必须来自该声称获准使用的片段和已确认数据，坏 JSON、伪造 ID、重复/漏声称、模型不符等整批拒绝。没有相关资料时明确显示材料不足语境，API 失败不会冒充材料不足。

资料清单：`app/data/claim_references.json`。本轮仅准入两条内部核查提示，保留原文件 SHA、章节、审核记录及适用关键词，不能证明产品事实。未核实的标准摘要、历史案例和泛化渠道建议均排除。没有非油炸的产品工艺材料；包装自述不能自证工艺。

本轮新增真实验收只有一次批次调用（约 18.9 秒），请求/返回均为 gpt-6.1-sol；基于先前真实百度 OCR 的就绪文字及原图，4 条声称均返回材料不足，引用和版本校验通过，主任务未改变。没有重跑 OCR，没有新增第二次初审调用。真实记录只在本地 `data/claim-precheck-live/result.json`；这不是人工签核。前端页面未在本轮实现，消费展示以共享 OpenAPI 为准。

最终边界复核：只有引用与该声称相关且明确允许支撑判定的准入材料，才可能接受 supported/contradiction；当前清单仅有内部核查提示，因此不具备这种判定依据。模型强行给出这类结论会被拒绝，不会改写成有效意见（2026-10-20 起按项拒绝，见下文）。过期执行返回时保留该次审计，但不能覆盖新重试的状态和结果。完整回归 166 条通过，随后补充的 22 条初审专项回归通过。真实本地 GET 连续读取三次，初审调用数保持 1。

### 初审部分成功与离线恢复（2026-10-20）

接口 `schema_version=claim-precheck-v2` 新增顶层 `partial`，单项新增 `error_code/error_message`。整批 JSON、版本、实际模型及声称ID缺失/重复/伪造仍严格拒绝；单项格式、引用或结论缺依据时仅该项 unavailable/verdict=null，不改写成材料不足，其他有效意见继续展示。全部单项均失败仍 unavailable，全部成功为 completed。

显式POST retry仅为当前失败项排队，保留已完成项；pending/running/completed重试幂等，不自动重试。执行器发送的claims只含失败项，网络或整批身份校验失败也不会抹去之前成功项。刷新GET零模型调用。提示词及原输入指纹保持不变，校验器单独记录 `claim-validator-v2`。

`python -m scripts.revalidate_claim_precheck --batch-id ID` 显式离线恢复当前文档、输入未变的最新批次：只读取当前attempt代次的保存响应，追加 `claim_precheck_validations` 派生审计，不修改原 `ClaimPrecheckAttempt`，不增加attempt次数或用量。重复恢复同一源响应和校验器版本幂等；迟到旧attempt不得覆盖新结果。API返回 `validator_version/revalidated_from_attempt_id` 供追溯；这不是重新调用模型或人工签核。

本轮已对当前真实任务离线恢复并只读验收页面：4项中3项“材料不足”正常显示，1项无合格依据的矛盾结论单独拒绝，按钮仅重试失败的1项。原任务、文档、规则结果、人工决定、原始调用审计及用量哈希均保持不变。证据仅保存于本地 `data/claim-partial-validation/`，未新增模型或OCR调用。完整后端回归217项通过，随后22项恢复/契约专项复验通过；浏览器16项专项与1项真实只读验收通过。

## 营养表复核诊断关联

新任务合并、旧任务复核投影与修正提交共用 `table_association`：只有 `TABLE_V2_NOT_FOUND` / `TABLE_LAYOUT_RECOVERED` 的非空同图证据全部落在唯一候选表时，才补充复核用的表 ID。计数不符、全图遗漏、无证据和多候选诊断继续单独展示。旧版本 JSON 保持原样，读取不会创建版本或自动确认字段。

已关联的 `needs_vision` 与 `needs_review` 可在同一次 corrections 请求中先确认待复核字段、再显式提交 `tableDecisions.confirm_complete`，必须勾选 `table_checked_all`，且表头、每份量及全部现有单元格已就绪。表 ID、原诊断码、证据、图像指纹与版本仍须匹配。糖或反式脂肪未标示 NRV 的空项不凭空补值。结构有误时使用原表 ID 整表替换；同图已有候选表时不允许用新 ID 追加，以免重复。真正未检出任何候选表仍可手录，当前不支持通过该入口给同图额外新增第二张表。

## 分项计算口径（2026-10-20）

策略 `scene09-2026-10-20-v2` 停用“整表必须改为每100g”的旧 COURSE-100G-001。NRV仍给每100g项目预检参考值，明确区分原每份列，不能将原每份17%直接与每100g23%判为冲突，也不将重算值回填OCR字段。每份列的NRV参考项保持REVIEW，不冒充已完成同基准校验。

能量按原表基准直接计算，含已标示膳食纤维，不重复计入糖或饱和脂肪。72g稿重算1418 kJ/份，标示1420 kJ/份，差2 kJ；修约区间4.45 kJ/份，因此仍为REVIEW。原0.5 kJ/100g通过阈值等比例换成0.36 kJ/72g，保持判定尺度。消息最多展示两位小数，`details`保留完整精度；新增 `basis/serving_g/computed_kj/shown_kj/difference_kj/source_rounding_interval_kj/source_pass_tolerance_kj/formula` 明确原表核算，旧 `*_per_100g` 与 `rounding_interval_kj` 保留为百克审计值。`output_header`反映原表，`calculation_bases`分别说明NRV、能量与声称基准。

高蛋白、高纤维仍按每100g或每420kJ门槛，低糖、无糖和实测容差保留每100g，跨位置比较也先统一基准。以上计算只接受已确认输入，不重新识别包装，不改变人工复核记录。

## 标准原文参考（2026-10-20）

`app/data/standard_sources.json` 固定关联现有营养、包装规则对应的 GB 28050—2025、GB 7718—2025。已下载公共机构网站PDF，核对首页标准编号、标题、版本，并保存来源、页数、SHA-256；本地核验副本仅在被忽略的 `data/standard-sources/`。不调用模型匹配，不表示已逐条核验。

`Regulation` 新增可空 `source_title/source_url`。新检查与旧任务的 issues/report 读取均补充来源，保留已有 `clause_id/verification_status`，不重写历史 Issue 或 Document。课程要求、未覆盖声称不附加国标链接。结果页与HTML报告仅展示标准名称、版本和“查看标准原文”；精确条款号、摘要和核验提示不在该区域展示，底层核验状态不变。标准映射或旧版没有来源时显示未关联，不捏造链接。
