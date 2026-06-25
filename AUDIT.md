# AUDIT — модуль `correspondence` (Odoo 19)

Аудит только по `correspondence/`. Соседние модули (`appstream_approval`,
`docx_report_pro`, `delegation`, `hr_employee_extended`) читались для контекста,
правки в них не предлагаются.

Уровни уверенности проставлены у каждой находки: **[точно]**, **[вероятно]**,
**[проверь]**. Патчи приведены «близко к», не применялись — применяй сам.

---

## Критичные баги

### 1. Исходящие уведомления не отправляются вообще — неверный xmlid шаблона  **[точно]**

`models/corr_outgoing_approve_process_mixin.py:1007-1012`

```python
template = self.env.ref(
    "correspondence.state_mixin_mail_template",
    raise_if_not_found=False
)
if not template:
    return
```

Запись с xmlid `correspondence.state_mixin_mail_template` **не существует**. Файл
`data/state_mixin_mail_template.xml` определяет два шаблона:
`corr_incoming_mail_template` и `corr_outgoing_mail_template` (проверено грепом —
других записей в файле нет). Поэтому `self.env.ref(..., raise_if_not_found=False)`
всегда возвращает `False`, и `action_notify` молча выходит на первой же строке.

Следствие: на всём исходящем workflow (`action_notify("agreement", signer)` для
руководителя, esp_signer, секретаря, мед.работника, и `action_notify("approved")`
инициатору) **не уходит ни письмо, ни сообщение в чаттер**. Activity-задачи ещё
создаются фреймворком (`_schedule_approval_activity`), поэтому баг визуально
неочевиден — у людей появляется «To-Do», но обещанное уведомление не приходит.

Исправление — поправить xmlid на реально существующий:

```python
template = self.env.ref(
    "correspondence.corr_outgoing_mail_template",
    raise_if_not_found=False
)
```

---

### 2. `prepare_medical_assessment_report_values` падает: `.with_context().name` на Selection/Char  **[точно]**

`models/correspondence_outgoing.py:1210` и `:1234`

```python
'employee_udo_issuing_authority': self.employee_udo_issuing_authority.with_context(lang=language_context).name if employee else '',
...
'medical_worker_job': self.medical_worker_job_id.with_context(lang=language_context).name if self.medical_worker_job_id else '',
```

Это ровно те копипаст-баги, про которые ты писал «могут быть ещё» — два штуки
остались в этом же методе:

- `employee_udo_issuing_authority` — **Selection** (`correspondence_outgoing.py:151`,
  `related="employee_id.issuing_authority"`; в `hr_employee_extended/models/hr_employee_base.py:123`
  это `fields.Selection`). Значение поля — строка-ключ (`'mvd'` и т.п.). У строки
  нет `.with_context()` → `AttributeError`. Под guard `if employee`, не
  `if self.employee_udo_issuing_authority`, поэтому падает всегда, когда задан
  `employee_id` (а для мед.освидетельствования он задан).
- `medical_worker_job_id` — **Char** (`correspondence_outgoing.py:253`,
  `related="medical_worker_id.function"`; `function` на `res.partner` — Char).
  Та же ошибка: `str.with_context(...).name` → `AttributeError`.

Путь срабатывания: первый подписант жмёт «Подписать с ЭЦП» → `/esp/get_sign_obj`
→ `_get_document_to_sign` → `_get_signing_document()` (шаг 3, генерация из шаблона,
т.к. `attachment_to_sign_ids` для этого типа пуст) → `render_docx(...medical_examination_pdf)`
→ `prepare_medical_assessment_report_values` → падение. Контроллер
`controllers/sign_esp.py:32-39` ловит исключение и возвращает `False`, поэтому
пользователь видит «Документ для подписания не найден» вместо трейсбека — флоу
мед.освидетельствования тихо нерабочий.

У тебя уже есть готовый хелпер `_selection_label` (`correspondence_outgoing.py:810`),
но он **нигде не вызывается**. Применить его для Selection, а для Char убрать
`.with_context().name`:

```python
'employee_udo_issuing_authority': self._selection_label('employee_udo_issuing_authority', language_context) if employee else '',
...
# function на res.partner не переводится — это просто Char
'medical_worker_job': self.medical_worker_job_id or '',
```

(Корректный паттерн для перевода Selection в твоём же коде:
`hr_employee_extended/models/hr_contract.py:846` — `_get_selection_field_translated`.)

