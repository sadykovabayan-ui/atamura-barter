"""
Тянет реестр бартера из Google Sheet (рабочая таблица Атамуры, ведут Аида/Аскар),
сопоставляет с registry.json по адресу квартиры и обогащает каждую запись:
  - имя контрагента и юр.лицо
  - ответственный менеджер
  - статус бартера (в работе / отработан / исполнен)
  - условия (50/50, 100% и т.д.)
  - финансы: сумма договора, оплачено, выполнено, остаток

Источник: публичный экспорт CSV Google Sheet.
Запуск: python3 scripts/gsheet_registry.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import urllib.request
from pathlib import Path
from collections import defaultdict

# === Конфигурация ===
SHEET_ID = "1SGbRE02ASsxURqWEiFxRbLisHjk-EIUFLnoLQnckvuQ"
SHEET_GID = "0"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"

REPO_ROOT = Path(__file__).resolve().parents[1]
if (REPO_ROOT / "data").exists() or (REPO_ROOT / ".github").exists():
    DATA_DIR = REPO_ROOT / "data"
else:
    DATA_DIR = Path(__file__).resolve().parents[1] / "выгрузка"
DATA_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY = DATA_DIR / "registry.json"
GSHEET_OUT = DATA_DIR / "gsheet_registry.json"

# === Нормализация ===
PROJECT_ALIASES = {
    "атмосфера": "Атмосфера",
    "атмо": "Атмосфера",
    "atmo": "Атмосфера",
    "atmosphera": "Атмосфера",
    "керуен": "KERUEN",
    "keruen": "KERUEN",
    "аура": "AURA",
    "aura": "AURA",
    "арлан": "Арлан",
    "arlan": "Арлан",
    "аксай": "AQSAI RESORT",
    "aqsai": "AQSAI RESORT",
    "аксай резорт": "AQSAI RESORT",
    "aqsai resort": "AQSAI RESORT",
    "bravo": "Bravo",
    "браво": "Bravo",
}

ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI", "7": "VII",
         "I": "I", "II": "II", "III": "III", "IV": "IV", "V": "V", "VI": "VI", "VII": "VII"}


def norm_project(s: str) -> str:
    s = (s or "").strip().lower()
    return PROJECT_ALIASES.get(s, s.title() if s else "")


def norm_queue(s: str) -> str | None:
    """Преобразует '1', '5', 'I', 'V' в 'I очередь', 'V очередь' и т.д."""
    if not s:
        return None
    s = str(s).strip()
    # Извлекаем числовую/римскую часть
    m = re.match(r"([IVX]+|\d+)", s)
    if not m:
        return None
    raw = m.group(1)
    rom = ROMAN.get(raw)
    return f"{rom} очередь" if rom else None


def parse_int_or_none(s):
    s = (str(s) or "").strip()
    if not s:
        return None
    m = re.match(r"-?\d+", s)
    return int(m.group(0)) if m else None


def parse_money(s) -> int | None:
    """'12 345' / '12,345' / '12345' → 12345"""
    if not s:
        return None
    s = str(s).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not s or s == "-":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def fetch_csv() -> list[dict]:
    """Скачивает CSV, возвращает list of dict по заголовкам."""
    with urllib.request.urlopen(CSV_URL) as r:
        data = r.read().decode("utf-8")
    rows = list(csv.reader(io.StringIO(data)))
    if not rows:
        return []
    headers = [h.strip() for h in rows[0]]
    result = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        d = {headers[i]: (row[i].strip() if i < len(row) else "") for i in range(len(headers))}
        result.append(d)
    return result


def normalize_record(r: dict) -> dict:
    """Преобразует строку из CSV в нормализованный формат."""
    project = norm_project(r.get("ЖК", ""))
    queue = norm_queue(r.get("Очередь", ""))
    block = (r.get("блок") or "").strip()
    floor = parse_int_or_none(r.get("этаж"))
    apartment = (r.get("квартира") or "").strip()
    apt_type = None
    # «Парковка», «Кладовка» — особый тип
    floor_txt = (r.get("этаж") or "").lower()
    if "парк" in floor_txt or "парк" in (r.get("блок") or "").lower():
        apt_type = "parking"
    elif "клад" in floor_txt or "клад" in (r.get("блок") or "").lower():
        apt_type = "storage"

    return {
        "date": r.get("Дата", ""),
        "row_num": r.get("№", ""),
        "contractor": r.get("Контрагент", ""),
        "legal_entity": r.get("Юр лицо", ""),
        "kind": r.get("Вид", ""),
        "responsible": r.get("Ответственный", ""),
        "status_property": r.get("Статус помещения", ""),
        "status_barter": r.get("Статус бартера", ""),
        "project": project,
        "queue": queue,
        "block": block,
        "floor": floor,
        "apartment": apartment,
        "apt_type": apt_type,
        "area": parse_money(r.get("кв.м.", "").replace(",", ".")) if r.get("кв.м.") else None,
        "price_per_meter": parse_money(r.get("цена")),
        "amount_property": parse_money(r.get("Стоимость помещения")),
        "terms": r.get("Условия", ""),
        "amount_contract": parse_money(r.get("Сумма договора")),
        "amount_paid_money": parse_money(r.get("Оплачено по договору (деньгами)")),
        "amount_done": parse_money(r.get("Выполнено по договору")),
        "remaining_contract": parse_money(r.get("Остаток по договору")),
        "remaining_to_pay": parse_money(r.get("Остаток к оплате (деньгами)")),
        "company": r.get("КОМПАНИЯ", ""),
        "note": r.get("Примечание", ""),
    }


def match_to_registry(rec: dict, items: dict) -> str | None:
    """Ищет propertyId в registry.json по нормализованному адресу.

    Стратегия: для каждой записи в registry смотрим meta.project / meta.house / meta.section /
    meta.floor / meta.number и сравниваем с rec.
    """
    target_project = rec["project"]
    target_queue_label = rec["queue"]
    target_block = (rec["block"] or "").strip()
    target_floor = rec["floor"]
    target_apt = rec["apartment"].strip()

    if not target_project or not target_queue_label:
        return None

    for pid, it in items.items():
        m = it.get("meta") or {}
        if not m:
            continue
        # ЖК
        if (m.get("project") or "").strip().lower() != target_project.lower():
            continue
        # Очередь (в registry — full house name: "I очередь" или "I очередь - кладовые и паркинг")
        house = (m.get("house") or "").strip()
        if not house.startswith(target_queue_label):
            continue
        # Block / section
        sec = str(m.get("section") or "").strip()
        if target_block and sec != target_block:
            # для парковок/кладовых блок в Google Sheet может не совпадать с section в PB
            if rec["apt_type"] not in ("parking", "storage"):
                continue
        # Floor
        if target_floor is not None and m.get("floor") is not None:
            if int(m.get("floor")) != int(target_floor) and rec["apt_type"] not in ("parking", "storage"):
                continue
        # Apartment number
        pb_num = str(m.get("number") or "").strip()
        if target_apt and pb_num and target_apt != pb_num:
            # для парковок/кладовых иногда «квартира» = код типа «ТП 50» — мягкое сравнение
            if target_apt not in pb_num and pb_num not in target_apt:
                continue
        return pid
    return None


def main():
    print("Тяну Google Sheet…")
    raw = fetch_csv()
    print(f"  Получено строк: {len(raw)}")

    records = []
    for r in raw:
        rec = normalize_record(r)
        records.append(rec)
    print(f"  Нормализовано: {len(records)}")

    # Сохраним нормализованную копию для отладки
    GSHEET_OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {GSHEET_OUT}")

    # Открываем registry.json
    if not REGISTRY.exists():
        print("  registry.json не найден — пропуск обогащения")
        return
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    items = reg["items"]

    # Сопоставляем
    matched = 0
    unmatched = []
    by_contractor = defaultdict(list)
    for rec in records:
        pid = match_to_registry(rec, items)
        if pid:
            items[pid]["gsheet"] = rec
            by_contractor[rec["contractor"]].append(pid)
            matched += 1
        else:
            unmatched.append(rec)

    print(f"  Сопоставлено: {matched} из {len(records)}")
    print(f"  Не нашли пару в registry: {len(unmatched)}")

    # Топ подрядчиков
    print("\n  Топ-15 подрядчиков (по сопоставленным квартирам):")
    top = sorted(by_contractor.items(), key=lambda x: -len(x[1]))[:15]
    for name, pids in top:
        print(f"    {len(pids):3} × {name}")

    # Записываем sync_at
    import time
    reg["gsheet_last_sync"] = int(time.time())
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  registry.json обновлён")


if __name__ == "__main__":
    main()
