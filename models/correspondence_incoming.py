from odoo import api, fields, models, _, _lt
from odoo.exceptions import AccessError, UserError, ValidationError
from markupsafe import Markup


class IncomingDocument(models.Model):
    _name = "corr.incoming"
    _description = "Входящая корреспонденция"
    _inherit = [
        "mail.thread",
        "mail.activity.mixin",
        "appstream.approval.mixin",
        "corr.incoming.approve.process.mixin"
    ]
    _order = "id desc"

    name = fields.Char(
        string="Номер входящего документа",
        copy=False,
        readonly=True,
        default=lambda self: _("(Новый)"),
        tracking=True,
    )

    subject = fields.Char(string="Тема", required=True)

    summary = fields.Text(string="Содержание", required=True)

    # partner_id = fields.Many2one(
    #     "res.partner",
    #     string="Отправитель документа",
    #     required=True,
    #     tracking=True,
    # )

    shipment_method_ids = fields.Many2many(
        "correspondence.shipment.method",
        relation="corr_incoming_ship_rel",
        column1="recipient_id",
        column2="shipment_method_id",
        string="Метод получения",
        required=True,
        default=lambda self: self.env["correspondence.shipment.method"].search([("name", "=", "Корпоративная электронная почта")], limit=1).ids
    )
    doc_arrival_date = fields.Date(
        string="Дата входящего документа")

    correspondent_line_ids = fields.One2many(
        "correspondence.correspondent.line",
        "incoming_id",
        string="Корреспонденты",
    )

    language = fields.Selection(
        [
            ('kazakh', 'Казахский'),
            ('russian', 'Русский'),
            ('english', 'Английский'),
            ('bilingual', 'Двуязычный (Казахский и Русский)'),
        ],
        string="Язык письма",
        required=True,
        default='russian',
        tracking=True,
    )

    external_number = fields.Char(
        string="Внешний исходящий номер")

    attachment_mail_ids = fields.Many2many(
        "ir.attachment",
        relation="corr_incoming_mail_rel",
        column1="incoming_id",
        column2="attachment_id",
        string="Входящее письмо",
    )

    attachment_rest_ids = fields.Many2many(
        "ir.attachment",
        relation="corr_incoming_docs_rel",
        column1="incoming_id",
        column2="attachment_id",
        string="Документы от отправителя",
    )

    assignment_line_ids = fields.One2many(
        "correspondence.assignment.line",
        "incoming_id",
        string="Задания",
    )

    all_assignments_done = fields.Boolean(
        string="Все задания выполнены",
        compute="_compute_all_assignments_done",
        store=False,
    )

    assignment_count = fields.Integer(
        string="Количество заданий",
        compute="_compute_assignment_count",
    )

    @api.depends("assignment_line_ids")
    def _compute_assignment_count(self):
        for rec in self:
            rec.assignment_count = len(rec.assignment_line_ids)

    @api.depends("assignment_line_ids.status")
    def _compute_all_assignments_done(self):
        # Терминальные статусы поручения: выполнено или отменено.
        # Документ должен автозакрываться, когда все строки в одном из них.
        TERMINAL_STATUSES = ("done", "cancelled")
        for rec in self:
            if not rec.assignment_line_ids:
                rec.all_assignments_done = False
            else:
                rec.all_assignments_done = all(
                    l.status in TERMINAL_STATUSES for l in rec.assignment_line_ids)
                
                
    # Computed поля для видимости колонок в таблице получателей
    show_col_full_name = fields.Boolean(compute="_compute_recipient_columns")
    show_col_address = fields.Boolean(compute="_compute_recipient_columns")
    show_col_position = fields.Boolean(compute="_compute_recipient_columns")
    show_col_email = fields.Boolean(compute="_compute_recipient_columns")
    show_col_odoo_user = fields.Boolean(compute="_compute_recipient_columns")
    show_col_phone = fields.Boolean(compute="_compute_recipient_columns")
    state_agreement_line_ids = fields.One2many(tracking=False)
    state_agreement_history_line_ids = fields.One2many(tracking=False)

    @api.depends('correspondent_line_ids.shipment_method_ids')
    def _compute_recipient_columns(self):
        # Вычисляет видимость колонок на основе выбранных методов отправки у всех получателей
        courier_mail = self.env.ref('correspondence.courier_mail', raise_if_not_found=False)
        driver_mail = self.env.ref('correspondence.driver_mail', raise_if_not_found=False)
        personal_email = self.env.ref('correspondence.personal_email', raise_if_not_found=False)
        corp_email = self.env.ref('correspondence.corp_email', raise_if_not_found=False)
        odoo_notification = self.env.ref('correspondence.odoo_notification', raise_if_not_found=False)
        whatsapp = self.env.ref('correspondence.whatsapp', raise_if_not_found=False)

        for rec in self:
            # Собираем все методы отправки со всех получателей
            all_methods = rec.correspondent_line_ids.mapped('shipment_method_ids')

            # full_name и address - для courier_mail, driver_mail
            rec.show_col_full_name = courier_mail in all_methods or driver_mail in all_methods
            rec.show_col_address = courier_mail in all_methods or driver_mail in all_methods

            # position и email - для personal_email, corp_email
            rec.show_col_position = personal_email in all_methods or corp_email in all_methods
            rec.show_col_email = personal_email in all_methods or corp_email in all_methods

            # odoo_user_id - для odoo_notification
            rec.show_col_odoo_user = odoo_notification in all_methods

            # phone - для whatsapp
            rec.show_col_phone = whatsapp in all_methods
    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------

    def _require_any_group(self, xmlids, message=_lt("Недостаточно прав.")):
        user = self.env.user
        if not any(user.has_group(x) for x in xmlids):
            raise AccessError(message)

    def _compute_display_name(self):
        for record in self:
            if record.subject:
                name = f"{record.name} - {record.subject}"
            else:
                name = f"{record.name}"
            record.display_name = name

    # ---------------------------------------------------------
    # Attachments ownership fix
    # ---------------------------------------------------------

    def _fix_attachment_ownership(self):
        """
        Устанавливает res_model и res_id для вложений Many2many.
        Это необходимо для корректной работы прав доступа к вложениям.
        """
        attachment_fields = [
            'attachment_mail_ids',
            'attachment_rest_ids',
        ]
        
        for record in self:
            for field in attachment_fields:
                attachments = getattr(record, field)
                if attachments:
                    attachments.write({
                        'res_model': record._name,
                        'res_id': record.id
                    })
        
        return self

    # ---------------------------------------------------------
    # ORM
    # ---------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        # Сначала создаём записи (здесь проверяются права доступа)
        records = super().create(vals_list)
        
        # Генерируем номера только после успешного создания
        for record in records:
            if not record.name or record.name in (_("(Новый)"), _("Новый")):
                record.name = (
                    self.env["ir.sequence"].sudo().next_by_code("correspondence.incoming")
                    or _("Новый")
                )
        
        # Фиксируем ownership вложений
        records._fix_attachment_ownership()
        
        return records

    def write(self, vals):
        import logging
        _logger = logging.getLogger(__name__)
        
        # Запоминаем старые состояния — нужны для хука синхронизации
        # execution-активити (Вариант B миграции)
        old_states = {}
        if 'state' in vals:
            old_states = {rec.id: rec.state for rec in self}
        
        # Обрабатываем изменения статуса в assignment_line_ids
        if 'assignment_line_ids' in vals:
            for cmd in vals['assignment_line_ids']:
                # cmd[0] = 1 означает обновление существующей записи
                # cmd = (1, id, {values})
                if cmd[0] == 1 and len(cmd) == 3:
                    line_id = cmd[1]
                    line_vals = cmd[2]
                    if 'status' in line_vals:
                        new_status = line_vals['status']
                        # Получаем запись
                        line = self.env['correspondence.assignment.line'].browse(line_id)
                        if line.exists():
                            _logger.info(f"=== INCOMING WRITE: Изменение статуса задания {line_id} на {new_status} ===")
                            _logger.info(f"report_needed={line.report_needed}, last_notified_status={line.last_notified_status}")
                            
                            # Помечаем для уведомления если нужно
                            if line.report_needed and new_status in ('in_progress', 'done'):
                                if line.last_notified_status != new_status:
                                    _logger.info(f"Помечаю задание {line_id} для уведомления")
                                    self.env.cr.execute("""
                                        UPDATE correspondence_assignment_line
                                        SET needs_notification = TRUE
                                        WHERE id = %s
                                    """, (line_id,))
        
        res = super().write(vals)
        
        # Если изменились вложения — фиксируем ownership
        if 'attachment_mail_ids' in vals or 'attachment_rest_ids' in vals:
            self._fix_attachment_ownership()
        
        # Хук смены состояния: триггерим sync execution-активити при переходе
        # В/ИЗ execution/rework. Покрывает кейс перехода review→execution
        # (где раньше активити создавались через старый _create_mail_activity)
        # и transitions execution→done/revision/rejected где нужно закрыть.
        if 'state' in vals:
            for rec in self:
                old_state = old_states.get(rec.id)
                new_state = rec.state
                if old_state != new_state and (
                    new_state in ('execution', 'rework')
                    or old_state in ('execution', 'rework')
                ):
                    rec._sync_execution_activities()
        
        return res

    # ---------------------------------------------------------
    # Хуки для appstream_approval
    # ---------------------------------------------------------

    def method_on_start(self):
        """Валидация перед запуском согласования"""
        for rec in self:
            if not rec.subject:
                raise ValidationError(_("Укажите тему документа"))
            # if not rec.partner_id:
            #    raise ValidationError(_("Укажите отправителя документа"))

    def method_on_approved(self):
        """Вызывается после полного согласования (переход в done)"""
        pass

    def _on_reject(self, old_state=None, reason=None):
        """При отклонении документа"""
        self.message_post(
            body=Markup(_("Документ отклонён.<br/><b>Причина:</b> %s")) % (reason or _("Не указана")),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )

    def _on_return(self, new_state=None, old_state=None, reason=None):
        """При возврате документа на доработку"""
        self.message_post(
            body=Markup(_("Документ возвращён на доработку.<br/><b>Причина:</b> %s")) % (reason or _("Не указана")),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        # Очищаем согласующих
        self.state_agreement_line_ids.unlink()
        
        # Создаём activity для инициатора
        if self.create_uid:
            activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
            if activity_type:
                self.activity_schedule(
                    activity_type_id=activity_type.id,
                    user_id=self.create_uid.id,
                    summary=_("Документ возвращён на доработку"),
                    note=reason or _("Требуется внести исправления и отправить повторно на согласование."),
                )

    # ---------------------------------------------------------
    # Автопереходы
    # ---------------------------------------------------------

    def _auto_transition_to_done(self):
        """
        Автоматический переход в Завершено когда все задания в терминальном
        статусе (выполнено или отменено).
        Вызывается из assignment_line при изменении статуса задания.
        """
        self.ensure_one()
        if self.state not in ('execution', 'rework'):
            return

        if not self.assignment_line_ids:
            return

        # Терминальные статусы задания: либо выполнено, либо отменено.
        if all(line.status in ("done", "cancelled") for line in self.assignment_line_ids):
            self.message_post(
                body=_("Все задания завершены. Документ автоматически закрыт."),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
            
            # Удаляем линии согласования если есть
            if self.state_agreement_line_ids:
                self.sudo().state_agreement_line_ids.unlink()
            
            # Закрываем все активности
            activities = self.env['mail.activity'].sudo().search([
                ('res_model', '=', self._name),
                ('res_id', '=', self.id),
            ])
            if activities:
                activities.unlink()
            
            # Меняем статус на done
            self.write({"state": "done"})

    # ---------------------------------------------------------
    # Управление execution-активити (Вариант B)
    # ---------------------------------------------------------

    def _sync_execution_activities(self):
        """
        Единая точка управления "execution"-активити на документе.

        Логика: одна активити на пару (документ, юзер) — не на каждую строку
        задания. Активити закрывается только когда у юзера НЕТ больше pending
        поручений на этом документе.

        Вызывается из:
        - correspondence.assignment.line.create() — на добавление строки
        - correspondence.assignment.line.write() — на смену user_id/status
        - corr_incoming_approve_process_mixin._create_activity_for_user() при
          notif_type="execution" — на переход документа в execution

        Сохраняет non-execution активити (например, "Документ согласован"
        для create_uid) — закрывает только активити юзеров, которые когда-либо
        были назначены как исполнители на этом документе.
        """
        self.ensure_one()

        activity_type = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False)
        if not activity_type:
            return

        # Все автоматические активити на этом документе
        all_auto = self.env['mail.activity'].sudo().search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('automated', '=', True),
        ])

        # Если документ не в execution/rework — закрываем execution-активити
        # ТОЛЬКО для тех юзеров, кто был/является исполнителем (не трогаем
        # "Документ согласован" для create_uid и подобные)
        if self.state not in ('execution', 'rework'):
            executor_ids = set(self.assignment_line_ids.mapped('user_id.id'))
            executor_ids |= set(self.assignment_line_ids.mapped(
                'original_user_id.id'))
            executor_ids |= set(self.assignment_line_ids.mapped(
                'executor_history_ids.id'))
            executor_ids.discard(False)

            for activity in all_auto:
                if activity.user_id.id in executor_ids:
                    activity.action_done()
            return

        # В execution/rework: синхронизируем по pending-поручениям.
        # Терминальные статусы (done, cancelled) исключаются — у юзера с такими
        # поручениями нет работы по документу.
        pending_users = self.assignment_line_ids.filtered(
            lambda l: l.status not in ('done', 'cancelled') and l.user_id
        ).mapped('user_id')

        users_with_activity = all_auto.mapped('user_id')

        # Создать активити юзерам с pending-поручениями без активити
        for user in pending_users:
            if user not in users_with_activity:
                self.activity_schedule(
                    activity_type_id=activity_type.id,
                    user_id=user.id,
                    summary=_("Требуется выполнение задания"),
                    note=_("Пожалуйста, выполните назначенные вам поручения по документу."),
                )

        # Закрыть активити юзеров без pending-поручений
        # (юзер закончил все свои поручения, либо был переназначен/удалён)
        for activity in all_auto:
            if activity.user_id not in pending_users:
                activity.action_done()

    # ---------------------------------------------------------
    # Действия (кнопки для специфичных случаев)
    # ---------------------------------------------------------

    def action_send_to_approval(self):
        """
        Отправить на согласование (из draft или revision).
        Запускает стандартный механизм appstream_approval.
        """
        self.ensure_one()
        if self.state not in ('draft', 'revision'):
            raise UserError(_("Отправить на согласование можно только из Черновика или Доработки."))

        # Валидация
        self.method_on_start()
        
        # Закрываем activity о доработке (если есть)
        activities = self.env["mail.activity"].sudo().search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("user_id", "=", self.env.user.id),
        ])
        if activities:
            activities.action_done()

        # Запускаем согласование — переход в review
        return self.action_approve()

    def action_return_to_draft(self):
        """
        Вернуть на доработку (из review в draft).
        Сразу переводит в draft без визарда выбора статуса.
        """
        self.ensure_one()
        return self.with_context(fixed_return_state='draft').action_return()

    def action_open_rework_wizard(self):
        """Открыть визард для отправки на доработку задач"""
        self._require_any_group([
            "correspondence.group_correspondence_director",
            "correspondence.group_correspondence_admin",
            "correspondence.group_correspondence_secretary",
        ])
        self.ensure_one()
        if self.state != "done":
            raise UserError(
                _("На доработку задач можно отправить только из Завершено."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Отправка на доработку задач"),
            "res_model": "correspondence.rework.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_incoming_id": self.id,
            },
        }

    def action_send_to_rework(self, reason, tasks):
        """
        Отправить на доработку задач (из done в rework).
        Вызывается из визарда.
        """
        self._require_any_group([
            "correspondence.group_correspondence_director",
            "correspondence.group_correspondence_admin",
            "correspondence.group_correspondence_secretary",
        ])
        self.ensure_one()
        if self.state != "done":
            raise UserError(_("Доработка задач возможна только из Завершено."))

        # Создаём новые задания
        for t in tasks or []:
            self.env["correspondence.assignment.line"].create({
                "incoming_id": self.id,
                "user_id": t["user_id"],
                "resolution_id": t["resolution_id"],
                "deadline": t.get("deadline"),
                "report_needed": t.get("report_needed", False),
            })

        self.message_post(
            body=Markup(_("Отправлено на доработку задач.<br/><b>Причина:</b> %s")) % (reason or ""),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )

        # Переводим в rework
        self.write({"state": "rework"})

    # ---------------------------------------------------------
    # Закрытие документа без заданий
    # ---------------------------------------------------------

    def action_close_document(self):
        """Закрыть документ без создания заданий (review → done)"""
        self.ensure_one()
        if self.state != 'review':
            raise ValidationError(_("Закрыть можно только на этапе Ознакомление"))
        
        # Закрываем все активности на документе
        activities = self.env['mail.activity'].sudo().search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
        ])
        if activities:
            activities.unlink()
        
        # Удаляем линии согласования
        if self.state_agreement_line_ids:
            self.sudo().state_agreement_line_ids.unlink()
        
        self.sudo().write({"state": "done"})

    # ---------------------------------------------------------
    # Проверка возможности редактирования заданий
    # ---------------------------------------------------------

    def _can_edit_assignments(self):
        """Проверяет, можно ли редактировать задания на текущем этапе"""
        # Задания можно редактировать на всех этапах кроме done
        return self.state != 'done'