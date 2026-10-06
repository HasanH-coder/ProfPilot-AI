"""Base classes and helpers shared by API request and response models."""

import uuid

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.core.errors import NotFoundError


class ApiModel(BaseModel):
    """JSON in camelCase, like the rest of the frontend; Python stays snake_case."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def parse_id(value: str, *, what: str = "item") -> str:
    """A normalized UUID string. Anything else can't exist, so it is "not found"."""
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as error:
        raise NotFoundError(f"This {what} doesn't exist, or you don't have access to it.") from error
