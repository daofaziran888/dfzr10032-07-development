"""RPA采集结果数据集业务规则。

设计口径（对应需求）：

1. 采集结果按任务定义从可重复读取的页面提取字段，记录里同时保留动作位置与执行批次。
2. 必填字段定位失败时，失败明细要能追到具体动作（动作位置），该条记录置为「定位失败」，
   字段值不会以正常空值落库交付，也不允许确认进入接入通道。
3. 重试同一页面时，同一（任务编号、执行批次、页面标识）下只允许一条「已确认」记录进入
   接入通道；后确认的记录会把先前的已确认记录顶替为「已顶替」。
4. 数据接入通道按「任务编号 + 执行批次」提交，整批没有阻断状态、且存在已确认记录才放行，
   并为每个页面生成一条与原页面关联的接入记录。
5. 每份提交给接入通道的批次都冻结其采用的字段定义版本与完整字段快照，支持按批次完整回查。
6. 批次仍在提取时收到新版本的结果字段定义：新定义另起版本，旧定义保持「已归档」，
   已建记录冻结旧定义快照不受影响，之后新建的记录才采用新版本。
"""
from __future__ import annotations

import copy
from typing import Any

from app.store import store

MODULE = "rpa_dataset"
DEF_MODULE = "rpa_field_def"
INGEST_MODULE = "rpa_ingestion_record"
TASK_MODULE = "robot_task"
CHANNEL_MODULE = "ingestion"

# 采集结果记录状态
STATUS_EXTRACTING = "提取中"
STATUS_CONFIRMED = "已确认"
STATUS_LOCATE_FAILED = "定位失败"
STATUS_SUPERSEDED = "已顶替"
STATUS_DELIVERED = "已接入"
RESULT_STATUSES = [
    STATUS_EXTRACTING,
    STATUS_CONFIRMED,
    STATUS_LOCATE_FAILED,
    STATUS_SUPERSEDED,
    STATUS_DELIVERED,
]
# 阻断接入通道提交的状态
BLOCKING_STATUSES = [STATUS_EXTRACTING, STATUS_LOCATE_FAILED]

# 字段定义版本状态
DEF_ACTIVE = "生效中"
DEF_ARCHIVED = "已归档"

RESULT_REQUIRED_FIELDS = ["任务编号", "执行批次", "页面标识", "页面地址"]
DEF_REQUIRED_FIELDS = ["任务编号", "字段定义"]


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _as_fields(raw: Any) -> list[dict[str, Any]]:
    """把入参字段定义规范成列表，每项形如 {字段名, 动作位置, 定位器, 是否必填}。"""
    if isinstance(raw, str):
        # 允许用英文逗号/中文逗号分隔的简写，默认全部必填
        names = [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]
        return [{"字段名": name, "动作位置": "", "定位器": "", "是否必填": True} for name in names]
    if isinstance(raw, list):
        fields: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("字段名") or "").strip()
            if not name:
                continue
            fields.append({
                "字段名": name,
                "动作位置": str(item.get("动作位置") or "").strip(),
                "定位器": str(item.get("定位器") or "").strip(),
                "是否必填": bool(item.get("是否必填", True)),
            })
        return fields
    return []