---

### 3. `/correspondence/download/file` — публичная загрузка любого вложения по id (IDOR)  **[точно, безопасность]**

`controllers/portal.py:122-135`

```python
@http.route('/correspondence/download/file/<model("ir.attachment"):record>',
            type='http', auth='public')
def download_attachment(self, record):
    file_content = base64.b64decode(record.sudo().datas)
    filename = record.sudo().name
    ...
    return request.make_response(file_content, headers=headers)
```

`auth='public'` + `record.sudo().datas` без какой-либо проверки владельца.
Любой неаутентифицированный пользователь, перебирая `ir.attachment` id, может
скачать **любое вложение в системе** (не только корреспонденцию — любые ЭЦП,
договоры, кадровые сканы и т.д.). Это классический broken access control.

Минимум: `auth='user'` и проверка, что запрашивающий — мед.работник документа,
которому принадлежит вложение, либо что вложение лежит на доступном ему
`corr.outgoing`:

```python
@http.route('/correspondence/download/file/<int:attachment_id>',
            type='http', auth='user')
def download_attachment(self, attachment_id):
    att = request.env['ir.attachment'].sudo().browse(attachment_id)
    if not att.exists() or att.res_model != 'corr.outgoing':
        raise NotFound()
    doc = request.env['corr.outgoing'].sudo().browse(att.res_id)
    if doc.medical_worker_id != request.env.user.partner_id:
        raise NotFound()
    # отдать файл
```

(Шаблон `portal_correspondence.xml:182,196` зовёт этот роут только для
`attachment_to_sign_ids`/`attachment_additional_sign_ids` мед.работника, так что
ужесточение портального сценария не ломает.)

---

### 4. Валидация формата вложения перед ЭЦП (`method_in_middle`) фактически мёртвая  **[вероятно]**

`models/correspondence_outgoing.py:718-730`

```python
def method_in_middle(self):
    if self.state == 'under_approval':
        allowed_mimetypes = [ ...docx..., 'application/pdf' ]
        for attachment in self.attachment_to_sign_ids:
            if attachment.mimetype not in allowed_mimetypes:
                raise ValidationError(_("Формат вложения ... только .pdf или .docx!"))
```

`method_in_middle` вызывается только из `_process_post_approval`
(`corr_outgoing_approve_process_mixin.py:514-515`), который исполняется в
`after_script` старого статуса. По порядку фреймворка (см. `appstream_approval/CLAUDE.md`)
к моменту `after_script` поле `state` уже переведено на следующий статус (`approval`).
То есть когда `method_in_middle` реально вызывается, `self.state == 'approval'`,
а guard проверяет `== 'under_approval'` → условие никогда не истинно → проверка
формата не выполняется. Файл некорректного формата дойдёт до подписания (в
`add_to_history` он просто молча не забрендируется, т.к. там ветвление по
mimetype).

Чинить либо переносом проверки в `action_send_to_approval` (где `state == 'draft'`
ещё актуален), либо проверять переданный `cur_state`, а не `self.state`:

```python
# вариант: валидировать на входе в согласование
def action_send_to_approval(self):
    ...
    self.method_on_start()
    self._validate_sign_attachment_format()   # вынести mimetype-чек сюда
```

> **[проверь]** подтверди на реальном прогоне under_approval→approval, что
> `self.state` действительно уже `approval` внутри `method_in_middle` — это
> зависит от тайминга фреймворка, который ты не контролируешь.

---

### 5. Кнопки «Скачать PDF/DOCX» падают для simple/guarantee/change_conditions — нет дефолтного отчёта  **[вероятно]**

`models/correspondence_outgoing.py:1476-1480` и `download_report_pdf/docx` (1507-1533)

```python
'default': {
    'pdf': 'correspondence.correspondence_outgoing_template_pdf',
    'docx': 'correspondence.correspondence_outgoing_template_docx',
},
```

Отчётов `correspondence_outgoing_template_pdf` / `_docx` в
`reports/correspondence_reports.xml` **нет** (проверено грепом — определены только
medical_checkup/explanation/medical_examination(+kaz)/job_offer(+kaz)/reference(+eng)).
Для типов, попадающих в `default` (simple, guarantee, change_conditions),
`_get_report_by_type` вернёт несуществующий xmlid, и `download_report_pdf`
поднимет `UserError("Отчёт '%s' не найден")`. Кнопки в шапке видны для
`state in ['processing','done','review']` без фильтра по типу
(`views/correspondence_outgoing_views.xml:161-172`), так что для обычного письма
пользователь жмёт «Скачать PDF» и получает ошибку.

