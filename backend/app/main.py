"""FastAPI application entry point. Run from backend/ with: uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import assessments, assistant, exams, health, preferences
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.db.supabase import close_http_client

# Application logs carry ids, sizes and timings only: never prompts, files or keys.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await close_http_client()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Let the Next.js frontend, which runs on a different origin, call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # So the browser can read the file name of a downloaded PDF.
    expose_headers=["Content-Disposition"],
)

install_error_handlers(app)

app.include_router(health.router)
app.include_router(assessments.router)
app.include_router(exams.router)
app.include_router(assistant.router)
app.include_router(preferences.router)
