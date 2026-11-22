"""字段映射文件导入接口：预检、确认、冲突更正、缓存/待办查询与投递回查。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.parse_import import parse_import_service

router = APIRouter(prefix="/api/parse-imports", tags=["响应解析导入"])


class MappingItem(BaseModel):
    field: str
    path: str
    type: str | None = None
    default: Any = None


class UploadPayload(BaseModel):
    endpoint: str
    filename: str = "mapping.json"
    content: str | None = None
    mappings: list[MappingItem] | None = None


class ConfirmPayload(BaseModel):
    interrupt_after: int | None = Field(default=None, ge=1)
    operator: str = "数据工程组"


class ResolvePayload(BaseModel):
    path: str
    note: str | None = None
    operator: str = "数据工程组"


class DeliveryPayload(BaseModel):
    pipeline_id: int | None = None
    note: str | None = None


def _found(result: dict[str, Any] | None, detail: str) -> dict[str, Any]:
    if result is None:
        raise HTTPException(status_code=404, detail=detail)
    return result


@router.get("/state")
def import_state() -> dict[str, Any]:
    """导入链路全景：样本、导入、映射版本、缓存结果、待办、冲突与交付。"""
    return parse_import_service.state()


@router.get("/samples")
def list_samples() -> dict[str, Any]:
    """同一端点的响应样本，导入定义都拿它核对。"""
    return {"items": parse_import_service.samples()}


@router.get("")
def list_imports() -> dict[str, Any]:
    return {"items": parse_import_service.list_imports()}


@router.post("/upload")
def upload_mapping(payload: UploadPayload) -> dict[str, Any]:
    """上传字段映射文件（JSON/CSV 文本），立即用同端点样本预检。"""
    mappings = (
        [item.model_dump() for item in payload.mappings]
        if payload.mappings else None
    )
    result, message = parse_import_service.upload(
        endpoint=payload.endpoint,
        filename=payload.filename,
        content=payload.content,
        mappings=mappings,
    )
    if result is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, **result}


@router.get("/deliveries")
def list_deliveries() -> dict[str, Any]:
    return {"items": parse_import_service.list_deliveries()}


@router.get("/deliveries/compare")
def compare_deliveries() -> dict[str, Any]:
    """比较每次交付实际使用的字段来源。"""
    return parse_import_service.compare_deliveries()


@router.post("/deliveries/run")
def run_delivery(payload: DeliveryPayload) -> dict[str, Any]:
    result, message = parse_import_service.run_delivery(
        pipeline_id=payload.pipeline_id, note=payload.note)
    if result is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, **result}


@router.get("/deliveries/{run_id}")
def trace_delivery(run_id: int) -> dict[str, Any]:
    result = parse_import_service.trace_delivery(run_id)
    return _found(result, f"投递 {run_id} 不存在")


@router.get("/{import_id}")
def get_import(import_id: int) -> dict[str, Any]:
    return _found(parse_import_service.get_import(import_id), f"导入 {import_id} 不存在")


@router.post("/{import_id}/confirm")
def confirm_import(import_id: int, payload: ConfirmPayload) -> dict[str, Any]:
    result, message = parse_import_service.confirm(
        import_id,
        interrupt_after=payload.interrupt_after,
        operator=payload.operator,
    )
    if result is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, **result}


@router.post("/{import_id}/conflicts/{conflict_id}/resolve")
def resolve_conflict(import_id: int, conflict_id: int, payload: ResolvePayload) -> dict[str, Any]:
    result, message = parse_import_service.resolve_conflict(
        import_id,
        conflict_id,
        chosen_path=payload.path,
        note=payload.note,
        operator=payload.operator,
    )
    if result is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, **result}
