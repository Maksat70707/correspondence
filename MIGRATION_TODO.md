# Миграция correspondence на Odoo 19 — статус и оставшиеся задачи

## Статус: синтаксическая миграция завершена

Модуль успешно проходит:
- Python-импорт (все зависимости установлены в venv).
- Парсинг всех XML-файлов (security, views, data, reports).
- Создание таблиц в БД.
- Начало setup моделей.

Падает только на `udo_number` — внешняя зависимость, см. блокер ниже.

---

## Блокер — ждём миграцию кастомного HR

`corr.outgoing.employee_udo_number` — related-field, ссылается на поле
`udo_number`, которое определяется в кастомном HR-расширении (ещё на
Odoo 16). Сток-`hr` в Odoo 19 этого поля не содержит.

**Что делать когда кастомный HR будет на Odoo 19:**

1. Перенести/добавить мигрированный модуль в `caspineft/`.
2. Убедиться что он есть в `depends` у `correspondence/__manifest__.py`
   (если не было — добавить).
3. Пересоздать тестовую БД:
   ```sql
   DROP DATABASE odoo19_test;
   CREATE DATABASE odoo19_test WITH OWNER=odoo TEMPLATE=template0
     ENCODING='UTF8' LC_COLLATE='C' LC_CTYPE='C';
   ```
4. `F5` в VS Code.
5. Если падение — лог traceback'а сюда, разберём.

---

## Что уже сделано (миграция 16 → 19)

### Манифест (`__manifest__.py`)
- `version`: `16.0.0.6` → `19.0.1.0.0`.
- `depends`: убрана зависимость `report_xlsx` (модуль deprecated, в коде не
  использовался — все 14 отчётов на `report_type="docx"`).
- `depends`: `mol` отсутствует (выяснилось что не нужен).
- Лицензия: добавить `'license': 'LGPL-3'` при возможности (warning в логе).

### XML — синтаксис Odoo 17+

**`attrs=` → отдельные атрибуты `invisible/readonly/required/column_invisible`**
- 120 вхождений в 3 файлах:
  - `views/correspondence_outgoing_views.xml` — 83
  - `views/correspondence_incoming_views.xml` — 32
  - `views/correspondence_assignment_line_views.xml` — 5
- Преобразование domain-tuple → Python-выражения с учётом `or`/`and` и скобок.

**`<tree>` → `<list>`**
- 18 тегов в 10 файлах (все view-определения для список-вьюх и inline tree
  внутри form views).

**`view_mode='tree'` → `view_mode='list'`**
- 15 вхождений в 7 файлах (`ir.actions.act_window`).

**`<group>` внутри `<search>` — убран как deprecated**
- `views/correspondence_incoming_views.xml:338`
- `views/correspondence_outgoing_views.xml:488`
- Фильтры группировки оставлены, теперь рендерятся Odoo автоматически
  в правой панели "Group By".

### Python — синтаксис Odoo 17+

**`name_get()` → `_compute_display_name`**
- `models/correspondence_incoming.py:~161`
- `models/correspondence_outgoing.py:~647`

### Зависимости Python (venv)

Добавлено в `Odoo 19/venv/`:
- `python-docx` (`from docx import ...` для генерации Word-документов)
- `PyPDF2` (работа с PDF — подписание, объединение)
- `reportlab` (генерация PDF — A4, шрифты, штампы)
- `qrcode` (QR-коды на печатных формах и в подписях)

### Инфраструктура

- Создан venv: `Odoo 19/venv/`.
- Установлены все Odoo 19 requirements.
- PostgreSQL БД `odoo19_test` с `C/C` locale (на Windows critical).
- `local-odoo-test.conf` — отдельный конфиг для миграции
  (db_name, dbfilter, http_port).
- CLAUDE.md восстановлены:
  - `caspineft/CLAUDE.md` — корневой, правила миграции.
  - `caspineft/appstream_approval/CLAUDE.md` — архитектура фреймворка
    (точные имена полей, порядок script-хуков, lifecycle API).

### Подтверждено аудитом

