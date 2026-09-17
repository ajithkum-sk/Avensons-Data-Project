"""Request/response shapes for the API."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class SnapshotOut(BaseModel):
    snapshot_id: int
    snapshot_date: date
    snapshot_type: str
    source_file: str | None = None
    row_count: int


class RunRequest(BaseModel):
    snapshot_id: int | None = Field(None, description="Defaults to the newest snapshot")
    only: list[str] | None = Field(None, description="Limit to these rule codes")


class RuleParamsIn(BaseModel):
    params: dict[str, Any] | None = None
    is_enabled: bool | None = None
    severity: Literal["low", "medium", "high"] | None = None


class GlobalConfigIn(BaseModel):
    params: dict[str, Any]


class ExceptionIn(BaseModel):
    rule_code: str
    customer_code: str | None = None
    salesman_key: int | None = None
    note: str | None = None


class NoteIn(BaseModel):
    author: str
    note: str
