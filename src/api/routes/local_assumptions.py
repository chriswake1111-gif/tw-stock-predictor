"""Local-only installed assumption editor; no management key crosses the UI."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.api.routes.installed_data_operations import _get_db_path, _get_instance_id
from src.services.local_assumption_service import LocalAssumptionService

router = APIRouter(prefix="/api/v2/research/assumptions", tags=["local-assumptions"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Text = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]


class EPSValues(Strict):
    fiscal_year: int = Field(ge=1900, le=2200)
    eps_base: float
    source: Text
    source_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    rationale: Text


class PEValues(Strict):
    label: Text
    pe_value: float = Field(gt=0)
    rationale: Text


class Point(Strict):
    role: Literal["origin", "swing_end", "projection_origin"]
    price: float = Field(gt=0)
    market_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class AnchorValues(Strict):
    rule_id: Literal["FB-03", "FB-04"]
    anchors: list[Point] = Field(min_length=2, max_length=3)
    source: Text
    rationale: Text


class Draft(Strict):
    values: EPSValues | PEValues | AnchorValues
    previous_id: str | None = Field(default=None, max_length=128)


class Decision(Strict):
    rationale: Text


Kind = Literal["eps", "pe", "anchor"]


def service(request):
    _get_instance_id(request)
    return LocalAssumptionService(_get_db_path(request))


def invoke(fn):
    try:
        return fn()
    except (ValueError, KeyError) as exc:
        raise HTTPException(409 if "idempotency_conflict" in str(exc) else 422, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(503, detail="local_research_storage_unavailable") from exc


def values_for(kind, payload):
    expected = {"eps": EPSValues, "pe": PEValues, "anchor": AnchorValues}[kind]
    if not isinstance(payload.values, expected):
        raise HTTPException(422, detail="assumption_kind_payload_mismatch")
    return payload.values.model_dump()


@router.get("/{symbol}")
def list_assumptions(symbol: str, request: Request):
    return invoke(lambda: service(request).list(symbol))


@router.post("/{symbol}/{kind}/preview")
def preview(symbol: str, kind: Kind, payload: Draft, request: Request):
    return invoke(lambda: service(request).preview(symbol, kind, values_for(kind, payload), payload.previous_id))


@router.post("/{symbol}/{kind}/draft")
def draft(symbol: str, kind: Kind, payload: Draft, request: Request,
          idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return invoke(lambda: service(request).execute(symbol, kind, "draft", values_for(kind, payload),
                  idempotency_key, previous_id=payload.previous_id))


@router.post("/{symbol}/{kind}/{resource_id}/{action}")
def decision(symbol: str, kind: Kind, resource_id: str, action: Literal["approve", "revoke"],
             payload: Decision, request: Request,
             idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return invoke(lambda: service(request).execute(symbol, kind, action, payload.model_dump(),
                  idempotency_key, resource_id=resource_id))