**Интеграция с appstream_approval совместима с Odoo 19:**
- Lifecycle hooks `_on_reject`, `_on_return` — сигнатуры совпадают
  с фреймворком (`old_state=None, reason=None` и `new_state=None,
  old_state=None, reason=None` соответственно).
- Прямые вызовы `appstream.approval.agreement.history.line` — все
  поля (`signed`, `qr`, `agreement_date`, `user_id`) на месте.
- Поля mixin (`state`, `state_id`, `state_agreement_line_ids`,
  `approval_user_ids`, `user_can_approve`, `need_esp`, `button_*_enabled`)
  — все присутствуют в новой версии фреймворка.

---

## Открытые задачи — некритичные deprecations

### 1. `@route(type='json')` → `@route(type='jsonrpc')`

В Odoo 19 `type='json'` — deprecated алиас на `type='jsonrpc'`. Warning,
не блокер.

**Файл:** `controllers/portal.py:36` (и возможно ещё места в файле).

**Правка:** в декораторах `@route` / `@http.route` заменить
`type='json'` на `type='jsonrpc'`. Остальные параметры не трогать.

### 2. `_(...)` в default-параметрах функций → `_lt(...)`

Warning: `no translation language detected, skipping translation`.
Возникает когда `_("...")` стоит как default-значение параметра — он
вычисляется на этапе загрузки класса, language context ещё не активен.

**Найденное место:** `models/correspondence_incoming.py:156`:
```python
def _require_any_group(self, xmlids, message=_("Недостаточно прав.")):
```

**Правка:**
```python
from odoo import _, _lt
def _require_any_group(self, xmlids, message=_lt("Недостаточно прав.")):
```

Проверить все .py файлы модуля на этот паттерн (`=_(` и `= _(`
в сигнатурах функций). Внутри тела функций `_(...)` оставлять как есть.

Аккуратно: `_lt` возвращает lazy proxy. Если где-то идёт сравнение
через `==` или конкатенация со строкой — обернуть в `str(...)`.

### 3. `_sql_constraints` → `models.Constraint`

В Odoo 19 `_sql_constraints` deprecated, рекомендуется новый синтаксис.
Сейчас warning при загрузке, не блокер.

**Действие:** отложено до этапа функциональных доработок.

---

## Архитектурные заметки (унаследовано из 16-версии, не правки миграции)

### hasattr-проверки в outgoing._on_reject / _on_return

`models/correspondence_outgoing.py` в hooks `_on_reject` (~строка 689)
и `_on_return` (~строка 711) обращается к методам `get_current_coordinator`
и `add_to_history` через `hasattr`. Это защитный паттерн на случай если
mixin `corr_outgoing_approve_process_mixin` отключён.

Сейчас миксин всегда подключён через `_inherit`, паттерн работает
вхолостую, но не вредит.

### Несогласованность `sudo()` в `_on_return`

- `correspondence_outgoing.py` `_on_return`:
  `self.state_agreement_line_ids.sudo().unlink()` — обход прав
- `correspondence_incoming.py:286` `_on_return`:
  `self.state_agreement_line_ids.unlink()` — с проверкой прав

Расхождение унаследовано из 16-версии, **не регрессия миграции**.

**Уточнить с бизнес-владельцем:** при возврате документа на доработку
имеет ли пользователь право удалять agreement lines?
- Если да — обе строки должны быть с `sudo()` (выровнять с outgoing).
- Если нет — обе без `sudo()` (выровнять с incoming).

---

## Известные баги в исходнике 16 (НЕ ТРОГАТЬ при миграции синтаксиса)

### Дублированный `action_done()`

В `models/correspondence_*.py` метод `action_done()` определён дважды
(см. начало миграции и аудит). Python использует второе определение,
первое игнорируется. Семантика разная:
- Первое — простая смена статуса.
- Второе — требует вложений (`message_attachment_count > 0`),
  делает `self.write()` вне цикла.

**Решение откладывается** до этапа функциональных доработок —
уточнить какое поведение правильное.

### `attachment_count` без `@api.depends()`

Compute-поле не пересчитывается автоматически при изменении вложений.
Visible bug в UI, но не блокер для миграции синтаксиса.

**Решение откладывается.**

---

## Чек-лист для смены состояния "blocked" → "complete"

