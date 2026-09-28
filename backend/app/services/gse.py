"""保障车辆业务规则：状态流转、保养到期判定、字段校验与筛选口径都收在这里。

状态由「上次保养日 / 下次保养日 + 车辆类别周期」推导并回写「车辆状态」，
保证列表、详情与动作执行看到的口径一致；报废与保养互斥，重复安排保养幂等拒绝。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

MODULE = "gse"
REQUIRED_FIELDS = ["车辆编号", "车辆类别", "适用作业"]
DATE_FIELDS = ["上次保养日", "下次保养日"]
STATUS_PENDING = "待保养"
STATUS_AVAILABLE = "可用"
STATUS_MAINTAINING = "保养中"
STATUS_SCRAPPED = "已报废"
ACTION_RULES = {"安排保养": STATUS_MAINTAINING, "确认可用": STATUS_AVAILABLE, "报废车辆": STATUS_SCRAPPED}

# 车辆类别不同，沿用不同保养周期（天）；未列明的类别走默认周期。
CATEGORY_CYCLE_DAYS: dict[str, int] = {
    "牵引车": 60,
    "摆渡车": 90,
    "加油车": 45,
    "行李传送车": 90,
    "传送带车": 90,
    "集装板拖车": 180,
    "客梯车": 120,
    "除冰车": 45,
    "升降平台车": 120,
}
DEFAULT_CYCLE_DAYS = 90


def _parse_day(raw: Any) -> date | None:
    """严格解析 YYYY-MM-DD；空白、格式错、日期不存在一律视为无效。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def cycle_days_for(category: Any) -> int:
    return CATEGORY_CYCLE_DAYS.get(str(category or "").strip(), DEFAULT_CYCLE_DAYS)


