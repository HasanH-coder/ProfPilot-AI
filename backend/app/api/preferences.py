"""The professor's assessment preferences (personalization)."""

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.db.supabase import Database
from app.schemas.api import PreferencesIn, PreferencesOut
from app.services.personalization import PreferenceService

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


@router.get("")
async def get_preferences(db: Database = Depends(get_db)) -> PreferencesOut:
    return PreferencesOut.model_validate(await PreferenceService(db).get())


@router.put("")
async def update_preferences(body: PreferencesIn, db: Database = Depends(get_db)) -> PreferencesOut:
    service = PreferenceService(db)
    return PreferencesOut.model_validate(
        await service.update(explicit_notes=body.explicit_notes, learning_enabled=body.learning_enabled)
    )


@router.delete("/learned")
async def reset_learned(db: Database = Depends(get_db)) -> PreferencesOut:
    """Forgets everything learned from past exams and revisions."""
    return PreferencesOut.model_validate(await PreferenceService(db).reset_learned())
