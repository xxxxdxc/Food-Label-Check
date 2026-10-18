from fastapi import FastAPI

app = FastAPI(title="Food-Label-Check")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
