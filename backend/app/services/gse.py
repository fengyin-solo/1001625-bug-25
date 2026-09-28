"""保障车辆业务规则：保养到期判定、状态流转与字段校验都收在这里。

口径约定：
- 车辆状态（外部字段「车辆状态」与内部 status）按上次/下次保养日推导，
  保养中、已报废属于显式流转出的状态，不参与自动覆盖。
- 保养周期按车辆类别区分，未命中映射时沿用默认周期。
- 已报废与保养互斥；同一车辆在保养中重复安排保养只保留一次并说明原因。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "gse"
REQUIRED_FIELDS = ["车辆编号", "车辆类别", "适用作业"]
DETAIL_FIELDS = ["停放区域", "上次保养日", "下次保养日", "责任人"]
DATE_FIELDS = ["上次保养日", "下次保养日"]

STATUS_DUE = "待保养"
STATUS_READY = "可用"
STATUS_SERVICING = "保养中"
STATUS_SCRAPPED = "已报废"
STATUS_ORDER = [STATUS_DUE, STATUS_READY, STATUS_SERVICING, STATUS_SCRAPPED]

ACTION_SCHEDULE = "安排保养"
ACTION_CONFIRM = "确认可用"
ACTION_SCRAP = "报废车辆"
ACTION_RULES = {
    ACTION_SCHEDULE: STATUS_SERVICING,
    ACTION_CONFIRM: STATUS_READY,
    ACTION_SCRAP: STATUS_SCRAPPED,
}

# 车辆类别不同，沿用不同保养周期（天）；未命中的类别沿用默认周期。
MAINTENANCE_CYCLE_DAYS: dict[str, int] = {
    "加油车": 45,
    "牵引车": 90,
    "摆渡车": 60,
    "传送车": 60,
    "除冰车": 30,
    "行李车": 60,
    "平台车": 90,
}
DEFAULT_CYCLE_DAYS = 90


def _parse_date(value: Any) -> date | None:
    """严格解析 YYYY-MM-DD；空值、格式错误或日期本身不存在（如 2026-02-30）都返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def maintenance_cycle_days(category: Any) -> int:
    """按车辆类别取保养周期：类别名命中映射时取专属周期，否则取默认周期。"""
    name = str(category or "").strip()
    for keyword, days in MAINTENANCE_CYCLE_DAYS.items():
        if keyword in name:
            return days
    return DEFAULT_CYCLE_DAYS


def derive_status(entry: dict[str, Any], *, today: date | None = None) -> str:
    """按上次/下次保养日判定到期状态。

    保养中、已报废是显式流转出来的状态，保养日再怎么变也不自动覆盖；
    其余状态下：保养日缺失/无法解析、上次晚于下次、下次保养日已过期，都判为待保养。
    """
    today = today or date.today()
    current = str(entry.get("status") or "")
    if current in (STATUS_SERVICING, STATUS_SCRAPPED):
        return current
    last_date = _parse_date(entry.get("上次保养日"))
    next_date = _parse_date(entry.get("下次保养日"))
    if last_date is None or next_date is None or last_date > next_date:
        return STATUS_DUE
    if next_date < today:
        return STATUS_DUE
    return STATUS_READY


def sync_status(entry: dict[str, Any]) -> dict[str, Any]:
    """把推导出的状态同步回内部 status 与对外展示的「车辆状态」，保证两者一致。"""
    status = derive_status(entry)
    entry["status"] = status
    entry["车辆状态"] = status
    entry["pending"] = status != STATUS_SCRAPPED
    entry["abnormal"] = status == STATUS_DUE
    return entry


def _validate_dates(values: dict[str, Any]) -> tuple[date | None, date | None, list[str]]:
    """校验保养日：缺失或格式异常按失败处理；上次晚于下次（填反）也拦下。"""
    errors: list[str] = []
    last_date = _parse_date(values.get("上次保养日"))
    next_date = _parse_date(values.get("下次保养日"))
    if str(values.get("上次保养日") or "").strip() and last_date is None:
        errors.append("上次保养日格式异常，应为 YYYY-MM-DD")
    if str(values.get("下次保养日") or "").strip() and next_date is None:
        errors.append("下次保养日格式异常，应为 YYYY-MM-DD")
    if last_date is not None and next_date is not None and last_date > next_date:
        errors.append("上次保养日不能晚于下次保养日，请核对是否填反")
    return last_date, next_date, errors