class RpaDatasetService:
    # ------------------------------------------------------------------ 字段定义版本
    def list_definitions(
        self,
        *,
        task_code: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(DEF_MODULE)
        if task_code:
            rows = [row for row in rows if task_code in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_definition(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(DEF_MODULE, entry_id)

    def active_definition(self, task_code: str) -> dict[str, Any] | None:
        for row in store.rows(DEF_MODULE):
            if row.get("任务编号") == task_code and row.get("状态") == DEF_ACTIVE:
                return row
        return None

    def create_definition(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in DEF_REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        task_code = str(values["任务编号"]).strip()
        if not store.rows(TASK_MODULE) or not any(
            row.get("任务编号") == task_code for row in store.rows(TASK_MODULE)
        ):
            return None, [f"任务编号 {task_code} 在RPA任务编排中不存在"]
        fields = _as_fields(values.get("字段定义"))
        if not fields:
            return None, ["字段定义"]

        rows = store.rows(DEF_MODULE)
        # 同一任务新版本生效前，旧版本一律归档：旧批次仍按冻结快照回查，不受影响。
        for row in rows:
            if row.get("任务编号") == task_code and row.get("状态") == DEF_ACTIVE:
                row["状态"] = DEF_ARCHIVED
        same_task_versions = [int(row.get("定义版本", 0)) for row in rows if row.get("任务编号") == task_code]
        version_no = max(same_task_versions, default=0) + 1
        entry = {
            "id": _next_id(rows),
            "定义编号": str(values.get("定义编号") or f"RFD-{task_code}-V{version_no}"),
            "任务编号": task_code,
            "定义版本": version_no,
            "状态": DEF_ACTIVE,
            "字段定义": fields,
            "创建时间": str(values.get("创建时间") or ""),
        }
        rows.append(entry)
        return entry, []

    # ------------------------------------------------------------------ 采集结果记录
    def list_results(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        task_code: str | None = None,
        batch_no: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if task_code:
            rows = [row for row in rows if row.get("任务编号") == task_code]
        if batch_no:
            rows = [row for row in rows if row.get("执行批次") == batch_no]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_result(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_result(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in RESULT_REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        task_code = str(values["任务编号"]).strip()
        definition = self.active_definition(task_code)
        if definition is None:
            return None, [f"任务 {task_code} 没有生效中的结果字段定义，无法提取字段"]

        rows = store.rows(MODULE)
        frozen_fields = copy.deepcopy(definition["字段定义"])
        entry = {
            "id": _next_id(rows),
            "记录编号": str(values.get("记录编号") or f"RDS-{len(rows) + 1:04d}"),
            "任务编号": task_code,
            "执行批次": str(values["执行批次"]).strip(),
            "页面标识": str(values["页面标识"]).strip(),
            "页面地址": str(values["页面地址"]).strip(),
            "动作位置": str(values.get("动作位置") or "").strip(),
            "定义编号": definition["定义编号"],
            "定义版本": definition["定义版本"],
            # 冻结快照：批次仍在提取时字段定义改版，也不影响这条记录的回查口径。
            "字段定义快照": frozen_fields,
            "字段采集值": [
                {
                    "字段名": spec["字段名"],
                    "动作位置": spec["动作位置"],
                    "定位器": spec["定位器"],
                    "是否必填": spec["是否必填"],
                    "采集值": None,
                    "提取状态": "待提取",
                }
                for spec in frozen_fields
            ],
            "失败明细": [],
            "状态": STATUS_EXTRACTING,
            "status": STATUS_EXTRACTING,
            "pending": True,
            "abnormal": False,
        }
        rows.append(entry)
        return entry, []

    def extract_result(
        self, entry_id: int, extracted: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """登记一次可重复读取页面的字段提取结果。

        extracted: {字段名: 值}；值为 None/空串视为该字段定位失败。
        必填字段定位失败 → 整条记录「定位失败」，失败明细带动作位置，值不按空值交付。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集结果记录 {entry_id} 不存在或已归档"
        if entry["状态"] != STATUS_EXTRACTING:
            return None, f"记录当前为「{entry['状态']}」，只有「{STATUS_EXTRACTING}」状态可提取字段"

        failures: list[dict[str, Any]] = []
        for item in entry["字段采集值"]:
            name = item["字段名"]
            raw = extracted.get(name)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                item["采集值"] = None
                item["提取状态"] = "定位失败"
                # 失败可追到具体动作：动作位置 + 定位器都落在明细里
                failures.append({
                    "字段名": name,
                    "是否必填": item["是否必填"],
                    "动作位置": item["动作位置"],
                    "定位器": item["定位器"],
                    "失败原因": "页面定位器未取到字段值",
                })
            else:
                item["采集值"] = raw
                item["提取状态"] = "已提取"

        entry["失败明细"] = failures
        required_failed = [f for f in failures if f["是否必填"]]
        if required_failed:
            # 必填失败：记录保持阻断状态，字段值不会作为正常空值交付。
            entry["状态"] = STATUS_LOCATE_FAILED
            entry["status"] = STATUS_LOCATE_FAILED
            entry["abnormal"] = True
            names = "、".join(
                f"{f['字段名']}（动作位置：{f['动作位置'] or '未标注'}）" for f in required_failed
            )
            return entry, f"必填字段定位失败：{names}，记录已置为定位失败，不能按空值交付"

        entry["状态"] = STATUS_CONFIRMED
        entry["status"] = STATUS_CONFIRMED
        entry["pending"] = False
        entry["abnormal"] = False
        return entry, "字段提取完成，记录已确认"

    def confirm_result(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集结果记录 {entry_id} 不存在或已归档"
        if entry["状态"] != STATUS_CONFIRMED:
            return None, f"记录当前为「{entry['状态']}」，只有提取成功的记录可确认"

        same_page = self._same_page_records(entry)
        # 已接入通道的页面不允许再确认新记录，保证进入通道的唯一一条不被改写。
        if any(row["状态"] == STATUS_DELIVERED for row in same_page):
            return None, "该页面已有记录进入接入通道，不能再确认新的采集记录"
        # 同一页面重试：顶替先前的已确认记录，始终只保留一条已确认记录。
        for row in same_page:
            if row is not entry and row["状态"] == STATUS_CONFIRMED:
                row["状态"] = STATUS_SUPERSEDED
                row["status"] = STATUS_SUPERSEDED
                row["pending"] = False
        return entry, "采集结果已确认，可随批次提交接入通道"

    def retry_result(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """对定位失败的记录重新提取（页面可重复读取），清空旧失败值后回到提取中。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集结果记录 {entry_id} 不存在或已归档"
        if entry["状态"] != STATUS_LOCATE_FAILED:
            return None, f"记录当前为「{entry['状态']}」，只有定位失败的记录可重试"
        for item in entry["字段采集值"]:
            item["采集值"] = None
            item["提取状态"] = "待提取"
        entry["失败明细"] = []
        entry["状态"] = STATUS_EXTRACTING
        entry["status"] = STATUS_EXTRACTING
        entry["pending"] = True
        entry["abnormal"] = False
        return entry, "已重置提取结果，可重新从页面提取字段"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "确认结果":
            return self.confirm_result(entry_id)
        if action == "重试提取":
            return self.retry_result(entry_id)
        return None, f"动作「{action}」不属于RPA采集结果数据集可执行范围"

    def _same_page_records(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            row
            for row in store.rows(MODULE)
            if row.get("任务编号") == entry["任务编号"]
            and row.get("执行批次") == entry["执行批次"]
            and row.get("页面标识") == entry["页面标识"]
        ]

    # ------------------------------------------------------------------ 接入通道提交
    def submit_batch(
        self, task_code: str, batch_no: str, channel_id: int
    ) -> tuple[list[dict[str, Any]] | None, str]:
        channel = store.find(CHANNEL_MODULE, channel_id)
        if channel is None:
            return None, f"接入通道 {channel_id} 不存在或已归档"

        rows = [
            row
            for row in store.rows(MODULE)
            if row.get("任务编号") == task_code and row.get("执行批次") == batch_no
        ]
        if not rows:
            return None, f"任务 {task_code} 批次 {batch_no} 下没有采集结果记录"
        # 同一任务+批次只要生成过接入记录就不允许重复提交，避免通道出现重复数据。
        already = [
            rec
            for rec in store.rows(INGEST_MODULE)
            if rec.get("任务编号") == task_code and rec.get("执行批次") == batch_no
        ]
        if already:
            return None, "该批次已提交过接入通道，不允许重复提交"

        blocking = [row for row in rows if row["状态"] in BLOCKING_STATUSES]
        if blocking:
            detail = "、".join(
                f"{row['记录编号']}（{row['状态']}）" for row in blocking[:5]
            )
            return None, f"批次仍有未就绪记录：{detail}，处理完成后再提交"

        confirmed = [row for row in rows if row["状态"] == STATUS_CONFIRMED]
        if not confirmed:
            return None, "批次内没有已确认记录，不能提交接入通道"

        ingest_rows = store.rows(INGEST_MODULE)
        created: list[dict[str, Any]] = []
        for row in confirmed:
            record = {
                "id": _next_id(ingest_rows),
                "接入记录编号": f"INR-{len(ingest_rows) + 1:04d}",
                "通道编号": channel.get("通道编号"),
                "任务编号": task_code,
                "执行批次": batch_no,
                "采集记录编号": row["记录编号"],
                "页面标识": row["页面标识"],
                "页面地址": row["页面地址"],
                "定义编号": row["定义编号"],
                "定义版本": row["定义版本"],
                "字段定义快照": copy.deepcopy(row["字段定义快照"]),
                "字段采集值": copy.deepcopy(row["字段采集值"]),
                "动作位置": row["动作位置"],
                "状态": "已生成",
                "status": "已生成",
                "pending": False,
                "abnormal": False,
            }
            ingest_rows.append(record)
            created.append(record)
            row["状态"] = STATUS_DELIVERED
            row["status"] = STATUS_DELIVERED
            row["pending"] = False
        return created, f"批次 {batch_no} 已按 {len(created)} 条已确认记录生成接入记录"

    def list_ingestion_records(
        self,
        *,
        task_code: str | None = None,
        batch_no: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(INGEST_MODULE)
        if task_code:
            rows = [row for row in rows if row.get("任务编号") == task_code]
        if batch_no:
            rows = [row for row in rows if row.get("执行批次") == batch_no]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def trace_batch(self, task_code: str, batch_no: str) -> dict[str, Any] | None:
        """按批次完整回查：采用的字段定义版本、每条页面记录、接入记录一次给齐。"""
        rows = [
            row
            for row in store.rows(MODULE)
            if row.get("任务编号") == task_code and row.get("执行批次") == batch_no
        ]
        if not rows:
            return None
        ingestion = [
            row
            for row in store.rows(INGEST_MODULE)
            if row.get("任务编号") == task_code and row.get("执行批次") == batch_no
        ]
        definitions = [
            row
            for row in store.rows(DEF_MODULE)
            if row.get("任务编号") == task_code
            and row.get("定义编号") in {r["定义编号"] for r in rows}
        ]
        return {
            "任务编号": task_code,
            "执行批次": batch_no,
            "字段定义版本": [
                {
                    "定义编号": d["定义编号"],
                    "定义版本": d["定义版本"],
                    "状态": d["状态"],
                    "字段定义": copy.deepcopy(d["字段定义"]),
                }
                for d in definitions
            ],
            "采集结果记录": copy.deepcopy(rows),
            "接入记录": copy.deepcopy(ingestion),
            "汇总": {
                "记录总数": len(rows),
                "已确认": sum(1 for r in rows if r["状态"] == STATUS_CONFIRMED),
                "已接入": sum(1 for r in rows if r["状态"] == STATUS_DELIVERED),
                "定位失败": sum(1 for r in rows if r["状态"] == STATUS_LOCATE_FAILED),
                "已顶替": sum(1 for r in rows if r["状态"] == STATUS_SUPERSEDED),
                "提取中": sum(1 for r in rows if r["状态"] == STATUS_EXTRACTING),
                "接入记录数": len(ingestion),
            },
        }
