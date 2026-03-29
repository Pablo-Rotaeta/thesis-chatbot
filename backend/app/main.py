import os
from dotenv import load_dotenv

# Load .env file — must be before any other imports
load_dotenv()

# Debug — confirm keys are loaded (remove after testing)
print("=== ENV CHECK ===")
print("OPENAI:", os.getenv("OPENAI_API_KEY", "NOT FOUND")[:15] + "...")
print("GEMINI:", os.getenv("GEMINI_API_KEY", "NOT FOUND")[:15] + "...")
print("ANTHROPIC:", os.getenv("ANTHROPIC_API_KEY", "NOT FOUND")[:15] + "...")
print("=================")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import chat, sessions, admin

app = FastAPI(title="Thesis Chatbot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

@app.get("/")
def root():
    return {"status": "ok", "message": "Thesis Chatbot API"}