Варианты: (а) скрывать кнопки скачивания для типов без шаблонного отчёта
(`invisible="not show_employee_fields and not show_medical_checkup ..."` или
по новому compute `has_report`), либо (б) добавить недостающие default-отчёты.
Если это задумано (у simple-письма есть свой `attachment_to_sign_ids`), то хотя бы
скрыть кнопки.

---

### 6. Секретарь может открыть rework-визард, но `action_confirm` его блокирует  **[вероятно]**

`wizards/correspondence_rework_wizard.py:23-31`

```python
def action_confirm(self):
    self.ensure_one()
    user = self.env.user
    if not (
        user.has_group('correspondence.group_correspondence_director')
        or user.has_group('correspondence.group_correspondence_admin')
    ):
        raise AccessError(_('Недостаточно прав.'))
```

Несогласованность прав: кнопка открытия визарда
`action_open_rework_wizard` (`correspondence_incoming.py:480-484`) и сама бизнес-логика
`action_send_to_rework` (`:506-513`) **разрешают секретаря**, а `action_confirm`
визарда — нет. Секретарь открывает визард, заполняет задачи, жмёт «Подтвердить» →
`AccessError`. Сценарий «секретарь отправляет на доработку задач» сломан.

Привести к одному списку групп — добавить секретаря (или вовсе убрать проверку из
визарда, т.к. `action_send_to_rework` её уже делает):

```python
if not (
    user.has_group('correspondence.group_correspondence_director')
    or user.has_group('correspondence.group_correspondence_admin')
    or user.has_group('correspondence.group_correspondence_secretary')
):
    raise AccessError(_('Недостаточно прав.'))
```

---

## Открытый вопрос: непоследовательный `sudo()` в `_on_return` / `_on_reject`

Ты просил отметить отдельно — вот разбор.

**`_on_return`:**
- Входящая (`correspondence_incoming.py:304-323`): `self.state_agreement_line_ids.unlink()`
  и `self.activity_schedule(...)` — **без sudo**.
- Исходящая (`correspondence_outgoing.py:838-877`): `self.sudo().state_agreement_line_ids.unlink()`
  и `self.sudo().activity_schedule(...)` — **с sudo**.

Доступ на `unlink` записей `appstream.approval.agreement.line`
(`security/ir.model.access.csv:29-32`): employee — read-only, **секретарь —
тоже без unlink** (`perm_unlink=0`), director/admin — можно. Возврат входящей идёт
из `review`, где согласующий обычно директор (unlink есть), поэтому отсутствие
sudo пока «прокатывает». Но это хрупко: как только возврат входящей сможет
инициировать кто-то без unlink-права на agreement.line (через
`additional_statement` → `revision`, либо если согласующим review станет
секретарь), `_on_return` упадёт `AccessError`. Исходящая поэтому и сделана через
sudo (там среди согласующих бывают рядовые employee). **Рекомендация:** привести
входящую к тому же sudo, что и исходящую — ради единообразия и устойчивости.

**`_on_reject` — связанная несостыковка [проверь]:**
- Входящая (`correspondence_incoming.py:296-302`): только `message_post`, **линии
  согласования НЕ чистятся**.
- Исходящая (`correspondence_outgoing.py:816-836`): `self.sudo().state_agreement_line_ids.unlink()`.

То есть у входящей после отклонения на `canceled` остаются висеть «in_progress»
согласующие, а её же `_on_return` их чистит. Не падает, но данные грязные и
ведут себя по-разному. Если так и задумано — ок; иначе добавить
`self.sudo().state_agreement_line_ids.unlink()` во входящий `_on_reject`.

---

## Улучшения идиоматики

### И1. `Command.*` вместо числовых кортежей  **[точно]**

`models/corr_outgoing_approve_process_mixin.py:627-628` и `:984-985`:

```python
setattr(self.sudo(), field_name, [(3, attachment.id)])
setattr(self.sudo(), field_name, [(4, new_attachment.id)])
```

Это два отдельных write. Объединить в один + `Command`:

```python
setattr(self.sudo(), field_name,
        [Command.unlink(attachment.id), Command.link(new_attachment.id)])
```

(`Command` уже импортирован в этом файле.)

