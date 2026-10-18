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
