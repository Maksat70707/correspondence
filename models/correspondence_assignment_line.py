from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from datetime import timedelta


class CorrespondenceAssignmentLine(models.Model):
    _name = "correspondence.assignment.line"
    _description = "Поручения"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id asc"

    incoming_id = fields.Many2one(
        "corr.incoming",
        ondelete="cascade",
    )

    user_id = fields.Many2one(
        "res.users",
        string="Назначено исполнителю",
        required=True,
        tracking=True,
    )

    # Поля для делегирования
    original_user_id = fields.Many2one(
        "res.users",
        string="Изначально назначено",
        readonly=True,
        help="Кому было назначено до делегирования",
    )

    delegated = fields.Boolean(
        string="Делегировано",
        default=False,
        readonly=True,
    )

    # Переназначение исполнителя
    reassign_user_id = fields.Many2one(
        "res.users",
        string="Переназначить",
        help="Выберите пользователя для переназначения задачи. "
             "После сохранения текущий исполнитель будет заменён.",
    )

    executor_history_ids = fields.Many2many(
        "res.users",
        relation="assignment_executor_history_rel",
        column1="assignment_id",
        column2="user_id",
        string="История исполнителей",
        readonly=True,
    )

    assigner_id = fields.Many2one(
        "res.users",
        string="Назначил",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
        tracking=True,
    )

    task = fields.Text(
        string="Задача/комментарий",
    )

    resolution_id = fields.Many2one(
        "correspondence.resolution.option",
        string="Резолюция",
        required=True,
        tracking=True,
    )

    report_needed = fields.Boolean(
        string="Требуется отчет",
        tracking=True,
    )

    deadline = fields.Date(
        string="Срок выполнения",
        tracking=True,
    )

    # Поле для отслеживания необходимости отправки уведомления
    needs_notification = fields.Boolean(
        string="Требуется уведомление",
        default=False,
        help="Флаг для cron - статус изменился и нужно отправить уведомление",
    )

    # Последний статус для которого отправлено уведомление
    last_notified_status = fields.Selection(
        [
            ("new", "Поручено"),
            ("in_progress", "В работе"),
            ("done", "Выполнено"),
            ("cancelled", "Отменено")
        ],
        string="Последний уведомлённый статус",
    )

    @api.onchange('resolution_id')
    def _onchange_resolution_id(self):
        """Устанавливает значения по умолчанию на основе резолюции"""
        if self.resolution_id:
            # Устанавливаем срок выполнения
            if self.resolution_id.days_for_completion:
                self.deadline = fields.Date.today() + timedelta(days=self.resolution_id.days_for_completion)
            # Устанавливаем требование отчёта
            self.report_needed = self.resolution_id.report_needed

    status = fields.Selection(
        [
            ("new", "Поручено"),
            ("in_progress", "В работе"),
            ("done", "Выполнено"),
            ("cancelled", "Отменено")
        ],
        default="new",
        string="Статус",
        required=True,
        tracking=True,
    )

    report = fields.Text(string="Отчет исполнителя", tracking=True)

    completion_datetime = fields.Datetime(
        string="Дата завершения",
        readonly=True,
        tracking=True,
    )

    attachment_ids = fields.Many2many(
        "ir.attachment",
        relation="correspondence_assignment_docs_rel",
        column1="line_id",
        column2="attachment_id",
        string="Документы",
    )

    def write(self, vals):
        """При изменении статуса помечаем запись для уведомления"""
        import logging
        _logger = logging.getLogger(__name__)
        
        # Запоминаем какие записи нужно пометить для уведомления
        records_to_notify_ids = []
        
        if 'status' in vals:
            new_status = vals['status']
            _logger.info(f"=== WRITE: Изменение статуса на {new_status} для записей {self.ids} ===")
            
            for rec in self:
                _logger.info(f"Запись {rec.id}: report_needed={rec.report_needed}, last_notified_status={rec.last_notified_status}, new_status={new_status}")
                
                # Помечаем для уведомления только если report_needed=True
                # и статус изменился на in_progress или done
                if rec.report_needed and new_status in ('in_progress', 'done'):
                    if rec.last_notified_status != new_status:
                        records_to_notify_ids.append(rec.id)
                        _logger.info(f"Запись {rec.id} будет помечена для уведомления")
        
        # Сохраняем основные изменения
        result = super().write(vals)
        
        # Помечаем записи для уведомления напрямую через SQL чтобы избежать рекурсии
        if records_to_notify_ids:
            _logger.info(f"Помечаю записи {records_to_notify_ids} для уведомления через SQL")
            self.env.cr.execute("""
                UPDATE correspondence_assignment_line
                SET needs_notification = TRUE
                WHERE id IN %s
            """, (tuple(records_to_notify_ids),))
        
        return result

    @api.model
    def _cron_send_status_notifications(self):
        """
        Cron-метод для отправки уведомлений о смене статуса заданий.
        Запускается в 12:00 и 17:30.
        Собирает все задания с needs_notification=True и report_needed=True,
        группирует по assigner_id и отправляет одно сообщение.
        """
        import logging
        _logger = logging.getLogger(__name__)
        _logger.info("=== CRON: Начало отправки уведомлений по заданиям ===")
        
        # Находим все задания требующие уведомления
        assignments = self.search([
            ('needs_notification', '=', True),
            ('report_needed', '=', True),
            ('status', 'in', ['in_progress', 'done']),
        ])
        
        _logger.info(f"Найдено заданий для уведомления: {len(assignments)}")

        if not assignments:
            _logger.info("=== CRON: Нет заданий для уведомления ===")
            return

        # Группируем по назначившему (assigner_id)
        assignments_by_assigner = {}
        for assignment in assignments:
            assigner = assignment.assigner_id
            if assigner not in assignments_by_assigner:
                assignments_by_assigner[assigner] = []
            assignments_by_assigner[assigner].append(assignment)

        _logger.info(f"Уведомлений будет отправлено: {len(assignments_by_assigner)} пользователям")

        # Получаем OdooBot
        odoobot = self.env.ref('base.partner_root', raise_if_not_found=False)
        if not odoobot:
            _logger.error("OdooBot не найден!")
            return

        # Отправляем уведомления каждому назначившему
        for assigner, assigner_assignments in assignments_by_assigner.items():
            if not assigner.partner_id:
                _logger.warning(f"У пользователя {assigner.name} нет partner_id")
                continue

            # Формируем текст сообщения
            message_lines = []
            for assignment in assigner_assignments:
                executor_name = assignment.user_id.name or ''
                resolution_name = assignment.resolution_id.name or ''
                
                # Определяем текст статуса
                if assignment.status == 'done':
                    status_text = 'выполнил(а)'
                elif assignment.status == 'in_progress':
                    status_text = 'взял(а) в работу'
                else:
                    continue

                if assignment.incoming_id:
                    incoming_name = assignment.incoming_id.name or ''
                    incoming_rec_id = assignment.incoming_id.id
                    line = f"{executor_name} {status_text} задачу «{resolution_name}» от входящего письма <a href='/web#id={incoming_rec_id}&model=corr.incoming'>{incoming_name}</a>"
                else:
                    line = f"{executor_name} {status_text} задачу «{resolution_name}»"
                message_lines.append(line)

            if message_lines:
                # Формируем HTML сообщение
                message_body = "<p><b>Отчёт по заданиям:</b></p><ul>"
                for line in message_lines:
                    message_body += f"<li>{line}</li>"
                message_body += "</ul>"

                _logger.info(f"Отправляю сообщение пользователю {assigner.name} через OdooBot")

                # Находим или создаём приватный канал между OdooBot и пользователем
                channel = self.env['mail.channel'].sudo().search([
                    ('channel_type', '=', 'chat'),
                    ('channel_partner_ids', 'in', [odoobot.id]),
                    ('channel_partner_ids', 'in', [assigner.partner_id.id]),
                ], limit=1)

                if not channel:
                    # Создаём новый приватный чат
                    channel = self.env['mail.channel'].sudo().create({
                        'name': f'OdooBot, {assigner.name}',
                        'channel_type': 'chat',
                        'channel_partner_ids': [(4, odoobot.id), (4, assigner.partner_id.id)],
                    })

                # Отправляем сообщение от имени OdooBot
                channel.sudo().with_context(mail_create_nosubscribe=True).message_post(
                    body=message_body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                    author_id=odoobot.id,
                )

            # Обновляем флаги для обработанных заданий
            for assignment in assigner_assignments:
                assignment.sudo().write({
                    'needs_notification': False,
                    'last_notified_status': assignment.status,
                })

        _logger.info("=== CRON: Завершение отправки уведомлений ===")

    is_overdue = fields.Boolean(
        string="Просрочено",
        compute="_compute_is_overdue",
        store=False,
    )

    incoming_state = fields.Selection(
        related="incoming_id.state",
        string="Статус документа",
        store=False,
        readonly=True,
    )

    """ incoming_partner_id = fields.Many2one(
        related="incoming_id.partner_id",
        string="Отправитель документа",
        store=False,
        readonly=True,
    ) """

    incoming_summary = fields.Text(
        related="incoming_id.summary",
        string="Содержание документа",
        store=False,
        readonly=True,
    )

    incoming_summary_short = fields.Char(
        string="Краткое содержание",
        compute="_compute_incoming_summary_short",
        store=False,
        readonly=True,
    )

    department_id = fields.Many2one(
        "hr.department",
        string="Подразделение",
        related="user_id.employee_id.department_id",
        store=True,
        readonly=True,
    )

    @api.depends("incoming_id.summary")
    def _compute_incoming_summary_short(self):
        for rec in self:
            text = rec.incoming_id.summary or ""
            if len(text) > 50:
                rec.incoming_summary_short = text[:50] + "..."
            else:
                rec.incoming_summary_short = text

    @api.depends("deadline", "status", "completion_datetime")
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.deadline:
                rec.is_overdue = False
            elif rec.status == "done":
                # Выполнено — проверяем, было ли с опозданием
                if rec.completion_datetime:
                    completed_date = rec.completion_datetime.date()
                    rec.is_overdue = completed_date > rec.deadline
                else:
                    rec.is_overdue = False
            else:
                # Не выполнено — проверяем текущую дату
                rec.is_overdue = rec.deadline < today

    # ---------------------------------------------------------
    # Attachments ownership fix
    # ---------------------------------------------------------

    def _fix_attachment_ownership(self):
        """
        Устанавливает res_model и res_id для вложений Many2many.
        Это необходимо для корректной работы прав доступа к вложениям.
        """
        for record in self:
            if record.attachment_ids:
                record.attachment_ids.write({
                    'res_model': record._name,
                    'res_id': record.id
                })
        return self

    # ---------------------------------------------------------
    # Делегирование
    # ---------------------------------------------------------

    def _check_delegation(self, user_id):
        """
        Проверяет, есть ли активная делегация для пользователя.
        Возвращает (delegator_user_id, delegated) или (user_id, False)
        """
        if not user_id:
            return user_id, False

        today = fields.Date.today()

        # Проверяем наличие модуля делегирования
        if "delegation.line" not in self.env:
            return user_id, False

        delegation = self.env["delegation.line"].sudo().search(
            [
                ("user_id", "=", user_id),
                ("from_date", "<=", today),
                ("to_date", ">=", today),
            ],
            limit=1,
        )

        if delegation and delegation.delegator_user_id:
            return delegation.delegator_user_id.id, True

        return user_id, False

    def _post_delegation_message(self, original_user, new_user):
        """
        Отправляет сообщение о делегировании в chatter родительского документа
        """
        if self.incoming_id:
            self.incoming_id.message_post(
                body=_(
                    "Задание делегировано: %(original)s → %(new)s<br/>"
                    "Резолюция: %(resolution)s"
                ) % {
                    'original': original_user.name,
                    'new': new_user.name,
                    'resolution': self.resolution_id.name if self.resolution_id else '',
                },
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )

    # ---------------------------------------------------------
    # Helpers: permissions
    # ---------------------------------------------------------

    def _is_director_or_admin(self):
        user = self.env.user
        return (
            user.has_group("correspondence.group_correspondence_director")
            or user.has_group("correspondence.group_correspondence_admin")
        )

    def _is_secretary(self):
        return self.env.user.has_group("correspondence.group_correspondence_secretary")

    # ---------------------------------------------------------
    # Helpers: activities
    # ---------------------------------------------------------

    def action_open_document(self):
        self.ensure_one()
        if not self.incoming_id:
            raise ValidationError(_("У данного поручения нет связанного документа."))
        return {
            "type": "ir.actions.act_window",
            "name": "Входящий документ",
            "res_model": "corr.incoming",
            "res_id": self.incoming_id.id,
            "view_mode": "form",
            "target": "current",
        }

    # ---------------------------------------------------------
    # ORM
    # ---------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("assigner_id"):
                vals["assigner_id"] = self.env.user.id

            # Проверяем делегирование
            original_user_id = vals.get("user_id")
            if original_user_id:
                new_user_id, delegated = self._check_delegation(original_user_id)

                if delegated:
                    vals["original_user_id"] = original_user_id
                    vals["user_id"] = new_user_id
                    vals["delegated"] = True

        records = super().create(vals_list)

        for record in records:
            # Отправляем сообщение о делегировании
            if record.delegated and record.original_user_id:
                original_user = self.env["res.users"].browse(
                    record.original_user_id.id)
                new_user = record.user_id
                record._post_delegation_message(original_user, new_user)

            # Фиксируем ownership вложений
            record._fix_attachment_ownership()

        # Sync execution-активити на родительских документах
        # (Вариант B: одна активити на пару (документ, юзер) — управляется на incoming)
        for incoming in records.mapped('incoming_id'):
            incoming._sync_execution_activities()

        return records

    def write(self, vals):
        if len(self) > 1:
            raise ValidationError(_("Массовое редактирование запрещено."))

        rec = self
        current_user = self.env.user

        # ---- Обработка переназначения ----
        # Если указан reassign_user_id — конвертируем в смену user_id
        if 'reassign_user_id' in vals and vals['reassign_user_id']:
            new_user_id = vals.pop('reassign_user_id')
            # Добавляем текущего исполнителя в историю
            if rec.user_id:
                vals['executor_history_ids'] = [(4, rec.user_id.id)]
            # Заменяем исполнителя
            vals['user_id'] = new_user_id
            # Очищаем поле переназначения (через SQL после write, чтобы избежать рекурсии)
        elif 'reassign_user_id' in vals:
            # Передали False/None — просто убираем из vals
            vals.pop('reassign_user_id')

        # ---- Блокировка при статусе Выполнено ----
        if rec.status == 'done':
            raise AccessError(
                _("Задание выполнено. Редактирование запрещено."))

        # ---- permissions ----
        # director/admin: everything
        if not rec._is_director_or_admin():
            # assigner can edit line (business choice)
            if rec.assigner_id == current_user:
                pass
            # executor can edit only limited fields
            elif rec.user_id == current_user or current_user in rec.executor_history_ids:
                allowed = {"status", "report",
                           "attachment_ids", "completion_datetime",
                           "reassign_user_id", "user_id", "executor_history_ids"}
                illegal = set(vals) - allowed
                if illegal:
                    raise AccessError(
                        _("Вы можете редактировать только свои задания. Поля статус, отчет и вложения."))
            else:
                raise AccessError(
                    _("Вы не можете редактировать чужую задачу."))

        old_user = rec.user_id
        old_status = rec.status
        incoming = rec.incoming_id

        # Если меняется user_id — проверяем делегирование
        if "user_id" in vals and vals["user_id"] != rec.user_id.id:
            new_user_id, delegated = self._check_delegation(vals["user_id"])
            if delegated:
                vals["original_user_id"] = vals["user_id"]
                vals["user_id"] = new_user_id
                vals["delegated"] = True
            else:
                vals["original_user_id"] = False
                vals["delegated"] = False

        # Проставляем completion_datetime ДО super().write() чтобы избежать
        # рекурсивного write (который бы упёрся в блокировку "status == done"
        # выше, потому что super().write уже применил новый статус)
        if old_status != "done" and vals.get("status") == "done":
            vals.setdefault("completion_datetime", fields.Datetime.now())

        res = super().write(vals)

        # Очищаем reassign_user_id после сохранения через SQL
        if rec.reassign_user_id:
            self.env.cr.execute("""
                UPDATE correspondence_assignment_line
                SET reassign_user_id = NULL
                WHERE id = %s
            """, (rec.id,))

        # ---- post-write hooks ----

        # Фиксируем ownership вложений при изменении
        if "attachment_ids" in vals:
            rec._fix_attachment_ownership()

        # Сообщение о делегировании при смене исполнителя
        if "user_id" in vals and rec.delegated and rec.original_user_id:
            original_user = rec.original_user_id
            new_user = rec.user_id
            rec._post_delegation_message(original_user, new_user)

        # Sync execution-активити на родителе при смене user_id или status
        # (Вариант B: одна активити на пару (документ, юзер) — управляется на incoming)
        if incoming and ("user_id" in vals or "status" in vals):
            incoming._sync_execution_activities()

        if old_status != "done" and vals.get("status") == "done":
            # completion_datetime уже выставлен в vals выше — не дублируем

            # auto-transition incoming to done when all tasks done
            if incoming and incoming.state in ('execution', 'rework'):
                if all(line.status == "done" for line in incoming.assignment_line_ids):
                    incoming._auto_transition_to_done()

        return res