Также `models/correspondence_assignment_line.py:524` —
`vals['executor_history_ids'] = [(4, rec.user_id.id)]` → `[Command.link(rec.user_id.id)]`;
и `:281-282` создание `discuss.channel.member` — `(0, 0, {...})` → `Command.create({...})`
(не забудь добавить `from odoo import Command` в этот файл — сейчас его там нет).

### И2. Применить уже написанный `_selection_label`; для `time_type` — тоже  **[точно]**

`_selection_label` (`correspondence_outgoing.py:810-814`) определён, но не
используется нигде. Кроме фикса бага №2, он же напрашивается в
`prepare_job_offer_report_values` (`:1307-1309`), где сейчас вручную:

```python
time_type_dict = dict(self._fields['time_type'].selection)
time_type_label = time_type_dict.get(self.time_type, '')
```

→ `time_type_label = self._selection_label('time_type', 'ru_RU')`. Один источник
истины для лейблов Selection.

### И3. Дефолт `shipment_method_ids` через xmlid, а не поиск по имени  **[вероятно]**

`models/correspondence_incoming.py:43`:

```python
default=lambda self: self.env["correspondence.shipment.method"].search([("name", "=", "Корпоративная электронная почта")], limit=1).ids
```

Хрупко (ломается при переводе/переименовании). Запись имеет xmlid `corp_email`
(`data/shipment_method.xml:3`):

```python
default=lambda self: self.env.ref("correspondence.corp_email", raise_if_not_found=False).ids
```

### И4. Ссылки в почтовых шаблонах — устаревший формат + неопределённый `base_url`  **[проверь]**

`data/state_mixin_mail_template.xml:59,157`:

```xml
<a t-attf-href="{{ base_url }}/web#id={{ obj.id }}&amp;view_type=form&amp;model={{ obj._name }}">
```

Формат `/web#id=...&model=...` устарел (в Odoo 17+ — `/odoo/<model>/<id>`; кстати,
крон в `correspondence_assignment_line.py:249` уже мигрирован на новый формат —
тут рассинхрон). Плюс переменная `base_url` в контексте рендера mail.template по
умолчанию не определена — href, скорее всего, рендерится с пустым началом.
Заменить на `{{ object.get_base_url() }}/odoo/{{ object._name }}/{{ object.id }}`.

### И5. `action_notify` мутирует общий `template.model_id` на каждый вызов  **[вероятно]**

Входящая `corr_incoming_approve_process_mixin.py:280-282`, исходящая `:1014-1019`:

```python
template.model_id = self.env["ir.model"].sudo().search([("model","=",self._name)]).id
```

Модель шаблона фиксирована (incoming/outgoing), так что это бесполезный write в
расшаренную запись на каждое уведомление (лишний UPDATE + риск гонок при
параллельных транзакциях). Убрать — шаблоны уже привязаны к нужной модели через
`model_id` в XML.

### И6. `import logging` внутри тел методов  **[вероятно, мелочь]**

`correspondence_incoming.py:223-224` (в `write`) и
`correspondence_assignment_line.py:147-148, 189-190` импортируют logging локально,
хотя в этих же файлах модульного `_logger` нет. Завести один модульный
`_logger = logging.getLogger(__name__)` сверху, как сделано в обоих mixin-ах.
Заодно много `_logger.info(f"...")` в горячем `write`/кроне — это шумные
INFO-логи на каждый апдейт статуса; стоит понизить до debug.

### И7. `@api.depends_context('uid')` на per-user compute  **[проверь]**

`_compute_is_employee_signer` (`correspondence_outgoing.py:565-583`) и
`_compute_can_cancel` (`:559-563`) читают `self.env.user`, но без
`@api.depends_context("uid")` (в отличие от `_compute_is_initiator` `:552`,
где он есть). Для нестора compute-полей это может отдать закэшированное от другого
пользователя значение в рамках общего кэша. Добавить `@api.depends_context("uid")`
для единообразия с `is_initiator`.

---

## Архитектурные предложения

### А1. Общий базовый mixin для двух approve-process mixin  **[трудоёмкость: средняя]**

`corr_incoming_approve_process_mixin` и `corr_outgoing_approve_process_mixin`
содержат практически идентичные методы:

- `get_current_coordinator` — побайтово одинаковы
  (incoming `:123-127` == outgoing `:397-400`);
- `additional_filter`, `filter_connection`, `check_group` — одинаковы
  (incoming `:251-261`, outgoing `:994-1001`);
