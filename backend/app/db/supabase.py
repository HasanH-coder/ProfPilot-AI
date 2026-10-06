"""Reads and writes Supabase data as the signed-in professor.

Every request carries the professor's own access token, so Postgres Row Level
Security decides what each call may read or change, exactly as it does for the
Next.js app. The API never uses a secret or service_role key: even a bug in an
endpoint can't reach another professor's data.

Services depend on the small `Database` interface below, not on HTTP details,
so tests can swap in an in-memory implementation.
"""

import json
import logging
from collections.abc import Iterable, Sequence
from typing import Any, Protocol
from urllib.parse import quote

import httpx2

from app.core.config import settings
from app.core.errors import (
    AppError,
    AuthenticationError,
    ConflictError,
    DatabaseError,
    InvalidInputError,
    NotFoundError,
)
from app.core.security import CurrentProfessor

logger = logging.getLogger("profpilot.db")

# (column, operator, value), e.g. ("id", "eq", some_id) or ("status", "in", ["a", "b"]).
Filter = tuple[str, str, Any]
# (column, "asc" | "desc")
Order = tuple[str, str]

_OPERATORS = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "is", "cs"}


class Database(Protocol):
    """What services need from the database, as the signed-in professor."""

    professor_id: str

    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Sequence[Filter] = (),
        order: Sequence[Order] = (),
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    async def select_one(self, table: str, *, columns: str = "*", filters: Sequence[Filter] = ()) -> dict[str, Any] | None: ...

    async def insert(
        self, table: str, rows: dict[str, Any] | list[dict[str, Any]], *, columns: str = "*"
    ) -> list[dict[str, Any]]: ...

    async def update(
        self, table: str, values: dict[str, Any], *, filters: Sequence[Filter], columns: str = "*"
    ) -> list[dict[str, Any]]: ...

    async def delete(self, table: str, *, filters: Sequence[Filter]) -> list[dict[str, Any]]: ...

    async def rpc(self, function: str, params: dict[str, Any]) -> Any: ...

    async def download(self, bucket: str, path: str) -> bytes: ...


_http_client: httpx2.AsyncClient | None = None


def get_http_client() -> httpx2.AsyncClient:
    """One connection pool for all Supabase calls."""
    global _http_client
    if _http_client is None:
        _http_client = httpx2.AsyncClient(timeout=httpx2.Timeout(30.0, read=120.0))
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


