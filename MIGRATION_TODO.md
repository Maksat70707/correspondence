# Миграция correspondence на Odoo 19 — что осталось

## Заблокировано — ждём миграцию кастомного HR

- `corr.outgoing.employee_udo_number` (related → `udo_number`) — поле
  `udo_number` определяется в кастомном HR-расширении, которое ещё на
  Odoo 16. Когда кастомный HR будет на 19, продолжить установку.

## Не критично, сделать позже одной правкой каждое

- `controllers/portal.py:36` — `@route(type='json')` → `@route(type='jsonrpc')`.
- `models/correspondence_incoming.py:156` (и возможно ещё места) —
  `_(...)` в default-параметрах функций → `_lt(...)` (lazy translation).
- `_sql_constraints` → `models.Constraint` (warning при загрузке).

## Известные баги из исходника (не трогать при миграции синтаксиса)

- Дублированный метод `action_done()` в `models/correspondence_*.py` —
  Python использует второе определение, первое игнорируется.
- `attachment_count` без `@api.depends()` — поле не автообновляется.

## Что уже сделано

- Манифест: depends почищен (убраны report_xlsx + mol которых не было), 
  version 19.0.1.0.0.
- XML: все `<tree>` → `<list>`, `view_mode 'tree'` → `'list'`.
- XML: все `attrs=` (120 вхождений в 3 файлах) → отдельные атрибуты 
  invisible/readonly/required.
- XML: `<group>` внутри `<search>` убран.
- Python: `name_get()` → `_compute_display_name` в обеих моделях.
- venv доустановлен: python-docx, PyPDF2, reportlab, qrcode.
- БД: чистая Odoo 19 база `odoo19_test` с C/C locale.
- CLAUDE.md восстановлены (корневой + appstream_approval).

## Подтверждено работой

- 67/68 модулей включая appstream_approval, hr (stock), website, portal
  устанавливаются.
- correspondence парсится, импорты работают.
- Загрузка падает на model setup из-за `udo_number` (внешняя зависимость).