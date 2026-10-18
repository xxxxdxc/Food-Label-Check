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