- `add_to_history` — блок создания history-line идентичен
  (incoming `:219-249`, outgoing `:525-552`; в outgoing сверху ещё ЭЦП-брендинг);
- `get_agreement_lines_by_approval_groups` — отличается только параметром
  `need_esp` и `action_notify` на seq==1 (incoming `:71-121`, outgoing `:356-391`).

Черновик:

```python
class CorrApproveProcessBase(models.AbstractModel):
    _name = "corr.approve.process.base"

    def get_current_coordinator(self): ...
    def additional_filter(self, init_approvers=None): return init_approvers
    def filter_connection(self, approval_groups=None): return approval_groups
    def check_group(self, group_ids): return group_ids.user_ids
    def _append_history_line(self, coordinator, state, status): ...   # общий Command.create
    def get_agreement_lines_by_approval_groups(self, ..., need_esp=False): ...
```

Оба существующих mixin наследуют его, специфику (ЭЦП-брендинг в outgoing,
ветка execution в incoming) оставляют у себя. Снимает ~120 строк дублирования и
один класс багов «поправили в одном, забыли в другом».

### А2. Общий mixin для самих документов corr.incoming / corr.outgoing  **[трудоёмкость: средняя]**

Дублируется почти 1:1:
- `_fix_attachment_ownership` (incoming `:179-198`, outgoing `:680-692`) — отличается
  только списком полей-вложений;
- `_compute_display_name` (incoming `:167-173`, outgoing `:694-700`) — идентичен;
- `create()` с генерацией номера из последовательности (incoming `:204-220`,
  outgoing `:658-672`) — идентичная логика, разный код последовательности.

Черновик `corr.document.base` с абстрактным `_attachment_field_names()` и
`_sequence_code()`, которые переопределяются в наследниках. Экономит ~60 строк.

### А3. Разбить `get_agreement_lines_by_before_approval_groups` (outgoing) на per-status билдеры  **[трудоёмкость: низкая]**

`corr_outgoing_approve_process_mixin.py:124-279` — ~155 строк, switch по `status`
(under_approval / review / approval / approval_medical_examination / processing) +
общий хвост. Ты уже вынес `_build_approval_medical_examination_lines` — добей
остальные по той же схеме:

```python
_STATUS_BUILDERS = {
    'under_approval': '_build_under_approval_lines',
    'approval': '_build_approval_lines',
    'processing': '_build_processing_lines',
    'review': '_build_review_lines',
    'approval_medical_examination': '_build_approval_medical_examination_lines',
}
def get_agreement_lines_by_before_approval_groups(self, ..., status=None, need_esp=False):
    builder = getattr(self, self._STATUS_BUILDERS.get(status, ''), None)
    return builder(sequence, init_approvers, need_esp) if builder else ([], sequence, init_approvers)
```

Читаемость и тестируемость каждой ветки растут, общая логика «activity+Command.create»
выносится в `_make_line(...)`.

### А4. Разбить `prepare_medical_assessment_report_values`  **[трудоёмкость: низкая]**

`correspondence_outgoing.py:1139-1266` — ~130 строк, 3 ответственности: (1) поиск
подписанных строк по ролям esp_signer/medical_worker/employee, (2) форматирование
дат/QR, (3) сборка values. Вынести `_collect_med_exam_signers()` →
`{role: history_line}` и `_signer_block(line, lang)` → dict, тогда тело метода
станет декларативной сборкой. Заодно это место, где сидят баги №2.

### А5. Мёртвый код — удалить  **[трудоёмкость: низкая]**

- **`correspondence.outgoing.recipients`** (`models/correspondence_outgoing_recipient_line.py`
  + `views/correspondence_outgoing_recipients_views.xml`): модель **не импортируется**
  в `models/__init__.py`, view **не в** `__manifest__.py`, в access.csv записи нет,
  в меню не выведена. Полностью орфан — `corr.outgoing` использует
  `correspondence.correspondent.line`, а не эту. Удалить оба файла.
- **`additional_condition`** — `hasattr(self,"additional_condition")` в обоих
  `after_script` (incoming `:141`, outgoing `:411`), но метод нигде не определён →
  всегда `False`, ветка мёртвая. Либо убрать, либо задокументировать как hook.
