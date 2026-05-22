"""
Рендерит registry.json в HTML-дашборд (Claude/бартер/registry.html).
Запускать после pb_barter_registry.py.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# CI/CD: scripts/ + data/ + index.html в корне репо
# Локально: Claude/бартер/scripts/ + выгрузка/ + registry.html
if (REPO_ROOT / "data").exists() or (REPO_ROOT / ".github").exists():
    REGISTRY = REPO_ROOT / "data" / "registry.json"
    OUT = REPO_ROOT / "index.html"
else:
    REGISTRY = REPO_ROOT / "выгрузка" / "registry.json"
    OUT = REPO_ROOT / "registry.html"

BARTER_KEY = "BOOKED_bitrix_643fe8daed856"
LOCAL_TZ = timezone(timedelta(hours=5))


def fmt_money(n):
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", " ")


def ts_to_local(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


def main():
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    items = reg["items"]
    last_run = ts_to_local(reg.get("last_run"))
    last_ts = ts_to_local(reg.get("last_processed_ts"))

    rows = []
    for pid, it in items.items():
        bc = it.get("barter_contractor") or {}
        gs = it.get("gsheet") or {}
        # Главный «подрядчик» — берём из Google Sheet (свежее, полнее), иначе из Bitrix24
        primary_contractor = gs.get("contractor") or bc.get("name")
        rows.append({
            "pid": pid,
            "meta": it.get("meta", {}),
            "status": it.get("current_status"),
            "label": it.get("current_status_label"),
            "deal": it.get("last_deal_name"),
            "mgr": it.get("last_responsible"),
            "date": it.get("last_date"),
            "history_count": len(it.get("history") or []),
            "initialized": it.get("initialized_from_property", False),
            "contractor": primary_contractor,
            "contractor_legal_entity": gs.get("legal_entity"),
            "contractor_responsible": gs.get("responsible"),
            "contractor_status_barter": gs.get("status_barter"),
            "contractor_terms": gs.get("terms"),
            "contractor_amount_contract": gs.get("amount_contract"),
            "contractor_amount_paid": gs.get("amount_paid_money"),
            "contractor_amount_done": gs.get("amount_done"),
            "contractor_remaining_to_pay": gs.get("remaining_to_pay"),
            "contractor_note": gs.get("note"),
            "contractor_source": "gsheet" if gs.get("contractor") else ("bitrix24" if bc.get("name") else None),
            "contractor_bitrix_deal_id": bc.get("deal_id"),
        })

    # Сортировка: сначала текущие бартеры со свежей историей, потом без, потом не-бартер
    def sort_key(r):
        is_b = 0 if r["status"] == BARTER_KEY else 1
        has_h = 0 if r["history_count"] > 0 else 1
        return (is_b, has_h, -(r["date"] or 0))
    rows.sort(key=sort_key)

    # Сводки
    active = [r for r in rows if r["status"] == BARTER_KEY]
    active_with_hist = [r for r in active if r["history_count"] > 0]
    closed_recent = [r for r in rows if r["status"] != BARTER_KEY and r["history_count"] > 0]
    with_contractor = [r for r in rows if r.get("contractor")]

    total_value = sum((r["meta"].get("price") or 0) for r in active)
    # Топ подрядчиков
    from collections import Counter
    contractor_counts = Counter(r["contractor"] for r in rows if r.get("contractor"))

    # Финансовые сводки из Google Sheet
    total_contract = sum(r["contractor_amount_contract"] or 0 for r in rows)
    total_paid = sum(r["contractor_amount_paid"] or 0 for r in rows)
    total_done = sum(r["contractor_amount_done"] or 0 for r in rows)
    total_remaining = sum(r["contractor_remaining_to_pay"] or 0 for r in rows)

    # JSON для встроенной таблицы
    table_data = []
    for r in rows:
        m = r["meta"]
        table_data.append({
            "pid": r["pid"],
            "project": m.get("project"),
            "house": m.get("house"),
            "section": m.get("section"),
            "number": m.get("number"),
            "floor": m.get("floor"),
            "area": m.get("area"),
            "price": m.get("price"),
            "status_label": r["label"] or "—",
            "is_active": r["status"] == BARTER_KEY,
            "deal": r["deal"] or "",
            "mgr": r["mgr"] or "",
            "date_local": ts_to_local(r["date"]) if r["date"] else "",
            "has_history": r["history_count"] > 0,
            "contractor": r.get("contractor") or "",
            "contractor_legal_entity": r.get("contractor_legal_entity") or "",
            "contractor_responsible": r.get("contractor_responsible") or "",
            "contractor_status_barter": r.get("contractor_status_barter") or "",
            "contractor_terms": r.get("contractor_terms") or "",
            "contractor_amount_contract": r.get("contractor_amount_contract") or 0,
            "contractor_amount_paid": r.get("contractor_amount_paid") or 0,
            "contractor_amount_done": r.get("contractor_amount_done") or 0,
            "contractor_remaining_to_pay": r.get("contractor_remaining_to_pay") or 0,
            "contractor_note": r.get("contractor_note") or "",
            "contractor_source": r.get("contractor_source") or "",
        })

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Реестр бартерщиков · Атамура</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f6f8; color: #1d2330; margin: 0; padding: 0; font-size: 14px; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
header {{ background: linear-gradient(135deg, #1a2433 0%, #2a3548 100%); color: white; padding: 32px 0; margin-bottom: 24px; border-bottom: 4px solid #c0392b; }}
header h1 {{ margin: 0 0 6px 0; font-size: 28px; }}
header .meta {{ color: #b8c1d0; font-size: 13px; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.kpi {{ background: white; padding: 20px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-top: 3px solid #1d4ed8; }}
.kpi.red {{ border-top-color: #c0392b; }}
.kpi.green {{ border-top-color: #15803d; }}
.kpi .label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: #6c757d; margin-bottom: 6px; font-weight: 600; }}
.kpi .value {{ font-size: 28px; font-weight: 700; }}
.kpi .hint {{ font-size: 12px; color: #6c757d; margin-top: 6px; }}
.filter-bar {{ background: white; padding: 14px 18px; border-radius: 10px; margin-bottom: 12px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
.pill {{ background: #f5f6f8; border: 1px solid #e4e7ec; padding: 5px 12px; border-radius: 14px; cursor: pointer; font-size: 13px; user-select: none; }}
.pill.active {{ background: #1a2433; color: white; border-color: #1a2433; }}
.filter-bar input, .filter-bar select {{ padding: 6px 10px; border: 1px solid #e4e7ec; border-radius: 5px; font-size: 13px; font-family: inherit; }}
.count {{ margin-left: auto; color: #6c757d; }}
.table-wrap {{ background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
thead th {{ background: #f5f6f8; padding: 10px 12px; text-align: left; border-bottom: 1px solid #e4e7ec; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.5px; color: #6c757d; font-weight: 600; position: sticky; top: 0; cursor: pointer; }}
tbody td {{ padding: 10px 12px; border-bottom: 1px solid #f0f2f5; }}
tbody tr:hover {{ background: #fafbfc; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.tag {{ display: inline-block; padding: 3px 9px; border-radius: 4px; font-size: 11.5px; font-weight: 600; }}
.tag-active {{ background: #fef3c7; color: #d97706; }}
.tag-confirmed {{ background: #dcfce7; color: #15803d; }}
.tag-closed {{ background: #dbeafe; color: #1d4ed8; }}
.subtle {{ color: #8a94a3; font-size: 11.5px; }}
</style>
</head>
<body>
<header>
  <div class="container">
    <h1>Реестр бартерщиков · Атамура</h1>
    <div class="meta">Источник: ProfitBase History API · Последний запуск: <b>{last_run}</b> · Последнее обработанное событие: {last_ts}</div>
  </div>
</header>
<div class="container">

<div class="kpi-grid">
  <div class="kpi red">
    <div class="label">Активный бартер</div>
    <div class="value">{len(active)}</div>
    <div class="hint">Объектов с customStatusId «Продано ЗБ»</div>
  </div>
  <div class="kpi green">
    <div class="label">Подтверждено историей</div>
    <div class="value">{len(active_with_hist)}</div>
    <div class="hint">Есть свежий CHANGE_STATUS event</div>
  </div>
  <div class="kpi">
    <div class="label">Стоимость в бартере</div>
    <div class="value">{fmt_money(total_value)}</div>
    <div class="hint">₸ — по price из ProfitBase</div>
  </div>
  <div class="kpi">
    <div class="label">Изменений за период</div>
    <div class="value">{len(closed_recent)}</div>
    <div class="hint">Объекты с историей смен (вышли/закрыты)</div>
  </div>
  <div class="kpi green">
    <div class="label">С подрядчиком</div>
    <div class="value">{len(with_contractor)}</div>
    <div class="hint">Из Google Sheet / Bitrix24</div>
  </div>
</div>

<div class="kpi-grid">
  <div class="kpi">
    <div class="label">Сумма договоров (бартер)</div>
    <div class="value">{fmt_money(total_contract)}</div>
    <div class="hint">₸ — из Google Sheet</div>
  </div>
  <div class="kpi green">
    <div class="label">Выполнено</div>
    <div class="value">{fmt_money(total_done)}</div>
    <div class="hint">₸ — подрядчик отработал</div>
  </div>
  <div class="kpi">
    <div class="label">Оплачено деньгами</div>
    <div class="value">{fmt_money(total_paid)}</div>
    <div class="hint">₸ — наличные/безнал доплаты</div>
  </div>
  <div class="kpi red">
    <div class="label">Остаток к оплате</div>
    <div class="value">{fmt_money(total_remaining)}</div>
    <div class="hint">₸ — компания должна подрядчикам</div>
  </div>
</div>

<div class="kpi" style="margin-bottom: 16px; padding: 16px 22px">
  <div class="label">ТОП ПОДРЯДЧИКОВ (по числу квартир)</div>
  <div style="display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 10px; font-size: 13px;">
    {chr(10).join(f'<span><b>{name}</b>: {n}</span>' for name, n in contractor_counts.most_common(15))}
  </div>
</div>

<div class="filter-bar">
  <span class="pill active" data-filter="all">Все</span>
  <span class="pill" data-filter="active">⚠️ Активный бартер</span>
  <span class="pill" data-filter="confirmed">✅ Подтверждён историей</span>
  <span class="pill" data-filter="with_contractor">👤 С подрядчиком</span>
  <span class="pill" data-filter="closed">📕 Закрыт / другой статус</span>
  <input type="text" id="search" placeholder="Поиск: подрядчик, ЖК, номер" style="min-width: 240px"/>
  <span class="count" id="count"></span>
</div>

<div class="table-wrap">
<table>
<thead>
<tr>
  <th>ЖК / очередь</th>
  <th>Секция/№</th>
  <th>Этаж</th>
  <th class="num">м²</th>
  <th class="num">Цена</th>
  <th>Статус</th>
  <th>👤 Подрядчик</th>
  <th>Условия / Статус бартера</th>
  <th class="num">Договор ₸</th>
  <th class="num">Выполн. ₸</th>
  <th class="num">Остаток ₸</th>
  <th>Ответств.</th>
</tr>
</thead>
<tbody id="tbody"></tbody>
</table>
</div>

<p class="subtle" style="margin-top: 16px">
  «Активный бартер» = customStatusId = 132940 («Продано ЗБ»).
  «Подтверждён историей» = есть свежий CHANGE_STATUS event с переходом в этот статус (за период работы реестра).
  Объекты без свежей истории — в Бартере давно (&gt; 1 года), переход не попал в lookback API.
</p>

</div>

<script>
const DATA = {json.dumps(table_data, ensure_ascii=False)};
const state = {{filter: 'all', q: ''}};

function render() {{
  let rows = DATA;
  if (state.filter === 'active') rows = rows.filter(r => r.is_active);
  if (state.filter === 'confirmed') rows = rows.filter(r => r.is_active && r.has_history);
  if (state.filter === 'with_contractor') rows = rows.filter(r => r.contractor);
  if (state.filter === 'closed') rows = rows.filter(r => !r.is_active && r.has_history);
  if (state.q) {{
    const q = state.q.toLowerCase();
    rows = rows.filter(r => [r.project, r.house, r.number, r.deal, r.mgr, r.contractor, r.contractor_legal_entity, r.contractor_responsible, r.contractor_status_barter].some(v => (v||'').toLowerCase().includes(q)));
  }}
  document.getElementById('count').innerHTML = `Показано: <b>${{rows.length}}</b>`;
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = rows.map(r => {{
    let tag;
    if (r.is_active && r.has_history) tag = '<span class="tag tag-confirmed">✅ Подтверждён</span>';
    else if (r.is_active) tag = '<span class="tag tag-active">⚠️ Активный</span>';
    else tag = '<span class="tag tag-closed">' + r.status_label + '</span>';
    const contractorCell = r.contractor
      ? `<b>${{r.contractor}}</b>${{r.contractor_legal_entity && r.contractor_legal_entity !== r.contractor ? '<br><span class="subtle">' + r.contractor_legal_entity + '</span>' : ''}}`
      : '<span class="subtle">—</span>';
    const termsCell = r.contractor_terms || r.contractor_status_barter
      ? `${{r.contractor_terms || ''}}<br><span class="subtle">${{r.contractor_status_barter || ''}}</span>`
      : '<span class="subtle">—</span>';
    return `<tr>
      <td>${{r.project||'—'}} / ${{r.house||'—'}}</td>
      <td>${{r.section||'—'}} / №${{r.number||'—'}}</td>
      <td>${{r.floor||'—'}}</td>
      <td class="num">${{(r.area||0).toLocaleString('ru-RU')}}</td>
      <td class="num">${{(r.price||0).toLocaleString('ru-RU')}}</td>
      <td>${{tag}}</td>
      <td>${{contractorCell}}</td>
      <td>${{termsCell}}</td>
      <td class="num">${{r.contractor_amount_contract ? r.contractor_amount_contract.toLocaleString('ru-RU') : '<span class="subtle">—</span>'}}</td>
      <td class="num">${{r.contractor_amount_done ? r.contractor_amount_done.toLocaleString('ru-RU') : '<span class="subtle">—</span>'}}</td>
      <td class="num">${{r.contractor_remaining_to_pay ? '<b style=\"color:#c0392b\">' + r.contractor_remaining_to_pay.toLocaleString('ru-RU') + '</b>' : '<span class="subtle">—</span>'}}</td>
      <td>${{r.contractor_responsible || r.mgr || '<span class="subtle">—</span>'}}</td>
    </tr>`;
  }}).join('');
}}

document.querySelectorAll('.pill').forEach(p => p.addEventListener('click', () => {{
  document.querySelectorAll('.pill').forEach(x => x.classList.remove('active'));
  p.classList.add('active');
  state.filter = p.dataset.filter;
  render();
}}));
document.getElementById('search').addEventListener('input', e => {{ state.q = e.target.value; render(); }});
render();
</script>
</body>
</html>"""

    OUT.write_text(html, encoding="utf-8")
    print(f"✅ Дашборд: {OUT} ({len(html):,} bytes)")
    print(f"   Активных бартеров: {len(active)} (подтверждено: {len(active_with_hist)})")
    print(f"   Стоимость: {fmt_money(total_value)} ₸")


if __name__ == "__main__":
    main()
