"""Consistent fleet overview and durable operator tasks."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app import data, model, operations, store, weather

Priority = Literal["normal", "high", "critical"]
TaskStatus = Literal["open", "in_progress", "done"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=140)]
Owner = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
IdempotencyKey = Annotated[str, Header(min_length=1, max_length=100)]
_TASK_FIELDS = ("title", "owner", "due_at", "priority", "status", "note")
_TASK_COLUMNS = ("id", "shipment_id", *_TASK_FIELDS, "version", "created_at", "updated_at")
_PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2}
_PROGRESS_LABELS = {"at_node": "В узле", "in_transit": "В пути", "delivered": "Доставлено"}
_ELEVATED_SLACK_H = 24
_ACTIVITY_LIMIT = 20


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("due_at", mode="before", check_fields=False)
    @classmethod
    def due_is_aware_iso(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 40 or "T" not in value:
            raise ValueError("Укажите срок в формате ISO 8601 со смещением")
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is None:
                raise ValueError
            return parsed.astimezone(timezone.utc)
        except (ValueError, OverflowError) as exc:
            raise ValueError("Укажите срок в формате ISO 8601 со смещением") from exc


class TaskCreate(TaskInput):
    shipment_id: str = Field(min_length=1, max_length=40)
    title: Title
    owner: Owner = ""
    due_at: datetime | None = None
    priority: Priority = "normal"
    status: TaskStatus = "open"
    note: Note = ""


class TaskPatch(TaskInput):
    expected_version: int = Field(strict=True, ge=1, le=2**63 - 1)
    title: Title | None = None
    owner: Owner | None = None
    due_at: datetime | None = None
    priority: Priority | None = None
    status: TaskStatus | None = None
    note: Note | None = None

    @model_validator(mode="after")
    def has_valid_updates(self):
        requested = self.model_fields_set - {"expected_version"}
        if not requested:
            raise ValueError("Укажите хотя бы одно поле задачи для изменения")
        if any(getattr(self, key) is None for key in requested - {"due_at"}):
            raise ValueError("Значение null допустимо только для срока")
        return self


def _task(row, shipment_name: str) -> dict:
    return {**{key: row[key] for key in _TASK_COLUMNS}, "shipment_name": shipment_name}


def _values(body: TaskInput) -> dict:
    values = body.model_dump(exclude_unset=isinstance(body, TaskPatch))
    if values.get("due_at") is not None:
        values["due_at"] = values["due_at"].isoformat()
    return values


def _create_task(body: TaskCreate, idempotency_key: str) -> dict:
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(422, "Idempotency-Key должен быть непустым")
    values = _values(body)
    creation_hash = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT * FROM workspace_tasks WHERE idempotency_key=?", (key,)).fetchone()
        if previous:
            if previous["creation_hash"] != creation_hash:
                raise HTTPException(409, "Этот ключ уже использован для другой задачи")
            shipment = operations._shipment(conn, previous["shipment_id"])
            return _task(previous, shipment["name"])
        shipment = operations._shipment(conn, body.shipment_id)
        timestamp = operations._now().isoformat()
        record = {
            **values, "id": uuid.uuid4().hex, "version": 1, "created_at": timestamp, "updated_at": timestamp,
        }
        conn.execute(
            "INSERT INTO workspace_tasks (id,shipment_id,title,owner,due_at,priority,status,note,version,created_at,"
            "updated_at,idempotency_key,creation_hash) VALUES (:id,:shipment_id,:title,:owner,:due_at,:priority,"
            ":status,:note,:version,:created_at,:updated_at,:idempotency_key,:creation_hash)",
            {**record, "idempotency_key": key, "creation_hash": creation_hash},
        )
        return _task(record, shipment["name"])


def _update_task(task_id: str, body: TaskPatch) -> dict:
    changes = _values(body)
    expected = changes.pop("expected_version")
    with store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM workspace_tasks WHERE id=?", (task_id,)).fetchone()
        if current is None:
            raise HTTPException(404, "Задача не найдена")
        shipment = operations._shipment(conn, current["shipment_id"])
        if current["version"] != expected:
            raise HTTPException(409, "Задача уже изменена. Обновите рабочую область")
        if all(current[key] == value for key, value in changes.items()):
            return _task(current, shipment["name"])
        record = {**dict(current), **changes, "expected": expected, "updated_at": operations._now().isoformat()}
        updated = conn.execute(
            "UPDATE workspace_tasks SET title=:title,owner=:owner,due_at=:due_at,priority=:priority,"
            "status=:status,note=:note,version=version+1,updated_at=:updated_at "
            "WHERE id=:id AND version=:expected RETURNING *",
            record,
        ).fetchone()
        return _task(updated, shipment["name"])


def _risk(view: dict, baseline: dict) -> tuple[str, str | None]:
    if view["progress_status"] == "delivered":
        return "complete", None
    if view["blocked"]:
        return "critical", "blocked"
    if view["late_h"] and view["late_h"] > 0:
        return "critical", "late"
    for leg in baseline["legs"]:
        if leg.get("reported_delay_h", 0) > 0:
            return "elevated", "reported_delay"
        if not leg.get("in_transit") and leg.get("weather", {}).get("wave_m", 0) >= 2:
            return "elevated", "sea_disruption"
    deadline = operations._timestamp(view["deadline"])
    if baseline["remaining_h"] is not None:
        eta = operations._timestamp(view["eta"])
        if 0 <= (deadline - eta).total_seconds() / 3600 < _ELEVATED_SLACK_H:
            return "elevated", "deadline_slack"
    return "normal", None


def _shipment_row(detail: dict, demo: bool) -> dict:
    view, baseline = detail["shipment"], detail["baseline"]
    node = data.NODES.get(view["current_node"])
    active_leg = next((leg for leg in detail["plan"]["legs"] if leg["id"] == view["current_leg_id"]), None)
    risk, reason = _risk(view, baseline)
    return {
        **view, "demo": demo,
        "current_node_name": node["name"] if node else None,
        "current_leg_label": (
            f"{data.NODES[active_leg['from']]['name']} → {data.NODES[active_leg['to']]['name']}" if active_leg else None
        ),
        "route_name": " → ".join(detail["plan"]["node_names"]),
        "route_template_name": data.get_route(view["route_id"])["name"],
        "remaining_h": baseline["remaining_h"], "remaining_cost_usd": baseline["total_usd"],
        "remaining_co2_kg": baseline["co2_kg"], "risk": risk, "risk_reason": reason,
        "projection_source": baseline["source"],
    }


def _document_summary(detail: dict) -> dict:
    view, passport = detail["shipment"], detail["documents"]
    fields = ("id", "kind", "label", "filename", "size_bytes", "uploaded_at", "extraction_status")
    # The fleet list needs completion state, not extracted commercial values or original bytes.
    return {
        "shipment_id": view["id"], "shipment_name": view["name"], "status": passport["status"],
        "requirements": [
            {**{key: requirement[key] for key in ("key", "label", "status", "reason", "source", "file_id")},
             "mismatch_count": len(requirement["mismatches"])}
            for requirement in passport["requirements"]
        ],
        "files": [{key: file.get(key) for key in fields} for file in passport["files"]],
        "scope": passport["scope"],
    }


def _alert(shipment: dict, kind: str, severity: str, title: str, description: str,
           action: str, target: str, source: str) -> dict:
    return {
        "id": f"{shipment['id']}:{kind}", "shipment_id": shipment["id"], "shipment_name": shipment["name"],
        "kind": kind, "severity": severity, "title": title, "description": description,
        "action": action, "target": target, "source": source,
    }


def _alerts(shipment: dict, documents: dict) -> list[dict]:
    if shipment["progress_status"] == "delivered":
        return []
    alerts = []
    source = shipment["projection_source"]
    if shipment["blocked"]:
        alerts.append(_alert(shipment, "route_blocked", "critical", "Оставшийся маршрут закрыт",
                             "Условия сценария перекрывают узел на принятом пути.",
                             "Проверить доступные продолжения", "dispatch", source))
    elif shipment["risk_reason"] == "late":
        hours = f"{shipment['late_h']:g}".replace(".", ",")
        alerts.append(_alert(shipment, "late", "critical", "Прогноз опоздания",
                             f"Расчётное прибытие позже срока на {hours} ч.",
                             "Сравнить маршруты и проверить срок", "dispatch", source))
    elif shipment["risk"] == "elevated":
        reason = {
            "reported_delay": "В оставшемся плане есть событие с задержкой.",
            "sea_disruption": "На оставшемся морском плече действует расчётное ограничение по волне.",
            "deadline_slack": "До срока доставки остаётся менее 24 ч запаса по прогнозу.",
        }[shipment["risk_reason"]]
        alerts.append(_alert(shipment, "risk", "high", "Требуется проверить ETA", reason,
                             "Открыть прогноз перевозки", "dispatch", source))
    checks = {
        "mismatch": ("documents_mismatch", "high", "Есть расхождения в документах", "Сверить данные с карточкой груза"),
        "missing": ("documents_missing", "high", "Не хватает документов", "Загрузить недостающие документы"),
        "review_required": ("documents_review", "normal", "Документы ждут проверки", "Открыть паспорт документов"),
    }
    for status, (kind, severity, title, action) in checks.items():
        requirements = [r["label"] for r in documents["requirements"] if r["status"] == status]
        if requirements:
            alerts.append(_alert(shipment, kind, severity, title, ", ".join(requirements),
                                 action, "documents", "document_checklist"))
    return alerts


def _activity_item(shipment: dict, key: str, kind: str, title: str, at: str, source: str) -> dict:
    return {"id": key, "shipment_id": shipment["id"], "shipment_name": shipment["name"],
            "kind": kind, "title": title, "at": at, "source": source, "demo": shipment["demo"]}


def _activities(shipment: dict, detail: dict, documents: dict, created_at: str | None) -> list[dict]:
    activity = []
    if created_at:
        activity.append(_activity_item(shipment, f"{shipment['id']}:created", "shipment_created",
                                       "Создана перевозка", created_at, "operator"))
    for event in operations._effective_events(detail["events"]):
        kind = event["event_type"]
        if kind in {"departure", "arrival"} and operations._event_time(event) is None:
            title = "Записано плановое отправление" if kind == "departure" else "Записано плановое прибытие"
        else:
            title = model.EVENT_RU.get(kind, "Событие перевозки")
        activity.append(_activity_item(shipment, event["id"], kind, title, event["received_ts"], event["source"]))
    for file in documents["files"]:
        if file["id"] and file["uploaded_at"]:
            label = file["label"] or file["filename"] or "Документ"
            activity.append(_activity_item(shipment, file["id"], "document_uploaded",
                                           f"Загружен документ · {label}", file["uploaded_at"], "operator"))
    return activity


def _tasks(conn, names: dict) -> list[dict]:
    rows = conn.execute("SELECT * FROM workspace_tasks").fetchall()
    tasks = [_task(row, names[row["shipment_id"]]) for row in rows if row["shipment_id"] in names]
    tasks.sort(key=lambda task: task["updated_at"], reverse=True)
    tasks.sort(key=lambda task: (task["status"] == "done", _PRIORITY_ORDER[task["priority"]], task["due_at"] is None,
                                task["due_at"] or ""))
    return tasks


def _totals(shipments: list[dict]) -> dict:
    projected = [shipment for shipment in shipments if shipment["remaining_cost_usd"] is not None]
    return {"remaining_cost_usd": round(sum(row["remaining_cost_usd"] for row in projected), 2),
            "remaining_co2_kg": round(sum(row["remaining_co2_kg"] for row in projected), 1),
            "projected_shipments_count": len(projected), "unprojected_shipments_count": len(shipments) - len(projected)}


def _analytics(shipments: list[dict], active: list[dict], totals: dict, source: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for shipment in shipments:
        groups.setdefault(shipment["route_id"], []).append(shipment)
    return {
        **totals, "currency": "USD", "demo_shipments_count": sum(shipment["demo"] for shipment in shipments),
        "by_status": [{"key": key, "label": label,
                       "count": sum(s["progress_status"] == key for s in shipments)}
                      for key, label in _PROGRESS_LABELS.items()],
        "by_route": [{"route_id": key, "label": rows[0]["route_template_name"], "count": len(rows),
                      **_totals([row for row in rows if row["progress_status"] != "delivered"])}
                     for key, rows in sorted(groups.items())],
        "by_risk": [{"key": key, "count": sum(s["risk"] == key for s in active)}
                    for key in ("normal", "elevated", "critical")],
        "source": source,
        "notice": (
            "Стоимость и CO₂e рассчитаны для оставшегося пути по модели. Итоги включают демонстрационные грузы. "
            "Доставленные и закрытые условиями сценария перевозки исключены из финансовых и углеродных сумм. "
            "Группы маршрутов показывают исходные шаблоны, а расчёты учитывают действующие версии."
        ),
    }


def overview(scenario: dict) -> dict:
    forecast = weather.cached_hourly_forecast()
    with store._conn() as conn:
        conn.execute("BEGIN")
        now = operations._now()
        ports = operations._port_state(conn)
        records = [(shipment, True, None) for shipment in data.SHIPMENTS]
        records.extend(
            (store._shipment_dict(row), False, row["created_at"])
            for row in conn.execute("SELECT * FROM shipments ORDER BY id")
        )
        shipments, documents, alerts, activity = [], [], [], []
        for shipment, demo, created_at in records:
            detail = operations._detail(conn, shipment, scenario, ports, forecast, now, include_proposals=False)
            view = _shipment_row(detail, demo)
            passport = _document_summary(detail)
            shipments.append(view)
            documents.append(passport)
            alerts.extend(_alerts(view, passport))
            activity.extend(_activities(view, detail, passport, created_at))
        by_shipment = {row["id"]: row for row in shipments}
        tasks = _tasks(conn, {key: row["name"] for key, row in by_shipment.items()})
    for task in tasks:
        at = task["updated_at"]
        if task["version"] == 1:
            kind, title = "task_created", "Создана задача"
        elif task["status"] == "done":
            kind, title = "task_completed", "Задача выполнена"
        else:
            kind, title = "task_updated", "Обновлена задача"
        activity.append(_activity_item(by_shipment[task["shipment_id"]], task["id"], kind, title, at, "operator"))
    activity.sort(key=lambda event: (operations._timestamp(event["at"]), event["id"]), reverse=True)
    alerts.sort(key=lambda alert: (_PRIORITY_ORDER[alert["severity"]], alert["shipment_id"], alert["kind"]))
    active = [shipment for shipment in shipments if shipment["progress_status"] != "delivered"]
    pending = [task for task in tasks if task["status"] != "done"]
    totals = _totals(active)
    summary = {
        "shipment_count": len(shipments), "active_count": len(active), "delivered_count": len(shipments) - len(active),
        "late_count": sum(s["risk_reason"] == "late" for s in active),
        "at_risk_count": sum(s["risk"] in {"critical", "elevated"} for s in active),
        "blocked_count": sum(s["blocked"] for s in active),
        "documents_blocked_count": sum(s["document_status"] != "ready_for_review" for s in active),
        "financial_risk_usd": round(sum(s["financial_risk_usd"] or 0 for s in active), 2),
        "remaining_cost_usd": totals["remaining_cost_usd"], "remaining_co2_kg": totals["remaining_co2_kg"],
        "active_tasks_count": len(pending),
        "overdue_tasks_count": sum(t["due_at"] is not None and operations._timestamp(t["due_at"]) < now for t in pending),
        "alerts_count": len(alerts),
    }
    return {
        "generated_at": now.isoformat(), "scenario": scenario, "summary": summary,
        "shipments": shipments, "alerts": alerts, "activity": activity[:_ACTIVITY_LIMIT], "tasks": tasks,
        "documents": documents,
        "analytics": _analytics(shipments, active, totals, "scenario" if operations._scenario_active(scenario) else "model"),
        "notice": "Задачи оператора и расчётные сигналы обновляются независимо. Задача не меняет состояние перевозки.",
    }


def create_router(guard_write) -> APIRouter:
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    @router.get("")
    def workspace(response: Response, scenario: Annotated[dict, Depends(operations._scenario)]):
        response.headers["Cache-Control"] = "no-store"
        return overview(scenario)

    @router.post("/tasks", status_code=201, dependencies=[Depends(guard_write)])
    def create_task(body: TaskCreate, idempotency_key: IdempotencyKey):
        return _create_task(body, idempotency_key)

    @router.patch("/tasks/{task_id}", dependencies=[Depends(guard_write)])
    def update_task(task_id: Annotated[str, StringConstraints(min_length=1, max_length=40)], body: TaskPatch):
        return _update_task(task_id, body)

    return router
