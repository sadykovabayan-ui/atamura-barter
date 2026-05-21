# Реестр бартерщиков Атамуры

Автоматический реестр всех квартир в Бартере / «Продано ЗБ» из ProfitBase. Обновляется каждые 30 минут через GitHub Actions.

## Публичная ссылка

После настройки GitHub Pages: **`https://<твой-username>.github.io/atamura-barter/`**

Дашборд показывает:
- Активный бартер (текущие квартиры в статусе «Продано ЗБ»)
- Подтверждённые свежей историей CHANGE_STATUS
- Закрытые сделки (для истории)
- Топ покупателей и менеджеров
- Фильтры и поиск

## Структура репо

```
atamura-barter/
├── index.html               ← публичный дашборд (генерируется)
├── data/
│   └── registry.json        ← текущее состояние (обновляется автоматом)
├── scripts/
│   ├── pb_barter_registry.py    ← обновление через ProfitBase API
│   └── pb_render_registry.py    ← рендерит index.html из registry.json
└── .github/workflows/
    ├── update-registry.yml      ← cron каждые 30 минут
    └── pages.yml                ← деплой на GitHub Pages
```

## Как запустить (один раз, после `git push`)

### 1. Включить GitHub Pages

Settings → Pages → Source: **GitHub Actions** (не «Deploy from a branch»).

### 2. Добавить секреты

Settings → Secrets and variables → Actions → New repository secret:

| Имя | Значение |
|---|---|
| `PROFITBASE_API_KEY` | `app-de80d7963c8c022719a47707393bac60` |
| `PROFITBASE_BASE_URL` | `https://pb12230.profitbase.ru` |
| `TELEGRAM_BOT_TOKEN` | `8862999763:AAHXbfAkO8jLHvkBSrEhs_N8jNpIC4Q2Mjg` |
| `TELEGRAM_CHAT_ID` | `336961566` |

### 3. Запустить первый раз вручную

Actions → "Update Barter Registry" → Run workflow.

Через 1-2 минуты `registry.json` и `index.html` обновятся, GitHub Pages автоматически развернёт.

## Локальный запуск (для теста)

```bash
python3 scripts/pb_barter_registry.py
python3 scripts/pb_render_registry.py
open index.html
```

Конфиг берётся из переменных окружения или из `.env` файла в корне (не коммитить!).

## Что в Telegram

При новом бартере:
```
🆕 Новый бартер
Атмо V / №20 (эт.2, 64.56 м², 25 824 000 ₸)
Покупатель: ТОО Цемент-А
Менеджер: Аскар Елеуов
Контакт: Иван Иванов +77001234567
Дата: 21.05.2026 14:30
```

При закрытии:
```
✅ Бартер закрыт → Подписание ПДКП
Атмо IV / №13
Был у: Достар Лифт
Сейчас: ИП Сидоров
```

## Стоимость

Бесплатно:
- GitHub Pages: бесплатно для public репо (для private — нужен Pro $4/мес)
- GitHub Actions: 2000 минут/мес бесплатно, наш workflow ~30 сек × 48 запусков/день = ~24 мин/день = ~720 мин/мес. **Укладываемся в free tier.**
- ProfitBase API: уже куплен
- Telegram Bot: бесплатно
