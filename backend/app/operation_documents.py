"""Private originals and local document consistency checks for the operations UI."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath

from fastapi import HTTPException, UploadFile
from pypdf import Configuration, PdfReader, apply_configuration

from app import data, store

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES_PER_SHIPMENT = 20
MAX_EXTRACTED_TEXT = 20_000
_PDF_LIMITS = Configuration(
    maximum_declared_stream_length=4_000_000,
    zlib_maximum_output_length=4_000_000,
    stream_decoding_work_maximum_length=8_000_000,
    stream_parsing_work_maximum_count=150_000,
    page_tree_maximum_page_entries=1_000,
    xform_maximum_invocations_per_extraction=500,
)
DOCUMENT_TYPES = {
    **data.DOC_TYPES,
    "bill_of_lading": "Морская накладная",
    "rail_waybill": "Железнодорожная накладная",
    "road_waybill": "Автомобильная накладная",
}
_MODE_DOCUMENTS = {
    "sea": "bill_of_lading",
    "rail": "rail_waybill",
    "road": "road_waybill",
}
_MODE_NAMES = {"sea": "морское", "rail": "железнодорожное", "road": "автомобильное"}
_FIELDS = {
    "shipment_id": r"shipment(?:\s*(?:id|number))?|отправка|номер\s+(?:груза|отправки)",
    "document_number": r"document\s*(?:no\.?|number)|номер\s+документа|инвойс\s+№",
    "weight_t": r"(?:(?:gross\s+)?weight|вес(?:\s+брутто)?|масса(?:\s+брутто)?)(?:\s*\((?:kg|t|кг|т)\))?",
    "destination": r"destination|порт\s+назначения|место\s+назначения|назначение",
    "cargo": r"cargo(?:\s+description)?|описание\s+груза|груз|товар",
    "origin": r"country\s+of\s+origin|страна\s+происхождения",
}


def _text_for_upload(content: bytes, suffix: str) -> tuple[str, str, str]:
    if suffix == ".txt":
        try:
            result = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "Текстовый файл должен быть в UTF-8") from exc
        if any(ord(ch) < 32 and ch not in "\r\n\t" for ch in result):
            raise HTTPException(422, "Текстовый файл содержит двоичные данные")
        return result[:MAX_EXTRACTED_TEXT], "text", "UTF-8"
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(422, "Содержимое файла не соответствует PDF")
        try:
            with apply_configuration(_PDF_LIMITS), PdfReader(io.BytesIO(content)) as reader:
                if reader.is_encrypted:
                    return "", "unavailable", "PDF защищён паролем"
                chunks = [page.extract_text() or "" for page in reader.pages[:4]]
        except Exception:
            return "", "unavailable", "Не удалось прочитать текст PDF"
        extracted = "\n".join(chunks).strip()[:MAX_EXTRACTED_TEXT]
        return extracted, "text" if extracted else "unavailable", "pypdf, первые 4 страницы"

    signature = b"\x89PNG\r\n\x1a\n" if suffix == ".png" else b"\xff\xd8\xff"
    if not content.startswith(signature):
        raise HTTPException(422, "Содержимое файла не соответствует формату изображения")
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        return "", "unavailable", "Локальный Tesseract не установлен"
    try:
        languages = subprocess.run(
            [tesseract, "--list-langs"], capture_output=True, check=True, timeout=2
        ).stdout.splitlines()
        selected = "+".join(lang for lang in ("rus", "eng") if lang.encode() in languages)
        if not selected:
            return "", "unavailable", "Локальный Tesseract без языков rus/eng"
        output = subprocess.run(
            [tesseract, "stdin", "stdout", "-l", selected, "--psm", "6"],
            input=content, capture_output=True, check=True, timeout=5,
        ).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return "", "unavailable", "Локальное распознавание изображения недоступно"
    return output[:MAX_EXTRACTED_TEXT], "ocr", f"Tesseract ({selected})"


def _fields(text: str) -> dict:
    fields = {}
    for key, label in _FIELDS.items():
        match = re.search(rf"^\s*(?:{label})\s*[:=]\s*([^\r\n]+)", text, re.M | re.I)
        if not match:
            continue
        value = match[1].strip()[:160]
        if key == "weight_t":
            number = re.fullmatch(
                r"([\d., ]+)\s*(кг|kg|т|t|tons?|тонн[аы]?)?", value, re.I
            )
            if number:
                try:
                    weight = float(number[1].replace(" ", "").replace(",", "."))
                except ValueError:
                    continue
                unit = (number[2] or "").casefold()
                if not unit:
                    unit_match = re.search(r"\((kg|t|кг|т)\)", match[0], re.I)
                    if unit_match is None:
                        continue
                    unit = unit_match[1].casefold()
                if math.isfinite(weight) and weight > 0:
                    fields[key] = weight / 1000 if unit in ("кг", "kg") else weight
            continue
        if value:
            fields[key] = value
    return fields


def prepare_upload(kind: str, file: UploadFile) -> tuple[dict, bytes]:
    if kind not in DOCUMENT_TYPES:
        raise HTTPException(422, "Неизвестный вид документа")
    raw_name = PurePosixPath((file.filename or "").replace("\\", "/")).name
    name = "".join(ch for ch in raw_name if ch.isprintable())[:180]
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in {".txt", ".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(415, "Поддерживаются PDF, TXT, PNG и JPEG")
    content = file.file.read(MAX_FILE_BYTES + 1)
    if not content:
        raise HTTPException(422, "Файл пуст")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "Файл больше 2 МиБ")
    text, extraction_status, extraction_source = _text_for_upload(content, suffix)
    return {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "label": DOCUMENT_TYPES[kind],
        "filename": name,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "extraction_status": extraction_status,
        "extraction_source": extraction_source,
        "extraction_note": extraction_source,
        "fields": _fields(text),
    }, content


def save_upload(conn, shipment_id: str, file: dict, content: bytes) -> dict:
    count = conn.execute(
        "SELECT COUNT(*) FROM operation_files WHERE shipment_id=?", (shipment_id,)
    ).fetchone()[0]
    existing = conn.execute(
        "SELECT metadata_json FROM operation_files WHERE shipment_id=? AND kind=? "
        "ORDER BY seq DESC LIMIT 1",
        (shipment_id, file["kind"]),
    ).fetchone()
    if existing and (metadata := json.loads(existing["metadata_json"]))["sha256"] == file["sha256"]:
        return metadata
    if count >= MAX_FILES_PER_SHIPMENT:
        raise HTTPException(409, "Достигнут лимит 20 файлов на груз")
    conn.execute(
        "INSERT INTO operation_files (id, shipment_id, kind, metadata_json, content) "
        "VALUES (?, ?, ?, ?, ?)",
        (file["id"], shipment_id, file["kind"], json.dumps(file, ensure_ascii=False), content),
    )
    return file


def _normal(value: str) -> str:
    return "".join(ch for ch in value.casefold().replace("ё", "е") if ch.isalnum())


def _check_fields(fields: dict, shipment: dict, destination: str) -> list[dict]:
    node = data.NODES[destination]
    expected = {
        "shipment_id": shipment["id"],
        "weight_t": shipment["weight_t"],
        "destination": node["name"],
        "cargo": shipment["cargo"],
    }
    accepted_destinations = {_normal(node["name"]), _normal(destination)}
    if destination == "bandar_abbas":
        accepted_destinations.update({"bandarabas", "bandarabbas", "бандараббас"})
    comparisons = {
        "shipment_id": lambda value: _normal(value) == _normal(shipment["id"]),
        "weight_t": lambda value: abs(value - shipment["weight_t"]) <= max(0.01, shipment["weight_t"] * 0.01),
        "destination": lambda value: _normal(value) in accepted_destinations,
        "cargo": lambda value: _normal(value) in {_normal(shipment["cargo"]), _normal(shipment["name"])},
    }
    labels = {"shipment_id": "Номер груза", "weight_t": "Масса, т", "destination": "Назначение", "cargo": "Описание груза"}
    return [
        {"field": key, "actual": fields[key], "expected": expected[key],
         "message": f"{labels[key]}: в файле «{fields[key]}», в карточке «{expected[key]}»"}
        for key, matches in comparisons.items()
        if key in fields and not matches(fields[key])
    ]


def passport(conn, shipment: dict, remaining: list[dict], destination: str) -> dict:
    rows = conn.execute(
        "SELECT id,kind,metadata_json FROM operation_files WHERE shipment_id=? ORDER BY seq DESC",
        (shipment["id"],),
    )
    files = [
        {**json.loads(row["metadata_json"]), "id": row["id"], "kind": row["kind"]}
        for row in rows
    ]
    for file in files:
        file["fields"] = file.get("fields") or {}
        file["mismatches"] = _check_fields(file["fields"], shipment, destination)
    latest: dict = {}
    for file in files:
        latest.setdefault(file["kind"], file)
    legacy = {doc["doc_type"]: doc for doc in store.get_documents(shipment["id"], conn)}
    seed = shipment.get("docs", {}) if data.get_shipment(shipment["id"]) else {}
    requirements = {}
    if remaining:
        requirements = {
            "invoice": "Сведения о партии груза",
            "packing_list": "Состав и масса партии",
            "certificate_of_origin": "Происхождение партии",
            "insurance": "Проверка страхового покрытия",
        }
        for mode in dict.fromkeys(leg["mode"] for leg in remaining):
            requirements[_MODE_DOCUMENTS[mode]] = f"В плане остаётся {_MODE_NAMES[mode]} плечо"
        crop = any(word in f"{shipment['name']} {shipment['cargo']}".casefold() for word in (
            "зерно", "пшениц", "кукуруз", "растени", "barley", "wheat", "grain",
        ))
        if crop:
            requirements["phytosanitary"] = "Растительная продукция в грузе"
    checks = []
    for key, reason in requirements.items():
        file = latest.get(key)
        doc = legacy.get(key, {})
        status, fields, mismatches = "missing", {}, []
        source = "checklist"
        if file:
            fields, mismatches = file["fields"], file["mismatches"]
            source = file.get("extraction_source") or "uploaded"
            comparable = {"shipment_id", "weight_t", "destination", "cargo"} & fields.keys()
            status = "mismatch" if mismatches else "matched" if comparable else "review_required"
        elif doc.get("status", seed.get(key)) == "verified":
            status, source = "demo_verified", "demo"
        elif doc.get("status", seed.get(key)) in {"submitted", "pending", "rejected", "expired"}:
            status, source = "review_required", "demo"
        checks.append({
            "key": key, "label": DOCUMENT_TYPES[key], "reason": reason, "status": status,
            "source": source, "fields": fields, "mismatches": mismatches,
            "file_id": file["id"] if file else None,
        })
    statuses = {check["status"] for check in checks}
    priority = ("mismatch", "missing", "review_required")
    return {
        "status": next((status for status in priority if status in statuses), "ready_for_review"),
        "requirements": checks, "files": files,
        "scope": "Внутренний чек-лист маршрута. Требования и допуски подтверждает перевозчик или брокер.",
    }
