"""
Тянет «бартерные» сделки из Bitrix24 и обогащает registry.json
именами подрядчиков (контакты/компании из CRM).

Запуск:
  python3 scripts/bitrix_deals.py

Источник: webhook URL из env (BITRIX24_WEBHOOK_URL) либо .env

Что делает:
  1. GET crm.deal.list с filter[%TITLE]=бартер
  2. Для каждой сделки берёт UF_CRM_44169 (ID помещения pb) и CONTACT_ID / COMPANY_ID
  3. Подгружает имена контактов / компаний (с кешем)
  4. Пишет результат в data/bitrix_deals.json
  5. Обогащает data/registry.json: добавляет в каждую запись поле `barter_contractor`
     с {name, source: 'bitrix24', deal_id, deal_title, stage}
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# === Пути ===
REPO_ROOT = Path(__file__).resolve().parents[1]
if (REPO_ROOT / "data").exists() or (REPO_ROOT / ".github").exists():
    DATA_DIR = REPO_ROOT / "data"
    ROOT_FOR_ENV = REPO_ROOT
else:
    DATA_DIR = Path(__file__).resolve().parents[1] / "выгрузка"
    ROOT_FOR_ENV = Path(__file__).resolve().parents[3]
DATA_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY = DATA_DIR / "registry.json"
DEALS_OUT = DATA_DIR / "bitrix_deals.json"


def load_env() -> dict[str, str]:
    keys = ["BITRIX24_WEBHOOK_URL"]
    env = {k: os.environ[k] for k in keys if k in os.environ and os.environ[k]}
    env_path = ROOT_FOR_ENV / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if "BITRIX24_WEBHOOK_URL" not in env:
        raise SystemExit("BITRIX24_WEBHOOK_URL не задан")
    return env


def bx_get(webhook: str, method: str, params: dict) -> dict:
    """Бэйскал к Bitrix24 webhook."""
    parts = []
    for k, v in params.items():
        if isinstance(v, list):
            for vv in v:
                parts.append(f"{urllib.parse.quote(k)}={urllib.parse.quote(str(vv))}")
        elif isinstance(v, dict):
            for kk, vv in v.items():
                parts.append(f"{urllib.parse.quote(k + '[' + kk + ']')}={urllib.parse.quote(str(vv))}")
        else:
            parts.append(f"{urllib.parse.quote(k)}={urllib.parse.quote(str(v))}")
    qs = "&".join(parts)
    url = f"{webhook.rstrip('/')}/{method}?{qs}"
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def bx_list(webhook: str, method: str, params: dict) -> list:
    """Постраничный list (для crm.deal.list, crm.contact.get и т.д.)."""
    items = []
    start = 0
    while True:
        p = dict(params)
        p["start"] = start
        d = bx_get(webhook, method, p)
        items.extend(d.get("result", []))
        if "next" not in d:
            break
        start = d["next"]
    return items


def fetch_contact(webhook: str, contact_id: int, cache: dict) -> dict:
    if not contact_id or str(contact_id) == "0":
        return {}
    if contact_id in cache:
        return cache[contact_id]
    try:
        d = bx_get(webhook, "crm.contact.get", {"id": contact_id})
        r = d.get("result", {})
        cache[contact_id] = {
            "id": contact_id,
            "name": " ".join(filter(None, [r.get("LAST_NAME"), r.get("NAME"), r.get("SECOND_NAME")])).strip(),
            "phone": (r.get("PHONE") or [{}])[0].get("VALUE", "") if r.get("PHONE") else "",
            "email": (r.get("EMAIL") or [{}])[0].get("VALUE", "") if r.get("EMAIL") else "",
            "company_id": r.get("COMPANY_ID"),
        }
    except Exception as e:
        cache[contact_id] = {"id": contact_id, "error": str(e)}
    return cache[contact_id]


def fetch_company(webhook: str, company_id: int, cache: dict) -> dict:
    if not company_id or str(company_id) == "0":
        return {}
    if company_id in cache:
        return cache[company_id]
    try:
        d = bx_get(webhook, "crm.company.get", {"id": company_id})
        r = d.get("result", {})
        cache[company_id] = {
            "id": company_id,
            "title": r.get("TITLE") or "",
            "phone": (r.get("PHONE") or [{}])[0].get("VALUE", "") if r.get("PHONE") else "",
            "industry": r.get("INDUSTRY") or "",
        }
    except Exception as e:
        cache[company_id] = {"id": company_id, "error": str(e)}
    return cache[company_id]


def main():
    env = load_env()
    webhook = env["BITRIX24_WEBHOOK_URL"]

    print("Тяну бартерные сделки из Bitrix24…")
    deals = bx_list(webhook, "crm.deal.list", {
        "filter[%TITLE]": "бартер",
        "select[]": [
            "ID", "TITLE", "STAGE_ID", "CONTACT_ID", "COMPANY_ID", "ASSIGNED_BY_ID",
            "DATE_CREATE", "DATE_MODIFY", "OPPORTUNITY", "CATEGORY_ID", "CLOSED",
            "UF_CRM_44169",            # ID помещения (pb)
            "UF_CRM_1624510158002",    # Номер помещения (pb)
            "UF_CRM_1624510062728",    # № дома (pb)
            "UF_CRM_6555B8B59939C",    # ЖК (pb)
            "UF_CRM_84E09",            # Тип помещения (pb)
        ],
    })
    print(f"  Получено: {len(deals)} сделок")

    # Подгружаем контакты и компании
    contact_cache, company_cache = {}, {}
    for d in deals:
        if d.get("CONTACT_ID"):
            fetch_contact(webhook, d["CONTACT_ID"], contact_cache)
        if d.get("COMPANY_ID"):
            fetch_company(webhook, d["COMPANY_ID"], company_cache)
    print(f"  Контактов: {len(contact_cache)}, компаний: {len(company_cache)}")

    # Соберём финальную структуру
    enriched_deals = []
    pid_to_contractor = {}
    for d in deals:
        contact = contact_cache.get(d.get("CONTACT_ID")) or {}
        company = company_cache.get(d.get("COMPANY_ID")) or {}
        # Имя подрядчика: компания > контакт > по TITLE сделки
        name = company.get("title") or contact.get("name") or d.get("TITLE") or ""
        pid = d.get("UF_CRM_44169")
        entry = {
            "deal_id": d.get("ID"),
            "deal_title": d.get("TITLE"),
            "stage": d.get("STAGE_ID"),
            "category_id": d.get("CATEGORY_ID"),
            "closed": d.get("CLOSED"),
            "date_create": d.get("DATE_CREATE"),
            "date_modify": d.get("DATE_MODIFY"),
            "opportunity": d.get("OPPORTUNITY"),
            "contractor_name": name,
            "contact": contact,
            "company": company,
            "property_id_pb": pid,
            "house_id_pb": d.get("UF_CRM_1624510062728"),
            "property_number_pb": d.get("UF_CRM_1624510158002"),
            "project_pb": d.get("UF_CRM_6555B8B59939C"),
            "type_pb": d.get("UF_CRM_84E09"),
        }
        enriched_deals.append(entry)
        if pid and pid != "0":
            pid_to_contractor[str(pid)] = entry

    DEALS_OUT.write_text(json.dumps(enriched_deals, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {DEALS_OUT} ({len(enriched_deals)} сделок)")
    print(f"  С привязкой pid: {len(pid_to_contractor)}")

    # Подгружаем мета для НОВЫХ pid (которых нет в реестре) из ProfitBase /property
    def fetch_pb_meta(pid: int) -> dict:
        pb_api_key = os.environ.get("PROFITBASE_API_KEY")
        pb_base = os.environ.get("PROFITBASE_BASE_URL", "https://pb12230.profitbase.ru")
        if not pb_api_key:
            for line in (ROOT_FOR_ENV / ".env").read_text(encoding="utf-8").splitlines():
                if line.startswith("PROFITBASE_API_KEY="):
                    pb_api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("PROFITBASE_BASE_URL="):
                    pb_base = line.split("=", 1)[1].strip().strip('"').strip("'")
        # Auth
        req = urllib.request.Request(
            f"{pb_base.rstrip('/')}/api/v4/json/authentication",
            data=json.dumps({"type": "api-app", "credentials": {"pb_api_key": pb_api_key}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        token = json.load(urllib.request.urlopen(req))["access_token"]
        url = f"{pb_base.rstrip('/')}/api/v4/json/property?access_token={token}&id={pid}"
        try:
            with urllib.request.urlopen(url) as r:
                d = json.load(r)
            items = d.get("data", [])
            if items:
                it = items[0]
                return {
                    "project": it.get("projectName"),
                    "house": it.get("houseName"),
                    "section": it.get("sectionName"),
                    "number": str(it.get("number")),
                    "floor": it.get("floor"),
                    "rooms": it.get("rooms_amount"),
                    "area": (it.get("area") or {}).get("area_total"),
                    "price": (it.get("price") or {}).get("value"),
                    "status": it.get("status"),
                    "customStatusId": it.get("customStatusId"),
                }
        except Exception:
            pass
        return {}

    # Обогащаем registry.json + добавляем новые объекты из Bitrix24
    if REGISTRY.exists():
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    else:
        reg = {"last_processed_ts": 0, "items": {}, "last_run": None}

    matched = 0
    added = 0
    for pid, deal in pid_to_contractor.items():
        if pid not in reg["items"]:
            # Объекта нет в реестре — добавляем (это закрытая бартерная сделка)
            meta = fetch_pb_meta(int(pid)) if pid.isdigit() else {}
            if not meta:
                continue
            reg["items"][pid] = {
                "meta": meta,
                "current_status": None,
                "current_status_label": "(SOLD — закрытый бартер)" if meta.get("status") == "SOLD" else f"(статус: {meta.get('status')})",
                "last_deal_name": None,
                "last_responsible": None,
                "last_date": 0,
                "history": [],
                "added_via_bitrix": True,
            }
            added += 1
        rec = reg["items"][pid]
        rec["barter_contractor"] = {
            "name": deal["contractor_name"],
            "deal_id": deal["deal_id"],
            "deal_title": deal["deal_title"],
            "stage": deal["stage"],
            "company_title": deal["company"].get("title"),
            "contact_name": deal["contact"].get("name"),
            "contact_phone": deal["contact"].get("phone"),
            "source": "bitrix24",
            "date_create": deal["date_create"],
        }
        matched += 1
    reg["bitrix_last_sync"] = enriched_deals[0]["date_modify"] if enriched_deals else None
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Привязано к подрядчику: {matched} (новых объектов добавлено: {added})")


if __name__ == "__main__":
    main()
