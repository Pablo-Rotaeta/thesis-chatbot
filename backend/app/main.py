from dotenv import load_dotenv
load_dotenv()
import os
print("OPENAI KEY:", os.getenv("OPENAI_API_KEY", "NOT FOUND")[:20])

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import chat, sessions, admin

app = FastAPI(title="Thesis Chatbot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
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
