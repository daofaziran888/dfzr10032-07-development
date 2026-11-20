"""RPA采集结果数据集接口。

覆盖三块：
- 结果字段定义版本（/field-definitions）：支持批次提取期间定义改版，旧版本归档可回查；
- 采集结果记录（根路径）：提取、确认、重试，保留动作位置与执行批次；
- 接入通道提交与批次回查（/submit、/trace、/ingestion-records）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.rpa_dataset import (
    DEF_ARCHIVED,
    RpaDatasetService,
)

router = APIRouter(prefix="/api/rpa_dataset", tags=["RPA采集结果数据集"])

service = RpaDatasetService()

RESULT_COLUMNS = [
    "记录编号", "任务编号", "执行批次", "页面标识", "页面地址", "动作位置",
    "定义编号", "定义版本", "状态",
]
DEF_COLUMNS = ["定义编号", "任务编号", "定义版本", "状态", "创建时间"]
INGEST_COLUMNS = [
    "接入记录编号", "通道编号", "任务编号", "执行批次", "采集记录编号",
    "页面标识", "定义版本", "状态",
]


# ---------------------------------------------------------------- 字段定义版本
@router.get("/field-definitions/export")
def export_definitions() -> dict[str, Any]:
    items, total = service.list_definitions(page=1, size=10000)
    return {"module": "rpa_field_def", "total": total, "items": items}


@router.get("/field-definitions", response_model=PageResult[dict])
def list_definitions(
    request: Request,
    task_code: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description=f"生效中、{DEF_ARCHIVED}"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {
        k: v
        for k, v in request.query_params.items()
        if k not in ("task_code", "status", "page", "size") and v.strip()
    }
    items, total = service.list_definitions(
        task_code=task_code, status=status, page=page, size=size, extra=extra
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/field-definitions/{entry_id}", response_model=dict)
def get_definition(entry_id: int) -> dict:
    entry = service.get_definition(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"字段定义 {entry_id} 不存在或已归档")
    return entry


@router.post("/field-definitions", response_model=ActionResult)
def create_definition(payload: EntryPayload) -> ActionResult:
    """登记一个字段定义版本；同一任务旧版本自动归档，已建采集记录冻结旧快照。"""
    entry, missing = service.create_definition(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段或任务不存在：{'、'.join(missing)}")
    return ActionResult(ok=True, message="结果字段定义版本已生效，旧版本已归档", entry=entry)


# ---------------------------------------------------------------- 采集结果记录
@router.get("/export")
def export_results() -> dict[str, Any]:
    items, total = service.list_results(page=1, size=10000)
    return {"module": "rpa_dataset", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_results(
    request: Request,
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="提取中、已确认、定位失败、已顶替、已接入"),
    task_code: str | None = Query(default=None, description="按任务编号过滤"),
    batch_no: str | None = Query(default=None, description="按执行批次过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {
        k: v
        for k, v in request.query_params.items()
        if k not in ("keyword", "status", "task_code", "batch_no", "page", "size") and v.strip()
    }
    items, total = service.list_results(
        keyword=keyword,
        status=status,
        task_code=task_code,
        batch_no=batch_no,
        page=page,
        size=size,
        extra=extra,
    )
    return PageResult(items=items, total=total, page=page, size=size)


# ---------------------------------------------------------------- 接入通道与回查
# 注意：/trace、/ingestion-records 等字面量路径必须放在 /{entry_id} 之前，
# 否则会被整型入口参数截获并返回 422。
@router.post("/submit", response_model=ActionResult)
def submit_batch(payload: EntryPayload) -> ActionResult:
    """数据接入通道按任务编号 + 执行批次提交，生成与原页面关联的接入记录。"""
    values = payload.values
    task_code = str(values.get("任务编号") or "").strip()
    batch_no = str(values.get("执行批次") or "").strip()
    channel_id = values.get("通道ID")
    if not task_code or not batch_no or channel_id is None:
        return ActionResult(ok=False, message="提交需要任务编号、执行批次与通道ID")
    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="通道ID必须是数字")
    records, message = service.submit_batch(task_code, batch_no, channel_id)
    if records is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={"接入记录": records, "数量": len(records)})


@router.get("/trace", response_model=dict)
def trace_batch(
    task_code: str = Query(..., description="任务编号"),
    batch_no: str = Query(..., description="执行批次"),
) -> dict:
    """按批次完整回查其采用的字段定义、全部页面记录与接入记录。"""
    result = service.trace_batch(task_code, batch_no)
    if result is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_code} 批次 {batch_no} 没有采集结果")
    return result


@router.get("/ingestion-records/export")
def export_ingestion_records() -> dict[str, Any]:
    items, total = service.list_ingestion_records(page=1, size=10000)
    return {"module": "rpa_ingestion_record", "total": total, "items": items}


@router.get("/ingestion-records", response_model=PageResult[dict])
def list_ingestion_records(
    request: Request,
    task_code: str | None = Query(default=None, description="按任务编号过滤"),
    batch_no: str | None = Query(default=None, description="按执行批次过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    extra = {
        k: v
        for k, v in request.query_params.items()
        if k not in ("task_code", "batch_no", "page", "size") and v.strip()
    }
    items, total = service.list_ingestion_records(
        task_code=task_code, batch_no=batch_no, page=page, size=size, extra=extra
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_result(entry_id: int) -> dict:
    entry = service.get_result(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采集结果记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_result(payload: EntryPayload) -> ActionResult:
    """新建采集结果记录，自动冻结任务当前生效字段定义版本与完整快照。"""
    entry, missing = service.create_result(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段或字段定义缺失：{'、'.join(missing)}")
    return ActionResult(ok=True, message="采集结果记录已建立，等待字段提取", entry=entry)


@router.post("/{entry_id}/extract", response_model=ActionResult)
def extract_result(entry_id: int, payload: EntryPayload) -> ActionResult:
    """提交从可重复读取页面取到的字段值；必填字段定位失败会带动作位置阻断交付。"""
    entry, message = service.extract_result(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    ok = entry["状态"] != "定位失败"
    return ActionResult(ok=ok, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """确认结果、重试提取两个动作；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