class SupabaseDatabase:
    """The real `Database`, over Supabase's REST (PostgREST) and Storage APIs."""

    def __init__(self, professor: CurrentProfessor, client: httpx2.AsyncClient | None = None) -> None:
        if not settings.is_supabase_configured:
            raise AppError(
                "The API isn't connected to Supabase. Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY.",
                code="not_configured",
            )
        self.professor_id = professor.id
        self._token = professor.access_token
        self._client = client or get_http_client()
        self._base = settings.supabase_url.rstrip("/")

    # -- PostgREST -------------------------------------------------------------

    def _headers(self, *, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": settings.supabase_publishable_key,
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Sequence[Filter] = (),
        order: Sequence[Order] = (),
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params = [("select", columns), *_filter_params(filters)]
        if order:
            params.append(("order", ",".join(f"{column}.{direction}" for column, direction in order)))
        if limit is not None:
            params.append(("limit", str(limit)))
        response = await self._request("GET", f"/rest/v1/{table}", params=params)
        return response.json()

    async def select_one(self, table: str, *, columns: str = "*", filters: Sequence[Filter] = ()) -> dict[str, Any] | None:
        rows = await self.select(table, columns=columns, filters=filters, limit=2)
        if len(rows) > 1:
            raise DatabaseError()
        return rows[0] if rows else None

    async def insert(
        self, table: str, rows: dict[str, Any] | list[dict[str, Any]], *, columns: str = "*"
    ) -> list[dict[str, Any]]:
        payload = rows if isinstance(rows, list) else [rows]
        if not payload:
            return []
        response = await self._request(
            "POST",
            f"/rest/v1/{table}",
            params=[("select", columns)],
            content=_dumps(payload),
            prefer="return=representation",
        )
        return response.json()

    async def update(
        self, table: str, values: dict[str, Any], *, filters: Sequence[Filter], columns: str = "*"
    ) -> list[dict[str, Any]]:
        if not filters:
            raise ValueError("update needs at least one filter")
        response = await self._request(
            "PATCH",
            f"/rest/v1/{table}",
            params=[("select", columns), *_filter_params(filters)],
            content=_dumps(values),
            prefer="return=representation",
        )
        return response.json()

    async def delete(self, table: str, *, filters: Sequence[Filter]) -> list[dict[str, Any]]:
        if not filters:
            raise ValueError("delete needs at least one filter")
        response = await self._request(
            "DELETE",
            f"/rest/v1/{table}",
            params=[("select", "id"), *_filter_params(filters)],
            prefer="return=representation",
        )
        return response.json()

    async def rpc(self, function: str, params: dict[str, Any]) -> Any:
        response = await self._request("POST", f"/rest/v1/rpc/{function}", content=_dumps(params))
        return response.json() if response.content else None

    # -- Storage ---------------------------------------------------------------

    async def download(self, bucket: str, path: str) -> bytes:
        # The /authenticated/ endpoint applies the Storage policies: a professor
        # can only download files in their own folder.
        url = f"/storage/v1/object/authenticated/{quote(bucket)}/{quote(path)}"
        response = await self._request("GET", url, storage=True)
        return response.content

    # -- Internals -------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Iterable[tuple[str, str]] = (),
        content: bytes | None = None,
        prefer: str | None = None,
        storage: bool = False,
    ) -> httpx2.Response:
        try:
            response = await self._client.request(
                method,
                self._base + path,
                params=list(params),
                content=content,
                headers=self._headers(prefer=prefer),
            )
        except httpx2.TimeoutException as error:
            raise DatabaseError("The database took too long to respond. Please try again.") from error
        except httpx2.HTTPError as error:
            raise DatabaseError() from error
        if response.status_code >= 400:
            raise _translate_error(response, storage=storage)
        return response


def _dumps(value: Any) -> bytes:
    return json.dumps(value, default=str, ensure_ascii=False).encode()


def _filter_params(filters: Sequence[Filter]) -> list[tuple[str, str]]:
    params = []
    for column, operator, value in filters:
        if operator not in _OPERATORS:
            raise ValueError(f"unsupported operator {operator}")
        if operator == "in":
            items = ",".join(_quote_list_item(item) for item in value)
            params.append((column, f"in.({items})"))
        elif operator == "is":
            params.append((column, f"is.{'null' if value is None else str(value).lower()}"))
        elif operator == "cs":
            params.append((column, f"cs.{json.dumps(value)}"))
        else:
            params.append((column, f"{operator}.{_scalar(value)}"))
    return params


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _quote_list_item(value: Any) -> str:
    text = _scalar(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _translate_error(response: httpx2.Response, *, storage: bool) -> AppError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = str(body.get("code") or body.get("error") or "")
    status = response.status_code

    if status == 401 or code in {"PGRST301", "PGRST302", "PGRST303"}:
        return AuthenticationError()
    if storage:
        if status in (400, 403, 404):
            return NotFoundError("This file couldn't be found.")
        logger.warning("Storage error %s", status)
        return DatabaseError("The file couldn't be read. Please try again.")
    # Row Level Security rejections look like "not found": never confirm that
    # another professor's item exists.
    if code == "42501" or status == 403:
        return NotFoundError()
    if code == "23503":
        return NotFoundError("Something this refers to doesn't exist any more.")
    if code == "23505":
        return ConflictError("This already exists.")
    if code in {"23514", "23502", "22P02", "22023", "22003", "22001"}:
        return InvalidInputError()
    if code == "PGRST116":
        return NotFoundError()
    logger.warning("Database error %s (HTTP %s)", code or "unknown", status)
    return DatabaseError()
