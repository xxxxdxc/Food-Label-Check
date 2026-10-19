# Food-Label-Check

食品包装智能审核平台（前后端分离）。

```
src/        后端（FastAPI + Worker + audit_engine）
frontend/   前端（React + TypeScript + Vite）
```

## 目录权限约定

- `src/`：后端开发负责人（zch1001）可修改。
- `frontend/`、`.github/`、根目录配置文件：仅项目负责人修改。
- 所有改动走分支和 Pull Request，不直接推 `main`。
- 密钥只放 `.env`，不提交。参考 `.env.example`。


## 魔搭 Git 部署

通过 Git 推送完整项目到创空间仓库，平台按 `ms_deploy.json` 和根目录 `Dockerfile` 构建并监听 7860 端口；本机无需运行 Docker。前端和 API 同源，独立 Worker 随服务启动。

在创空间设置的**私密变量**中配置 `BAIDU_API_KEY`、`BAIDU_SECRET_KEY`、`VLM_API_KEY`、`VLM_BASE_URL`、`VLM_MODEL`、`CLAIM_PRECHECK_MODEL`，以及随机生成的至少 32 字符 `SESSION_SECRET`。不要把 `.env` 或访问令牌推送到仓库。

运行设置：`PUBLIC_ACCESS=1`、`LOCAL_ONLY=0`、`OCR_MODE=live`、`VLM_ENABLED=1`、`DATA_DIR=/app/runtime`；`ALLOWED_ORIGINS` 设置为公开应用域名的 JSON 数组。公开访问无需登录，任务通过签名 HttpOnly Cookie 按访客隔离；清除 Cookie 后无法找回该访客任务。运行目录随平台容器生命周期管理，重建前应导出需要保留的结果。
