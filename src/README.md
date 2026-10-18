# 后端 src

```
src/
  app/            FastAPI 应用（api、models、schemas、services、worker）
  audit_engine/   迁入的审核引擎（label_check、label_check_v2）
  tests/
  requirements.txt
```

运行（在 `src/` 下）：

```bash
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

## D0 接口契约

在 `src/` 下运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.export_openapi --output ..\shared\openapi.json
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

`shared/openapi.json` 供前端用 `openapi-typescript` 生成类型。当前 `/api/health` 和 `/api/auth/login` 有实际逻辑；登录成功后设置签名 cookie，其余业务路由通过认证后明确返回 501。

仓库的 path-guard 对后端贡献者只允许修改 `src/`，而规划要求导出到 `shared/`。提交前需要由项目负责人处理该生成文件的归属；不要把它直接放进后端贡献者的 PR。

复核提交模型有意保留两种命名风格：平台新增字段使用 `snake_case`，来自旧版离线复核 JSON 的字段通过 Pydantic alias 保持 `camelCase`。这样服务端可以把原型产生的复核记录原样校验，避免在迁移边界静默改名；前端应以 OpenAPI 生成的字段名为准。
