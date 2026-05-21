"""
Реестр бартерщиков Атамуры — автономный, через ProfitBase REST API v4.

Что делает:
  1. Получает access_token через api-app credentials (из .env).
  2. Скачивает страницами историю /api/v4/json/history (POST, по 100 событий).
  3. Останавливается на событиях старше `last_processed_ts` из registry.json.
  4. Фильтрует actionKey == "CHANGE_STATUS".
  5. Для каждого propertyId агрегирует ПОСЛЕДНЕЕ событие смены статуса.
  6. Помечает «текущий бартер» = propertyStatusKey == BARTER_KEY.
  7. Обновляет registry.json (атомарная запись через .tmp + rename).
  8. Отправляет Telegram-уведомления о новых/закрытых бартерах.

Запускать локально: python3 scripts/pb_barter_registry.py
Запускать на сервере: cron job каждые N минут.

Окружение (.env в корне проекта):
  PROFITBASE_API_KEY      — api-app ключ от ProfitBase
  PROFITBASE_BASE_URL     — https://pb12230.profitbase.ru
  TELEGRAM_BOT_TOKEN      — токен Telegram бота
  TELEGRAM_CHAT_ID        — чат куда слать уведомления
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# === Конфигурация ===

# В CI/CD-режиме (GitHub Actions): скрипт лежит в repo/scripts/, данные в repo/data/
# В локальном режиме (Claude/бартер/scripts/): берёт пути из проекта Atamura
REPO_ROOT = Path(__file__).resolve().parents[1]  # на 1 вверх от scripts/
if (REPO_ROOT / "data").exists() or (REPO_ROOT / ".github").exists():
    # Режим GitHub Actions / standalone repo
    ROOT = REPO_ROOT
    OUT_DIR = REPO_ROOT / "data"
else:
    # Локальный режим — старая структура
    ROOT = Path(__file__).resolve().parents[3]
    OUT_DIR = Path(__file__).resolve().parents[1] / "выгрузка"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY = OUT_DIR / "registry.json"

# Ключ статуса «Бартер / Продано ЗБ» в propertyStatusKey
# Соответствует customStatusId=132940 («Продано ЗБ» / в истории «Продано 3Б»)
# Подтверждено через API history 21.05.2026
BARTER_KEY = "BOOKED_bitrix_643fe8daed856"

# Локальная таймзона для отображения (Алматы UTC+5)
LOCAL_TZ = timezone(timedelta(hours=5))


def load_env() -> dict[str, str]:
    """Берём конфиг из os.environ (GitHub Actions secrets), потом дополняем из .env если есть."""
    keys = ["PROFITBASE_API_KEY", "PROFITBASE_BASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    env = {k: os.environ[k] for k in keys if k in os.environ and os.environ[k]}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            env.setdefault(k, v)
    if not env.get("PROFITBASE_BASE_URL"):
        env["PROFITBASE_BASE_URL"] = "https://pb12230.profitbase.ru"
    if "PROFITBASE_API_KEY" not in env:
        raise SystemExit("PROFITBASE_API_KEY не задан (ни в env, ни в .env)")
    return env


def authenticate(env: dict[str, str]) -> str:
    base = env["PROFITBASE_BASE_URL"].rstrip("/")
    req = urllib.request.Request(
        f"{base}/api/v4/json/authentication",
        data=json.dumps({"type": "api-app", "credentials": {"pb_api_key": env["PROFITBASE_API_KEY"]}}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)["access_token"]


def fetch_history_page(base: str, token: str, offset: int) -> dict:
    url = f"{base}/api/v4/json/history?access_token={token}&offset={offset}"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def load_registry() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {
        "last_processed_ts": 0,
        "items": {},  # propertyId(str) → {meta, current_status, history[]}
        "last_run": None,
    }


def save_registry(reg: dict) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(REGISTRY)


def fetch_property_meta(base: str, token: str, property_ids: list[int]) -> dict[int, dict]:
    """Подтянуть метаданные (ЖК, очередь, площадь, цена) для конкретных объектов."""
    meta = {}
    # /property отдаёт всё; берём чанками
    offset = 0
    needed = set(property_ids)
    while needed:
        url = f"{base}/api/v4/json/property?access_token={token}&limit=500&offset={offset}"
        with urllib.request.urlopen(url) as r:
            d = json.load(r)
        chunk = d.get("data", [])
        if not chunk:
            break
        for it in chunk:
            pid = it.get("id")
            if pid in needed:
                meta[pid] = {
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
                needed.discard(pid)
        if len(chunk) < 500 or not needed:
            break
        offset += 500
    return meta


def ts_to_local(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


def telegram_send(env: dict[str, str], text: str) -> bool:
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = env.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("⚠ Telegram не настроен — пропуск уведомления")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data)) as r:
            r.read()
        return True
    except Exception as e:
        print(f"⚠ Telegram error: {e}")
        return False


def fetch_with_retry(fn, *args, retries: int = 5, delay: float = 2.0, **kw):
    """Безопасный вызов сетевой функции с экспоненциальной паузой."""
    for attempt in range(retries):
        try:
            return fn(*args, **kw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            wait = delay * (2 ** attempt)
            print(f"  ⚠ сеть: {e}; повтор через {wait:.0f}с (попытка {attempt + 2}/{retries})")
            time.sleep(wait)


def initialize_from_property(base: str, token: str, reg: dict) -> int:
    """Первичная заливка реестра из /property: текущее состояние всех бартеров."""
    print("Первичная инициализация из /property…")
    offset = 0
    added = 0
    while True:
        url = f"{base}/api/v4/json/property?access_token={token}&limit=500&offset={offset}"
        with urllib.request.urlopen(url) as r:
            d = json.load(r)
        chunk = d.get("data", [])
        if not chunk:
            break
        for it in chunk:
            if it.get("customStatusId") != 132940:
                continue
            pid_s = str(it["id"])
            if pid_s in reg["items"]:
                continue
            reg["items"][pid_s] = {
                "meta": {
                    "project": it.get("projectName"),
                    "house": it.get("houseName"),
                    "section": it.get("sectionName"),
                    "number": str(it.get("number")),
                    "floor": it.get("floor"),
                    "rooms": it.get("rooms_amount"),
                    "area": (it.get("area") or {}).get("area_total"),
                    "price": (it.get("price") or {}).get("value"),
                },
                "current_status": BARTER_KEY,
                "current_status_label": "Продано ЗБ (инициализация)",
                "last_deal_name": None,
                "last_responsible": None,
                "last_date": 0,
                "history": [],
                "initialized_from_property": True,
            }
            added += 1
        if len(chunk) < 500:
            break
        offset += 500
    print(f"  Добавлено объектов из /property: {added}")
    return added


def main(notify: bool = True, lookback_days: int = 7, max_pages: int = 200) -> None:
    env = load_env()
    base = env["PROFITBASE_BASE_URL"].rstrip("/")
    token = fetch_with_retry(authenticate, env)

    reg = load_registry()
    is_first_run = reg["last_processed_ts"] == 0

    # Первичная инициализация — берём текущие бартеры из /property
    if is_first_run:
        fetch_with_retry(initialize_from_property, base, token, reg)

    # При первом запуске ставим разумный lookback
    last_ts = reg["last_processed_ts"]
    if last_ts == 0:
        last_ts = int(time.time()) - lookback_days * 86400
        print(f"Lookback: {lookback_days} дн. (с {ts_to_local(last_ts)})")
    else:
        print(f"Последняя обработанная дата: {ts_to_local(last_ts)}")

    # Идём по истории страницами пока не достигнем last_ts
    new_events = []
    offset = 0
    pages = 0
    while pages < max_pages:
        try:
            d = fetch_with_retry(fetch_history_page, base, token, offset)
        except Exception as e:
            print(f"⚠ Прерывание из-за сетевой ошибки на offset={offset}: {e}")
            break
        page = d.get("response", [])
        if not page:
            break
        stopped = False
        for e in page:
            if e["date"] <= last_ts:
                stopped = True
                break
            if e["actionKey"] == "CHANGE_STATUS":
                new_events.append(e)
        if stopped:
            break
        offset += 100
        pages += 1
        if offset >= d.get("total", 0):
            break
    print(f"Прошли страниц истории: {pages}")
    # Сортируем по дате (раньше → позже), чтобы корректно обновлять состояние
    new_events.sort(key=lambda e: e["date"])
    print(f"Новых CHANGE_STATUS событий: {len(new_events)}")

    if not new_events:
        reg["last_run"] = int(time.time())
        save_registry(reg)
        print("Изменений нет.")
        return

    # Подтянем метаданные для всех propertyId
    pids = sorted({e["propertyId"] for e in new_events})
    if pids:
        # Только если объект ещё неизвестен в реестре — иначе берём из реестра
        unknown = [p for p in pids if str(p) not in reg["items"]]
        meta = fetch_property_meta(base, token, unknown) if unknown else {}
        for p, m in meta.items():
            reg["items"].setdefault(str(p), {"meta": m, "current_status": None, "history": []})

    notifications = []  # (тип, текст)
    for e in new_events:
        pid_s = str(e["propertyId"])
        is_barter_event = e.get("propertyStatusKey") == BARTER_KEY
        is_known = pid_s in reg["items"]
        # Если объект ещё неизвестен — добавляем только когда событие связано с бартером
        # (либо переход в Бартер, либо переход ИЗ Бартера — то и другое требует знать что объект был в Бартере)
        if not is_known and not is_barter_event:
            # Этот объект никогда не был в бартере по нашему реестру; пропускаем (это просто
            # обычная сделка в системе, не относится к нашей задаче)
            continue
        rec = reg["items"].setdefault(pid_s, {"meta": {}, "current_status": None, "history": []})
        prev_status = rec.get("current_status")
        new_key = e.get("propertyStatusKey")
        new_label = e.get("propertyStatus")
        rec["history"].append({
            "date": e["date"],
            "date_local": ts_to_local(e["date"]),
            "from_status_key": prev_status,
            "to_status_key": new_key,
            "to_status_label": new_label,
            "dealId": e.get("dealId"),
            "dealName": e.get("dealName"),
            "responsibleName": e.get("responsibleName"),
            "contactName": e.get("contactName"),
            "contactPhone": e.get("contactPhone"),
            "contactId": e.get("contactId"),
        })
        rec["current_status"] = new_key
        rec["current_status_label"] = new_label
        rec["last_deal_name"] = e.get("dealName")
        rec["last_responsible"] = e.get("responsibleName")
        rec["last_date"] = e["date"]
        rec["last_date_local"] = ts_to_local(e["date"])

        # Notifications: переход в Бартер / выход из Бартера
        if new_key == BARTER_KEY and prev_status != BARTER_KEY:
            m = rec.get("meta") or {}
            notifications.append(("new", (
                f"🆕 <b>Новый бартер</b>\n"
                f"{m.get('project','?')} / {m.get('house','?')} / №{m.get('number','?')} "
                f"(эт.{m.get('floor','?')}, {m.get('area','?')} м², "
                f"{(m.get('price') or 0):,} ₸)\n".replace(",", " ") +
                f"Покупатель: <b>{e.get('dealName') or '—'}</b>\n"
                f"Менеджер: {e.get('responsibleName') or '—'}\n"
                f"Контакт: {e.get('contactName') or '—'} {e.get('contactPhone') or ''}\n"
                f"Дата: {ts_to_local(e['date'])}\n"
                f"propertyId={pid_s}"
            )))
        elif prev_status == BARTER_KEY and new_key != BARTER_KEY:
            m = rec.get("meta") or {}
            notifications.append(("closed", (
                f"✅ <b>Бартер закрыт</b> → {new_label}\n"
                f"{m.get('project','?')} / {m.get('house','?')} / №{m.get('number','?')}\n"
                f"Был у: {rec.get('last_deal_name','?')}\n"
                f"Сейчас: {e.get('dealName') or '—'}\n"
                f"Дата: {ts_to_local(e['date'])}"
            )))

    reg["last_processed_ts"] = max(e["date"] for e in new_events)
    reg["last_run"] = int(time.time())
    save_registry(reg)

    # Сводка
    active = [r for r in reg["items"].values() if r.get("current_status") == BARTER_KEY]
    print(f"Текущих активных бартеров в реестре: {len(active)}")
    print(f"Изменений: {len(new_events)}, уведомлений: {len(notifications)}")

    if notify and notifications:
        # Шлём пачкой (до 10 за раз)
        for typ, text in notifications[:10]:
            telegram_send(env, text)
        if len(notifications) > 10:
            telegram_send(env, f"… и ещё {len(notifications) - 10} изменений.")


if __name__ == "__main__":
    notify = "--no-telegram" not in sys.argv
    main(notify=notify)