def _sync_status(entry: dict[str, Any], *, today: date | None = None) -> None:
    """按保养日期推导状态并同步「车辆状态」。

    已报废、保养中是动作造成的强状态，不由日期改写；其余车辆按下次保养日
    是否到期在「待保养 / 可用」之间切换。日期缺失或异常时视为待保养（到期）。
    """
    today = today or date.today()
    # 已报废、保养中是动作造成的强状态，不被日期推导改写；到期与否留到
    # 「确认可用」时再校验，避免保养中的车被列表读成待保养。
    if entry.get("status") in (STATUS_SCRAPPED, STATUS_MAINTAINING):
        synced = str(entry["status"])
    else:
        last_day = _parse_day(entry.get("上次保养日"))
        next_day = _parse_day(entry.get("下次保养日"))
        # 日期填反（下次早于上次）本身就是异常数据，按到期处理，倒逼先安排保养。
        due = next_day is None or last_day is None or next_day < last_day or next_day < today
        synced = STATUS_PENDING if due else STATUS_AVAILABLE
    entry["status"] = synced
    entry["车辆状态"] = synced
    entry["pending"] = synced != STATUS_SCRAPPED


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
        for row in rows:
            _sync_status(row)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("车辆编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            _sync_status(entry)
        return entry

    def stats(self) -> dict[str, int]:
        """列表卡片与车辆详情共用同一批行、同一套判定，数字天然一致。"""
        rows = store.rows(MODULE)
        for row in rows:
            _sync_status(row)
        counts = {STATUS_PENDING: 0, STATUS_AVAILABLE: 0, STATUS_MAINTAINING: 0, STATUS_SCRAPPED: 0}
        for row in rows:
            counts[str(row.get("status"))] = counts.get(str(row.get("status")), 0) + 1
        return {
            "在册车辆": len(rows),
            "待保养车辆": counts[STATUS_PENDING],
            "保养中车辆": counts[STATUS_MAINTAINING],
            "可用车辆": counts[STATUS_AVAILABLE],
            "已报废车辆": counts[STATUS_SCRAPPED],
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        vehicle_no = str(values.get("车辆编号")).strip()
        if any(str(row.get("车辆编号", "")).strip() == vehicle_no for row in store.rows(MODULE)):
            return None, f"车辆编号「{vehicle_no}」已存在，不能重复登记；如要保养请对在册车辆安排保养"

        # 保养日期若填写就必须合法；填反了同样按失败处理，避免脏数据进入列表。
        last_day = _parse_day(values.get("上次保养日"))
        next_day = _parse_day(values.get("下次保养日"))
        for field in DATE_FIELDS:
            raw = str(values.get(field) or "").strip()
            if raw and _parse_day(raw) is None:
                return None, f"{field}格式无效，应为 YYYY-MM-DD，请核对后重新提交"
        if last_day and next_day and next_day < last_day:
            return None, "下次保养日早于上次保养日，两次保养日可能填反了，请核对后重新提交"

        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS:
            entry[field] = str(values.get(field)).strip()
        for field in ["停放区域", "责任人"]:
            entry[field] = str(values.get(field) or "").strip()
        entry["上次保养日"] = last_day.isoformat() if last_day else ""
        entry["下次保养日"] = next_day.isoformat() if next_day else ""
        entry["status"] = STATUS_PENDING
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        _sync_status(entry)
        return entry, ""

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"保障车辆 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于保障车辆可执行范围"
        _sync_status(entry)
        values = values or {}

        if action == "安排保养":
            return self._schedule_maintenance(entry, values)
        if action == "确认可用":
            return self._confirm_available(entry)
        return self._scrap(entry)

    def _schedule_maintenance(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        # 报废与保养互斥：终态车辆不再允许安排保养。
        if entry["status"] == STATUS_SCRAPPED:
            return None, "车辆已报废，报废与保养互斥，不能再安排保养"
        # 同一车辆重复提交只保留一次：保养中的单不重复生成，直接说明原因。
        if entry["status"] == STATUS_MAINTAINING:
            return None, f"车辆「{entry.get('车辆编号')}」已在保养中，重复安排保养不会重复建单"

        raw_last = str(values.get("上次保养日") or "").strip()
        raw_next = str(values.get("下次保养日") or "").strip()
        if not raw_last:
            return None, "缺少上次保养日，无法安排保养，请补充保养实际完成日期"
        last_day = _parse_day(raw_last)
        if last_day is None:
            return None, "上次保养日格式无效，应为 YYYY-MM-DD，保养未生效，请核对后重新提交"
        next_day = _parse_day(raw_next) if raw_next else None
        if raw_next and next_day is None:
            return None, "下次保养日格式无效，应为 YYYY-MM-DD，保养未生效，请核对后重新提交"
        if next_day is None:
            # 下次保养日缺失不算失败：按车辆类别对应周期顺延。
            next_day = last_day + timedelta(days=cycle_days_for(entry.get("车辆类别")))
        if next_day < last_day:
            return None, "下次保养日早于上次保养日，两次保养日可能填反了，请核对后重新提交"
        if last_day > date.today():
            return None, "上次保养日不能晚于今天，保养尚未发生，请核对后重新提交"

        entry["上次保养日"] = last_day.isoformat()
        entry["下次保养日"] = next_day.isoformat()
        entry["status"] = STATUS_MAINTAINING
        entry["车辆状态"] = STATUS_MAINTAINING
        entry["pending"] = True
        return entry, f"保障车辆已安排保养，预计下次保养日 {next_day.isoformat()}"

    def _confirm_available(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry["status"] == STATUS_SCRAPPED:
            return None, "车辆已报废，报废与保养互斥，不能确认可用"
        if entry["status"] == STATUS_AVAILABLE:
            return None, "车辆当前已是可用状态，无需重复确认"

        last_day = _parse_day(entry.get("上次保养日"))
        next_day = _parse_day(entry.get("下次保养日"))
        if last_day is None or next_day is None:
            return None, "保养日期缺失，无法确认可用，请先安排保养补齐上次保养日与下次保养日"
        if next_day < last_day:
            return None, "下次保养日早于上次保养日，保养记录异常，请先核对保养日期"
        if next_day < date.today():
            # 保养完成时发现新周期已到期：回落到待保养，车辆状态保持一致。
            entry["status"] = STATUS_PENDING
            entry["车辆状态"] = STATUS_PENDING
            entry["pending"] = True
            return None, f"下次保养日 {next_day.isoformat()} 已过期，需重新安排保养，不能确认可用"

        entry["status"] = STATUS_AVAILABLE
        entry["车辆状态"] = STATUS_AVAILABLE
        entry["pending"] = True
        return entry, "保障车辆已确认可用"

    def _scrap(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry["status"] == STATUS_SCRAPPED:
            return None, "车辆已是报废状态，无需重复报废"
        entry["status"] = STATUS_SCRAPPED
        entry["车辆状态"] = STATUS_SCRAPPED
        entry["pending"] = False
        return entry, "保障车辆已报废"