class GseService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        # 列表读出来先按保养日重新推导状态并同步回仓库，避免「日期已过期但还显示可用」。
        rows = [sync_status(row) for row in rows]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("车辆编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        # 详情与列表走同一套推导口径，保证两处数字/状态完全相同。
        return sync_status(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        errors = [
            f"缺少必填字段：{field}"
            for field in REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        vehicle_no = str(values.get("车辆编号") or "").strip()
        if vehicle_no and any(
            str(row.get("车辆编号") or "").strip() == vehicle_no
            for row in store.rows(MODULE)
        ):
            errors.append(f"车辆编号 {vehicle_no} 已存在，不能重复登记")
        _, _, date_errors = _validate_dates(values)
        errors.extend(date_errors)
        if errors:
            return None, errors
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + DETAIL_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip():
                entry[field] = str(value).strip()
        entry["status"] = STATUS_DUE
        rows.append(entry)
        return sync_status(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"保障车辆 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于保障车辆可执行范围"
        values = values or {}
        sync_status(entry)
        current = str(entry.get("status") or "")

        if action == ACTION_SCRAP:
            # 报废与保养互斥：保养中的车辆必须先确认可用才能报废。
            if current == STATUS_SCRAPPED:
                return None, "车辆已报废，不能重复报废"
            if current == STATUS_SERVICING:
                return None, "车辆保养中，与报废互斥，请先确认可用后再报废"
            entry["status"] = STATUS_SCRAPPED
            entry["车辆状态"] = STATUS_SCRAPPED
            entry["pending"] = False
            entry["abnormal"] = False
            return entry, "保障车辆已报废车辆"

        if action == ACTION_SCHEDULE:
            # 报废车辆不能再安排保养（报废与保养互斥）。
            if current == STATUS_SCRAPPED:
                return None, "车辆已报废，不能再安排保养"
            # 保养中的车辆重复提交：只保留第一次的安排，不生成重复记录。
            if current == STATUS_SERVICING:
                return None, "车辆已在保养中，重复安排保养不会重复生成记录"

            raw_last = values.get("上次保养日", entry.get("上次保养日"))
            raw_next = values.get("下次保养日")
            if not str(raw_last or "").strip():
                return None, "安排保养失败：缺少上次保养日"
            last_date = _parse_date(raw_last)
            if last_date is None:
                return None, "安排保养失败：上次保养日格式异常，应为 YYYY-MM-DD"
            if str(raw_next or "").strip():
                next_date = _parse_date(raw_next)
                if next_date is None:
                    return None, "安排保养失败：下次保养日格式异常，应为 YYYY-MM-DD"
                if last_date > next_date:
                    return None, "安排保养失败：上次保养日不能晚于下次保养日，请核对是否填反"
            else:
                # 下次保养日未填时，按车辆类别的保养周期自动推算。
                cycle_days = maintenance_cycle_days(entry.get("车辆类别"))
                next_date = last_date + timedelta(days=cycle_days)

            entry["上次保养日"] = last_date.isoformat()
            entry["下次保养日"] = next_date.isoformat()
            entry["status"] = STATUS_SERVICING
            entry["车辆状态"] = STATUS_SERVICING
            entry["pending"] = True
            entry["abnormal"] = False
            return entry, (
                f"保障车辆已安排保养，下次保养日按{entry.get('车辆类别') or '默认'}"
                f"{maintenance_cycle_days(entry.get('车辆类别'))}天周期推算为 {entry['下次保养日']}"
            )

        # 确认可用：保养完成后才能确认；保养日异常或已到期一律拦下。
        if current == STATUS_SCRAPPED:
            return None, "车辆已报废，不能确认可用"
        if current != STATUS_SERVICING:
            if current == STATUS_READY:
                return None, "车辆当前已是可用状态，无需重复确认"
            if derive_status(entry) == STATUS_DUE:
                return None, "确认可用失败：下次保养日已过期或保养日异常，需重新安排保养后再确认"
            return None, "车辆尚未安排保养，需先安排保养并完成后才能确认可用"
        last_date = _parse_date(entry.get("上次保养日"))
        next_date = _parse_date(entry.get("下次保养日"))
        if last_date is None or next_date is None:
            return None, "确认可用失败：保养日缺失或格式异常，应为 YYYY-MM-DD"
        if last_date > next_date:
            return None, "确认可用失败：上次保养日晚于下次保养日，请核对保养记录"
        if next_date < date.today():
            return None, "确认可用失败：下次保养日已过期，需重新安排保养"
        entry["status"] = STATUS_READY
        entry["车辆状态"] = STATUS_READY
        entry["pending"] = True
        entry["abnormal"] = False
        return entry, "保障车辆已确认可用"
