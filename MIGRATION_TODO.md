# Состояние модуля correspondence на момент аудита

> Файл предназначен для AI-аудита и любого нового контрибьютора.
> Миграция Odoo 16→19 функционально **завершена** — все ограничения
> режима миграции (типа «не трогать», «отложено до конца миграции»)
> **сняты**. Аудит имеет право пересматривать ранее обойдённые места.

## История миграции

См. git tag `migration-functional`. Полная история — в коммитах.

Кратко что было сделано (для понимания контекста, **не как инструкции что трогать/не трогать**):

- `groups_id` → `group_ids` на `res.users`
- `mail.channel` → `discuss.channel`
- `@api.depends` добавлены на compute-поля
- `Markup` wrap на HTML-телах `message_post`
- `tracking=False` переопределён на унаследованных `state_agreement_*_line_ids`
- ЭЦП-flow унифицирован через колежие `/esp/get_sign_obj`, `/sign_esp`, `/esp/save_signed_pdf`
- `portal_signing_mixin` удалён (был obsolete)
- `_schedule_approval_activity()` добавлен в `action_send_to_approval` skip-путь
- `view_mode` → `kanban,list,form` на основных action
- 3 копипаст-бага `.with_context(lang=...).name` на не-relation полях исправлены в `prepare_medical_assessment_report_values`
- `tracking=False` на унаследованных полях в `corr.incoming`
- Filter «На моём согласовании» удалён из обоих search view'ов (workaround на отсутствующее `wkf_groups_ids`)
- `domain="[('share', '=', False)]"` на `user_id`/`reassign_user_id` в `assignment_line`
- Secretary перенесён из all-меню в employee-меню Поручений

## Открытые вопросы (не блокирующие, ждут внешних решений)

1. **`sudo()` consistency в `_on_return`**
   У `corr.outgoing._on_return` — `self.state_agreement_line_ids.unlink()`.
   У `corr.incoming._on_return` — `self.sudo().state_agreement_line_ids.unlink()`.
   Бизнес-вопрос: имеет ли возвращающий право удалять линии под собой?
   Решение ждёт ответа владельца процесса.

2. **`wkf_groups_ids` отсутствует на `res.users`**
   `appstream_approval/models/approval_mixin.py:439` ссылается на это поле,
   но оно нигде не определено. Любой поиск через `approval_user_ids` падает с
   `KeyError`. Workaround: фильтр «На моём согласовании» удалён из search view'ов.
   Альтернатива: shim `wkf_groups_ids = fields.Many2many(related="group_ids")`.
   Ждёт фикса коллеги.

3. **Уникальность QR при подписании**
   `appstream_approval`'s `/sign_esp` генерит **один QR на документ** (по
   `approval_esp.uuid`), а не по подписанту. Поэтому 3 подписи медицинского
   направления показывают одинаковый QR. По решению коллеги — это by design,
   проверочная страница одна на документ. Наши шаблоны переадаптированы.

4. **Print-меню по ролям**
   Хотелось бы ограничить какие отчёты доступны какой роли. Фича, не миграция,
   отложено.

## Ранее зафиксированные баги в исходнике 16 — кандидаты на повторный разбор

Эти места были помечены «не трогать в фазе миграции» ради сохранения чистоты diff'а.
**Сейчас аудит ДОЛЖЕН их рассмотреть.**

1. **Дублированный метод `action_done()`** в `models/correspondence_*.py` —
   Python использует второе определение, первое — мёртвое. Семантика разная.
2. **`attachment_count` без `@api.depends()`** — поле не автообновляется при
   изменении вложений.

## Чего нет в этом репо но влияет

- **`appstream_approval`** — лежит рядом в `caspineft/`. Свой `CLAUDE.md`
  с архитектурой. **Аудит correspondence не предлагает правки в нём.**
- **`docx_report_pro`** — Caspineft engine отчётов с методом `render_docx`.
  Бинарные `.docx` шаблоны в `correspondence/static/templates/` им же
  обрабатываются.
- **`delegation`, `hr_employee_extended`** — другие модули Caspineft, могут
  встретиться в импортах/depends.