Когда кастомный HR появится на 19 и установка пройдёт:

- [ ] `--stop-after-init` снимается, `F5` запускает сервер
- [ ] Открывается http://localhost:8069
- [ ] Логин admin / admin
- [ ] Модуль "Correspondence" в меню видим
- [ ] **List view** — отрисовка, фильтры, сортировка
- [ ] **Form view** — создание тестовой incoming-записи
- [ ] **Form view** — создание тестовой outgoing-записи
- [ ] **Header buttons** — появляются/скрываются по `state` и `type`
- [ ] Пройти **полный workflow** (submit → approve → ... → done)
  на одной записи incoming
- [ ] То же на outgoing
- [ ] **Reject + Return** — wizard'ы работают, документ возвращается
- [ ] **Forward** — wizard, переадресация работает
- [ ] **Search view** — фильтры и группировки в правой панели
- [ ] **DevTools браузера (F12)** — нет красных ошибок
- [ ] **Portal** — внешняя ссылка на просмотр документа работает
- [ ] **Отчёты docx** — генерация одной из 14 печатных форм работает
- [ ] **ЭЦП-подписание** (если есть возможность тестового сертификата)

Если все галки — `git tag migration-functional` и снимаем блокер.

---

## Контрольные точки в git

| Тег | Состояние |
|-----|-----------|
| `migration-syntax-complete` | Синтаксическая миграция завершена, ждём HR |
| `migration-functional` (будущий) | Установка проходит, UI работает, продакшн-ready |





## Восстановить разграничение Print-меню по ролям

При миграции из `reports/correspondence_reports.xml` удалены 
`<field name="groups_id">` во всех 14 ir.actions.report — поле удалено 
в Odoo 17+.

### Что потерялось

В Odoo 16 эти `groups_id` ограничивали:
1. Видимость кнопки отчёта в Print-меню формы corr.outgoing.
2. Возможность вызвать отчёт через прямой URL (защита на серверной стороне).

### Что осталось безопасно

Защита данных не потеряна — для генерации отчёта нужен read-доступ к 
самой записи corr.outgoing, а на этом уровне у нас уже есть `ir.rule` 
в security/record_rules.xml.

### Что нужно вернуть

UX-разграничение: пользователь без нужной роли всё ещё **видит кнопку**
отчёта в Print-меню, но при клике получит AccessError или пустой результат
(потому что не имеет прав на саму запись).

### Как реализовать (Odoo 17+ способ)

Через `ir.rule` на модели ir.actions.report. Для каждого из 14 отчётов
создать record rule, ограничивающее видимость по группам пользователя.

Пример шаблона:

```xml
<record id="ir_rule_<report_id>" model="ir.rule">
    <field name="name">Доступ к отчёту: <название></field>
    <field name="model_id" ref="base.model_ir_actions_report"/>
    <field name="domain_force">
        [('id', '!=', ref('correspondence.<report_id>'))]
    </field>
    <field name="groups" eval="[(6, 0, [
        ref('correspondence.group_correspondence_employee'),
        ref('correspondence.group_correspondence_secretary'),
        ref('correspondence.group_correspondence_director'),
        ref('correspondence.group_correspondence_admin'),
    ])]"/>
</record>
```

Логика: правило применяется ТОЛЬКО к указанным группам, 
и для них «скрывает» все отчёты КРОМЕ конкретного. Это эквивалент
«отчёт виден только этим группам».

### Альтернатива на будущее (если групп много типов)

Для отчётов, доступных всем 4 группам — record rule не нужен,
все равно все видят.

Для отчётов с уникальными правами (например, кадровые направления 
видны только HR-сотрудникам, не всем) — отдельные record rules.

Сейчас все 14 отчётов имели одинаковые 4 группы — поэтому пока 
никто разграничение не теряет на практике (все 4 группы и так 
видят всё). Разграничение начнёт быть актуальным когда понадобятся 
отчёты с ограниченным доступом.

### Приоритет

Низкий. Все 14 отчётов имеют одинаковые группы, никто из текущих 
ролей разграничения не потеряет. Делать когда появится отчёт с 
действительно ограниченной аудиторией.