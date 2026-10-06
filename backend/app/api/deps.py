"""Dependencies shared by the API routes."""

from fastapi import Depends

from app.ai.openai_service import OpenAIService, get_openai_service
from app.core.security import CurrentProfessor, get_current_professor
from app.db.supabase import Database, SupabaseDatabase


def get_db(professor: CurrentProfessor = Depends(get_current_professor)) -> Database:
    """The database, as the signed-in professor (Row Level Security applies)."""
    return SupabaseDatabase(professor)


def get_ai() -> OpenAIService:
    return get_openai_service()