- **ветка `from_portal=True`** в `_process_post_approval`
  (`corr_outgoing_approve_process_mixin.py:500-503`) и весь параметр `from_portal`:
  единственный «портальный» путь (`/sign_esp` → `record.action_approve()` →
  `after_script`) идёт с `from_portal=False`. Докстринг (`:407-408, 443`) ссылается
  на `portal_sign_esp`, которого в коде нет (грепнул — только в комментариях).
  Ветка не вызывается → удалить параметр и обновить докстринг.
- **`prepare_report_values`** (`:997-1000`) и `_compute_all_assignments_done` /
  `all_assignments_done` (`correspondence_incoming.py:92-118`): первый вызывается
  только дефолтным отчётом, которого нет (см. баг №5); второй computed, но в логике
  не используется (авто-переход считает терминальность инлайн в
  `_auto_transition_to_done`/`assignment_line.write`), в форме лежит `invisible="1"`.
  Проверить и снести, если подтвердится неиспользование.

### А6. Хрупкость guard-а в `get_agreement_lines` (incoming vs outgoing)  **[проверь]**

Outgoing `get_agreement_lines` (`:70-77`) имеет guard «если текущий user уже
in_progress — выходим», который нейтрализует преждевременный вызов `before_script`
нового статуса (фреймворк зовёт before_script ДО after_script). Incoming
`get_agreement_lines` (`:13`) такого guard-а не имеет и работает только потому, что
единственный многошаговый переход (review→execution) целит в статус без
`before_script`. Это разные неявные механизмы для одной задачи. Не баг, но как
только во входящей появится цепочка статусов с before_script — она сломается так
же, как сломалась бы исходящая без guard-а. Стоит вынести единый, явно
документированный механизм (см. А1) и покрыть комментарием про порядок
before/after_script.

---

## Summary

**Найдено: 6 критичных, 7 идиоматических, 6 архитектурных** (+ 1 открытый вопрос
по sudo, который ты просил).

### Топ-5 что чинил бы первым

1. **Баг №1** — исходящие уведомления не уходят (битый xmlid
   `state_mixin_mail_template`). Тихий, бьёт по всему исходящему флоу. Фикс — одна строка.
2. **Баг №2** — `prepare_medical_assessment_report_values` падает на Selection/Char
   (`employee_udo_issuing_authority`, `medical_worker_job_id`). Ломает генерацию и
   подписание мед.освидетельствования. Хелпер `_selection_label` уже написан.
3. **Баг №3** — публичная загрузка любого `ir.attachment` по id (IDOR). Безопасность.
4. **Баг №6** — секретарь не может подтвердить rework-визард (рассинхрон прав).
5. **Баг №5** — кнопки «Скачать PDF/DOCX» падают для simple/guarantee/change_conditions
   (нет default-отчёта).

### Места, где нет уверенности (проверь сам)

- **Баг №4** (`method_in_middle`): уверен в логике порядка before/after_script по
  CLAUDE.md, но это тайминг фреймворка — подтверди прогоном, что `self.state` уже
  `approval` внутри `method_in_middle`.
- **`_on_reject` входящей** не чистит agreement-линии (в отличие от исходящей и от
  своего же `_on_return`) — подтверди, задумано ли.
- **Дедуп в `appstream.approval.agreement.line.create`** (`приложение колег`,
  `:86-88`) схлопывает строки по `user_id`. Если в мед.освидетельствовании
  esp_signer / medical_worker / employee окажутся одним пользователем (или в
  medical_checkup/explanation esp_signer == employee) — лишняя строка молча
  исчезнет, и может не остаться ни одной `in_progress` → документ застрянет.
  Граничный кейс, проверь, нужна ли на нашей стороне защита (например, валидация
  «подписанты различны» в `method_on_start`).
- **Batch-write в `assignment_line.write`** (`:512-513`): `len(self) > 1` →
  `ValidationError`. Если в editable-list вкладки «Задания» отредактировать две
  строки с одинаковыми vals за одно сохранение, Odoo может сгруппировать их в один
  `write` на recordset из 2 записей → сохранение упадёт. Воспроизведи на двух
  строках со сменой статуса в один сейв.
- **И4/И7** помечены `[проверь]` — поведение `base_url` в mail.template и
  кэширование per-uid compute.
- **`_compute_is_vahta`** (`:502-506`): при отсутствии контракта `current_salary`
  = 0/False, `0 < 25000` → `is_vahta = True`, и справка с места работы напишет
  «вахтовый метод» сотруднику без контракта. Уточни, ок ли это.
