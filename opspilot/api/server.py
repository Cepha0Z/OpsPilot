from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from ..core.agent import (
    run_agent,
    approve_action,
    reject_action,
)


WEB_DIR = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="OpsPilot")


app.mount(
    "/assets",
    StaticFiles(directory=WEB_DIR),
    name="frontend"
)


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "message": "OpsPilot backend is running."
    }


@app.post("/api/chat")
def chat(request: dict):

    message = request.get("message", "").strip()

    session_id = request.get("session_id")
    idempotency_key = request.get("request_id")

    if not message:
        return {
            "type": "error",
            "message": "Message cannot be empty."
        }

    return run_agent(
        user_message=message,
        session_id=session_id,
        idempotency_key=idempotency_key,
    )


@app.post("/api/approve")
def approve(request: dict):

    approval_id = request.get("approval_id")

    if not approval_id:
        return {
            "type": "error",
            "message": "Approval ID is required."
        }

    return approve_action(approval_id)


@app.post("/api/reject")
def reject(request: dict):

    approval_id = request.get("approval_id")

    if not approval_id:
        return {
            "type": "error",
            "message": "Approval ID is required."
        }

    return reject_action(approval_id)
