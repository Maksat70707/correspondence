import base64
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from datetime import datetime
from markupsafe import Markup

class OutgoingDocument(models.Model):
    _name = "corr.outgoing"
    _description = "Исходящая корреспонденция"
    _inherit = [
        "mail.thread",
        "mail.activity.mixin",
        "corr.approval.activity.dedup.mixin",
        "appstream.approval.mixin",
        "corr.outgoing.approve.process.mixin",
        "portal.signing.mixin",
    ]
    _order = "id desc"

    # ---------------------------------------------------------
    # Основные поля
    # ---------------------------------------------------------

    name = fields.Char(
        string="Номер исходящего документа",
        copy=False,
        readonly=True,
        default="---",
        tracking=True,
    )

    active = fields.Boolean(
        string="Активен",
        default=True,
        help="Снятый флаг убирает документ из списков; запись не удаляется.",
    )

    subject = fields.Char(
        string="Тема",
        required=True,
    )

    summary = fields.Text(
        string="Краткое содержание",
        required=True,
    )

    letter_body = fields.Text(
        string="Текст письма",
        help="Основной текст исходящего письма.",
    )

    type_id = fields.Many2one(
        "correspondence.outgoing.type",
        string="Тип письма",
        default=lambda self: self.env.ref(
            "correspondence.simple", raise_if_not_found=False
        ),
        required=True,
    )

    language = fields.Selection(
        [
            ('kazakh', 'Казахский'),
            ('russian', 'Русский'),
            ('english', 'Английский'),
            ('bilingual1', 'Двуязычный (Казахский и Русский)'),
            ('bilingual2', 'Двуязычный (Русский и Английский)'),
        ],
        string="Язык письма",
        required=True,
        default='russian',
        tracking=True,
    )

    correspondent_line_ids = fields.One2many(
        "correspondence.correspondent.line",
        "outgoing_id",
        string="Получатели",
    )

    incoming_id = fields.Many2one(
        "corr.incoming",
        string="Входящее письмо",
    )
    parent_outgoing_id = fields.Many2one(
        "corr.outgoing",
        string="Предыдущее исходящее",
        index=True,
    )
    follow_up_outgoing_ids = fields.One2many(
        "corr.outgoing",
        "parent_outgoing_id",
        string="Последующие исходящие",
    )
    follow_up_outgoing_count = fields.Integer(
        compute="_compute_follow_up_outgoing_count",
    )

    # Вручную добавленные согласующие (для этапа Согласование)
    additional_approver_ids = fields.Many2many(
        "res.users",
        "corr_outgoing_additional_approvers_rel",
        "outgoing_id",
        "user_id",
        string="Дополнительные согласующие",
        help="Согласующие на этапе 'Согласование' (после начальника инициатора)",
        # Только внутренние пользователи. Портальный согласующий получил бы
        # строку согласования и активность, до которых у него нет доступа в
        # бэкенде, — документ встал бы намертво на этапе Согласования.
        domain=[("share", "=", False)],
    )

    esp_signer_id = fields.Many2one(
        comodel_name="res.users",
        string="Утверждающий сотрудник",
        required=True,
    )

    available_signer_ids = fields.Many2many(
        "res.users",
        compute="_compute_available_signer_ids",
        compute_sudo=True,
        string="Доступные утверждающие",
    )

    # Исходящее письмо для подписания (только один файл)
    attachment_to_sign_ids = fields.Many2many(
        "ir.attachment",
        relation="corr_outgoing_sign_attachment_rel",
        column1="outgoing_id",
        column2="attachment_id",
        string="Исходящее письмо для подписания",
    )

    # Дополнительные документы для подписания (без номера в подписи)
    attachment_additional_sign_ids = fields.Many2many(
        "ir.attachment",
        relation="corr_outgoing_additional_sign_attachment_rel",
        column1="outgoing_id",
        column2="attachment_id",
        string="Дополнительные документы для подписания",
    )

    # Прочие документы без подписи
    attachment_extra_ids = fields.Many2many(
        "ir.attachment",
        relation="corr_outgoing_extra_attachment_rel",
        column1="outgoing_id",
        column2="attachment_id",
        string="Прочие документы без подписи",
    )

    # Данные сотрудника
    employee_id = fields.Many2one(
        comodel_name='hr.employee',
        string="Сотрудник",
    )

    employee_job_id = fields.Many2one(
        related="employee_id.job_id",
        string="Должность",
    )

    employee_identification_id = fields.Char(
        related="employee_id.identification_id",
        string="ИИН сотрудника",
        readonly=True,
    )
    employee_udo_number = fields.Char(
        related="employee_id.udo_number",
        string="Номер удостоверения личности",
        readonly=True,
    )

    employee_udo_issuing_authority = fields.Selection(
        related="employee_id.issuing_authority",
        string="Орган выдачи удостоверения",
        readonly=True,
    )

    employee_udo_issuing_date = fields.Date(
        related="employee_id.issuing_date_start",
        string="Дата выдачи удостоверения",
        readonly=True,
    )

    employee_primary_contract_id = fields.Many2one(
        related="employee_id.primary_contract_id",
        string="Основной Трудовой договор",
        readonly=True,
    )
    employee_current_contract_id = fields.Many2one(
        related="employee_id.contract_id",
        string="Текущий Трудовой договор",
        readonly=True,
    )
    employee_contract_date = fields.Date(
        related="employee_primary_contract_id.date_start",
        string="Дата заключения основного трудового договора",
        readonly=True,
    )
    employee_contract_job_id = fields.Many2one(
        related="employee_current_contract_id.job_id",
        string="Должность по текущему трудовому договору",
        readonly=True,
    )
    text_attachments_rus = fields.Char(
        string="Приложения рус",
    )
    text_attachments_kaz = fields.Char(
        string="Приложения",
    )

    # Направление на МО
    medical_checkup_date = fields.Date(
        string="Дата медицинского осмотра",
    )

    checkup_time = fields.Char(
        string="Время осмотра",
    )

    clinic = fields.Char(
        string="Клиника",
    )

    clinic_address = fields.Char(
        string="Адрес клиники",
    )

    additional_screenings = fields.Boolean(
        string="Дополнительные обследования",
    )

    reason_for_checkup_additional_screenings_rus = fields.Text(
        string="Причина дополнительных обследований рус",
    )

    reason_for_checkup_additional_screenings_kaz = fields.Text(
        string="Причина дополнительных обследований каз",
    )

    # Требование объяснительной

    explanatory_note_text_rus = fields.Text(
        string="Основания требования объяснительной рус",
        default='Во исполнение требований   п.2 ст 65 Трудового Кодекса Республики Казахстан, и на основании: _____, просим Вас в течении 2 (двух) рабочих дней со дня получения настоящего требования предоставить полное письменное объяснение по _____.',
    )

    explanatory_note_text_kaz = fields.Text(
        string="Основания требования объяснительной каз",
        default='Қазақстан Республикасының Еңбек кодексінің 65-бабы 2-тармағының талаптарын орындау мақсатында және _____ негізінде, осы талапты алған күннен бастап 2 (екі) жұмыс күні ішінде _____ қатысты толық жазбаша түсініктеме беруіңізді сұраймыз.',
    )

    # Направление на медицинское освидетельствование

    act_number = fields.Char(
        string="Номер акта",
    )

    act_date = fields.Date(
        string="Дата акта",
    )

    reason_for_medical_examination_rus = fields.Text(
        string="Причина направления на медицинское освидетельствование рус",
        default='Работник направляется на медицинское освидетельствование в связи с выявлением в ходе предсменного (послесменного) осмотра, внешних признаков, позволяющих обоснованно предполагать нахождение работника в состоянии алкогольного, наркотического или иного токсического опьянения, что может представлять угрозу жизни и здоровью самого работника и (или) иных лиц, а также безопасности производственного процесса.',
    )

    reason_for_medical_examination_kaz = fields.Text(
        string="Причина направления на медицинское освидетельствование каз",
        default='Жұмысшы кезек алдындағы (кезек соңындағы) тексеру барысында, жұмысшының алкогольдік, наркотикалық немесе басқа улану жағдайында болуы мүмкін деген негізді болжам жасауға мүмкіндік беретін сыртқы белгілердің анықталуына байланысты медициналық куәландыруға жіберіледі, бұл өз қызметкерінің және (немесе) басқа адамдардың өмірі мен денсаулығына, сондай-ақ өндірістік процестің қауіпсіздігіне қауіп төндіруі мүмкін.',
    )

    medical_worker_id = fields.Many2one(comodel_name="res.partner", string="Медицинский работник", domain="[('is_company', '=', False)]")

    medical_worker_job_id = fields.Char(
        related="medical_worker_id.function",
        string="Должность мед. сотрудника",
        readonly=True,
    )

    available_medical_worker_ids = fields.Many2many(
        "res.partner",
        compute="_compute_available_medical_worker_ids",
        compute_sudo=True,
        string="Доступные мед. работники",
    )
    medical_assessment_refusal = fields.Boolean(
        string="Отказ от медицинского освидетельствования",
    )

    # Предложение о работе

    job_offer_position_id = fields.Many2one(
        comodel_name="hr.job",
        string="Должность",
        domain="[('job_group_id.state', '=', 'active')]"
    )

    candidate_name = fields.Char(
        string="Имя кандидата",
    )

    candidate_email = fields.Char(
        string="Email кандидата",
    )

    candidate_phone = fields.Char(
        string="Телефон кандидата",
    )

    time_type = fields.Selection([
        ('full_time', 'Полная занятость'),
        ('part_time', 'По совместительству'),
    ], string="Тип занятости")

    part_time_hours = fields.Integer(
        string="Часы по совместительству",
    )

    working_hours = fields.Char(
        string="Рабочие часы",
    )

    work_start_date = fields.Date(
        string="Дата начала работы",
    )

    contract_period = fields.Char(
        string="Срок договора",
    )

    trial_period = fields.Char(
        string="Испытательный срок",
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        default=lambda self: self.env.company.currency_id,
        readonly=True
    )
    salary = fields.Monetary(
        related="job_offer_position_id.wage",
        string="Зарплата",
        currency_field="currency_id",
    )
    custom_salary = fields.Monetary(
        string="Зарплата",
        currency_field="currency_id",
    )
    custom_salary_bool = fields.Boolean(
        string="Произвольная зарплата",
    )
    holiday_days = fields.Integer(
        string="Количество дней отпуска",
    )

    additional_info_1 = fields.Text(
        string="Дополнительная информация 1",
    )

    additional_info_2 = fields.Text(
        string="Дополнительная информация 2",
    )

    offer_end_date = fields.Date(
        string="Срок ответа на предложение",
    )

    contact_phone = fields.Char(
        string="Контактный телефон",
    )

    contact_email = fields.Char(
        string="Контактный email",
    )

    # Справка с места работы

    current_salary = fields.Monetary(
        related="employee_current_contract_id.total_wage",
        string="Текущая зарплата",
        readonly=True,
    )
    average_salary = fields.Integer(
        string="Средняя зарплата по должности",
    )
    work_schedule_needed = fields.Boolean(
        string="Указать график работы",
    )
    salary_needed = fields.Boolean(
        string="Указать зарплату",
    )
    vacation_start_date = fields.Date(
        string="Дата начала отпуска",
    )
    vacation_end_date = fields.Date(
        string="Дата окончания отпуска",
    )
    state_agreement_line_ids = fields.One2many(tracking=False)
    state_agreement_history_line_ids = fields.One2many(tracking=False)

    # Шаблонное письмо для печати

    where = fields.Char(
        string="Куда",
    )

    whom = fields.Char(
        string="Кому",
    )

    mail_subject_rus = fields.Char(
        string="Тема письма рус",
    )

    mail_subject_kaz = fields.Char(
        string="Тема письма каз",
    )

    summary_rus = fields.Text(
        string="Тело письма рус",
    )
    summary_kaz = fields.Text(
        string="Тело письма каз",
    )

    # ---------------------------------------------------------
    # Computed поля
    # ---------------------------------------------------------

    user_esp = fields.Boolean(
        string="Требуется ЭЦП",
        compute="_compute_user_esp",
    )

    is_initiator = fields.Boolean(
        string="Является инициатором",
        compute="_compute_is_initiator",
    )

    acts_for_initiator = fields.Boolean(
        string="Действует за инициатора",
        compute="_compute_acts_for_initiator",
    )

    is_current_approver = fields.Boolean(
        string="Текущий согласующий",
        compute="_compute_is_current_approver",
    )

    can_cancel = fields.Boolean(
        string="Может отменить",
        compute="_compute_can_cancel",
    )

    is_employee_signer = fields.Boolean(
        string="Сотрудник-подписант",
        compute="_compute_is_employee_signer",
    )

    # "Письмо моего подразделения" — свойство не письма, а того, кто смотрит,
    # поэтому поле вычисляемое и не хранится. Метод search разворачивает его в
    # create_uid IN (...), благодаря чему одним и тем же признаком пользуются и
    # фильтр в поисковой панели, и, при необходимости, правило доступа.
    # Само меню строит домен напрямую через _my_department_user_ids().
    # Текущие согласующие для колонки в списке. Many2one на res.users, а не на
    # строки согласования: тег тогда показывает имя пользователя напрямую, без
    # оглядки на _rec_name строки.
    current_approver_ids = fields.Many2many(
        "res.users",
        string="Текущие согласующие",
        compute="_compute_current_approver_ids",
    )

    is_my_department = fields.Boolean(
        string="Письмо моего подразделения",
        compute="_compute_is_my_department",
        search="_search_is_my_department",
    )

    is_vahta = fields.Boolean(
        string="Вахтовик",
        compute="_compute_is_vahta",
    )
    has_report = fields.Boolean(
        compute='_compute_has_report',
        string="Доступен ли отчёт-шаблон для текущего типа",
    )
    show_simple = fields.Boolean(
        compute="_compute_show_simple",
    )
    show_explanation = fields.Boolean(
        compute="_compute_show_explanation",
    )
    show_medical_examination = fields.Boolean(
        compute="_compute_show_medical_examination",
    )
    show_medical_checkup = fields.Boolean(
        compute="_compute_show_medical_checkup",
    )
    show_change_conditions = fields.Boolean(
        compute="_compute_show_change_conditions",
    )
    show_job_offer = fields.Boolean(
        compute="_compute_show_job_offer",
    )
    show_reference = fields.Boolean(
        compute="_compute_show_reference",
    )
    show_guarantee = fields.Boolean(
        compute="_compute_show_guarantee",
    )
    show_rus_fields = fields.Boolean(
        compute="_compute_show_rus_fields",
    )
    show_kaz_fields = fields.Boolean(
        compute="_compute_show_kaz_fields",
    )
    show_main_attachment = fields.Boolean(
        compute="_compute_show_main_attachment",
    )
    show_employee_fields = fields.Boolean(
        compute="_compute_show_employee_fields",
    )
    show_general_template  = fields.Boolean(
        compute="_compute_show_general_template",
    )
    @api.depends("follow_up_outgoing_ids")
    def _compute_follow_up_outgoing_count(self):
        for rec in self:
            rec.follow_up_outgoing_count = len(rec.follow_up_outgoing_ids)
    @api.depends('type_id')
    def _compute_show_simple(self):
        """Проверяет, нужно ли показывать доп поля письма"""
        simple_type = self.env.ref('correspondence.simple', raise_if_not_found=False)
        for rec in self:
            rec.show_simple = (rec.type_id == simple_type)
    @api.depends('type_id')
    def _compute_show_explanation(self):
        """Проверяет, нужно ли показывать доп поля письма"""
        explanation_type = self.env.ref('correspondence.explanation', raise_if_not_found=False)
        for rec in self:
            rec.show_explanation = (rec.type_id == explanation_type)
    @api.depends('type_id')
    def _compute_show_medical_examination(self):
        """Проверяет, нужно ли показывать доп поля письма"""
        medical_examination_type = self.env.ref('correspondence.medical_examination', raise_if_not_found=False)
        for rec in self:
            rec.show_medical_examination = (rec.type_id == medical_examination_type)
    @api.depends('type_id')
    def _compute_show_medical_checkup(self):
        """Проверяет, нужно ли показывать доп поля письма"""
        medical_checkup_type = self.env.ref('correspondence.medical_checkup', raise_if_not_found=False)
        for rec in self:
            rec.show_medical_checkup = (rec.type_id == medical_checkup_type)
    @api.depends('type_id')
    def _compute_show_change_conditions(self):
        """Проверяет, нужно ли показывать доп поля письма"""
        change_conditions_type = self.env.ref('correspondence.change_conditions', raise_if_not_found=False)
        for rec in self:
            rec.show_change_conditions = (rec.type_id == change_conditions_type)
    @api.depends('type_id')
    def _compute_show_job_offer(self):
        """Проверяет, нужно ли показывать доп поля письма"""
        job_offer_type = self.env.ref('correspondence.job_offer', raise_if_not_found=False)
        for rec in self:
            rec.show_job_offer = (rec.type_id == job_offer_type)
    @api.depends('type_id')
    def _compute_show_reference(self):
        """Проверяет, нужно ли показывать доп поля письма"""
        reference_type = self.env.ref('correspondence.reference', raise_if_not_found=False)
        for rec in self:
            rec.show_reference = (rec.type_id == reference_type)
    @api.depends('type_id')
    def _compute_show_guarantee(self):
        """Проверяет, нужно ли показывать доп поля письма"""
        guarantee_type = self.env.ref('correspondence.guarantee', raise_if_not_found=False)
        for rec in self:
            rec.show_guarantee = (rec.type_id == guarantee_type)
    @api.depends('language')
    def _compute_show_rus_fields(self):
        """Проверяет, нужно ли показывать русские поля"""
        for rec in self:
            rec.show_rus_fields = rec.language in ('russian', 'bilingual2')
    @api.depends('language')
    def _compute_show_kaz_fields(self):
        """Проверяет, нужно ли показывать казахские поля"""
        for rec in self:
            rec.show_kaz_fields = rec.language in ('kazakh', 'bilingual1')
    @api.depends('current_salary')
    def _compute_is_vahta(self):
        """Проверяет, является ли сотрудник вахтовиком"""
        for rec in self:
            rec.is_vahta = rec.current_salary < 25000
    @api.depends('type_id', 'language')
    def _compute_has_report(self):
        for rec in self:
            rec.has_report = bool(rec._get_report_by_type('pdf'))
    @api.depends('type_id', 'language')
    def _compute_show_general_template(self):
        """Проверяет, нужно ли показывать шаблонное письмо для печати"""
        general_template_type = self.env.ref('correspondence.general_template', raise_if_not_found=False)
        for rec in self:
            rec.show_general_template = (rec.type_id == general_template_type)
    @api.depends('show_simple', 'show_guarantee', 'show_change_conditions')
    def _compute_show_main_attachment(self):
        """Проверяет, нужно ли показывать поле с основным вложением для подписания"""
        for rec in self:
            rec.show_main_attachment = (rec.show_simple or
                                        rec.show_guarantee or
                                        rec.show_change_conditions)
    @api.depends('show_explanation', 'show_medical_examination', 'show_medical_checkup', 'show_reference')
    def _compute_show_employee_fields(self):
        """Проверяет, нужно ли показывать поля с данными сотрудника"""
        for rec in self:
            rec.show_employee_fields = (rec.show_explanation or
                                        rec.show_medical_examination or
                                        rec.show_medical_checkup or
                                        rec.show_reference)


    @api.depends("state_agreement_line_ids", "state_agreement_line_ids.status", "state_agreement_line_ids.need_esp")
    def _compute_user_esp(self):
        """Проверяет, нужна ли ЭЦП текущему пользователю"""
        for rec in self:
            rec.user_esp = False
            for line in rec.state_agreement_line_ids:
                if (rec.env.user == line.user_id
                    and line.status == "in_progress"
                        and line.need_esp):
                    rec.user_esp = True
                    break

    @api.depends('type_id', 'type_id.esp_signer_ids')
    def _compute_available_signer_ids(self):
        # share = True — портальные и публичные пользователи. Утверждающий
        # подписывает ЭЦП в бэкенде, портальный туда не попадёт.
        all_users = self.env['res.users'].sudo().search([('share', '=', False)])
        for rec in self:
            if rec.type_id and rec.type_id.esp_signer_ids:
                # Тип мог быть настроен до появления фильтра — чистим и здесь.
                rec.available_signer_ids = rec.type_id.esp_signer_ids.filtered(
                    lambda u: not u.share
                )
            else:
                rec.available_signer_ids = all_users

    @api.constrains('additional_approver_ids', 'esp_signer_id')
    def _check_approvers_are_internal(self):
        """
        Домен фильтрует только выпадающий список. Запись могла прийти из
        импорта, через API или остаться с тех пор, когда фильтра не было, —
        поэтому проверяем ещё и на уровне ORM.
        """
        for rec in self:
            share_users = rec.additional_approver_ids.filtered(lambda u: u.share)
            if rec.esp_signer_id.share:
                share_users |= rec.esp_signer_id
            if share_users:
                raise ValidationError(
                    "Согласующим или утверждающим нельзя назначить портального "
                    "пользователя — у него нет доступа к согласованию в системе.\n"
                    "Проверьте: %s" % ", ".join(share_users.mapped("name"))
                )

    @api.onchange('type_id')
    def _onchange_type_id_clear_signer(self):
        """Сбрасываем выбранного утверждающего, если он не в списке для нового типа."""
        if self.esp_signer_id and self.type_id and self.type_id.esp_signer_ids:
            if self.esp_signer_id not in self.type_id.esp_signer_ids:
                self.esp_signer_id = False

    @api.depends_context("uid")
    @api.depends("create_uid")
    def _compute_is_initiator(self):
        """Проверяет, является ли текущий пользователь инициатором"""
        for rec in self:
            rec.is_initiator = rec.create_uid == self.env.user

    @api.depends("state_agreement_line_ids.status", "state_agreement_line_ids.user_id")
    def _compute_current_approver_ids(self):
        # sudo(): в списке "Моё подразделение" чужие письма открыты на чтение,
        # но строки согласования могут быть закрыты своим правилом доступа —
        # без sudo колонка у чужих писем молча опустела бы.
        for rec in self:
            rec.current_approver_ids = rec.sudo().state_agreement_line_ids.filtered(
                lambda l: l.status == "in_progress"
            ).user_id

    @api.model
    def _my_department_user_ids(self):
        """
        Пользователи моего подразделения, включая меня самого.

        sudo(): рядовой сотрудник не читает hr.employee целиком, ему доступна
        только hr.employee.public, а member_ids ведёт на hr.employee.
        Без карточки сотрудника или без подразделения остаёмся при себе —
        отбор просто сузится до своих писем.
        """
        employee = self.env.user.employee_id
        department = employee.department_id if employee else False
        if not department:
            return self.env.user.ids
        users = department.sudo().member_ids.user_id
        return (users | self.env.user).ids

    @api.depends_context("uid")
    @api.depends("create_uid")
    def _compute_is_my_department(self):
        department_user_ids = self._my_department_user_ids()
        for rec in self:
            rec.is_my_department = rec.create_uid.id in department_user_ids

    def _search_is_my_department(self, operator, value):
        if operator not in ("=", "!="):
            raise UserError(_("Неподдерживаемый оператор для этого фильтра."))
        positive = (operator == "=") == bool(value)
        department_user_ids = self._my_department_user_ids()
        return [("create_uid", "in" if positive else "not in", department_user_ids)]

    @api.depends_context("uid")
    @api.depends("create_uid")
    def _compute_acts_for_initiator(self):
        """
        Инициатор или его действующий заместитель.

        Нужно там, где право принадлежит именно автору документа: отозвать
        своё письмо, ознакомиться с результатом. Если инициатор в отпуске,
        эти действия должен мочь сделать тот, кто его замещает.
        """
        Line = self.env["appstream.approval.agreement.line"]
        for rec in self:
            if rec.create_uid == self.env.user:
                rec.acts_for_initiator = True
            else:
                rec.acts_for_initiator = (
                    Line._corr_find_delegate(rec.create_uid) == self.env.user
                )

    @api.depends_context("uid")
    @api.depends("state_agreement_line_ids", "state_agreement_line_ids.status",
                 "state_agreement_line_ids.user_id")
    def _compute_is_current_approver(self):
        """
        У текущего пользователя есть строка согласования "В процессе".

        После передачи строки заместителю (см.
        approval_agreement_line_delegation) владельцем становится он, поэтому
        отдельной проверки делегации здесь не нужно.
        """
        for rec in self:
            rec.is_current_approver = bool(rec.state_agreement_line_ids.filtered(
                lambda l: l.user_id == self.env.user and l.status == 'in_progress'
            ))

    @api.depends_context("uid")
    @api.depends("state", "acts_for_initiator")
    def _compute_can_cancel(self):
        """
        Кто может отозвать документ.

        Намеренно НЕ is_current_approver: на Согласовании и Утверждении
        текущий согласующий — это начальник или утверждающий, и отзывать
        чужое письмо они не должны. Для них есть "Отклонить" и "Вернуть на
        доработку". Отзыв остаётся правом автора и того, кто его замещает.
        """
        for rec in self:
            rec.can_cancel = rec.acts_for_initiator and rec.state in ('draft', 'under_approval', 'approval', 'approval_medical_examination')

    @api.depends_context("uid")
    @api.depends("state", "employee_id", "state_agreement_line_ids")
    def _compute_is_employee_signer(self):
        """
        True если текущий пользователь — employee_id этого документа
        и является текущим подписантом (in_progress).
        Используется для кнопки 'Отказаться от мед. освидетельствования'.
        """
        for rec in self:
            if (
                rec.state == 'approval_medical_examination'
                and rec.employee_id
                and rec.employee_id.user_id == self.env.user
            ):
                has_active_line = rec.state_agreement_line_ids.filtered(
                    lambda l: l.user_id == self.env.user and l.status == 'in_progress'
                )
                rec.is_employee_signer = bool(has_active_line)
            else:
                rec.is_employee_signer = False

    @api.depends('type_id')
    def _compute_available_medical_worker_ids(self):
        """
        Возвращает партнёров, привязанных к портальным пользователям.
        Используется только во view как источник domain для medical_worker_id.
        """
        portal_group = self.env.ref('base.group_portal', raise_if_not_found=False)
        if portal_group:
            portal_users = self.env['res.users'].sudo().search([
                ('group_ids', 'in', portal_group.id),
                ('active', '=', True),
            ])
            partners = portal_users.partner_id.filtered(lambda p: not p.is_company)
        else:
            partners = self.env['res.partner']
        for rec in self:
            rec.available_medical_worker_ids = partners
            


    # Статусы, из которых документ можно убрать в архив. Остальные — это
    # документ в работе: заархивированный, он исчезнет из списков согласующих,
    # но останется висеть в их активностях, и маршрут встанет.
    ARCHIVABLE_STATES = ('draft', 'done', 'rejected', 'canceled')

    @api.constrains('active')
    def _check_archivable_state(self):
        for rec in self:
            if not rec.active and rec.state not in self.ARCHIVABLE_STATES:
                raise ValidationError(_(
                    "Документ «%(name)s» находится в работе и не может быть "
                    "заархивирован. Сначала завершите или отмените его."
                ) % {"name": rec.display_name})

    @api.constrains('attachment_to_sign_ids')
    def _check_single_attachment_to_sign(self):
        """Проверяет что прикреплен только один файл для подписания"""
        for rec in self:
            if len(rec.attachment_to_sign_ids) > 1:
                raise ValidationError(_("Можно прикрепить только один файл для подписания в поле 'Исходящее письмо для подписания'."))

    # ---------------------------------------------------------
    # ORM методы
    # ---------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)

        for record in records:
            record._fix_attachment_ownership()

        return records

    def write(self, vals):
        res = super().write(vals)

        # Номер здесь НЕ присваивается. Единственный триггер — ЭЦП-подпись
        # на Утверждении (см. _assign_document_number и вызов из
        # corr_outgoing_approve_process_mixin.add_to_history).
        #
        # Раньше здесь стоял `if vals.get('state') == 'processing'`. Это давало
        # два бага: (1) номера ещё нет в момент вшивания штампа в файл;
        # (2) любой транзитный write состояния 'processing' — а _action_approve
        # выставляет state ДО выполнения before/after_script — сжигал номер
        # раньше времени.

        if 'attachment_to_sign_ids' in vals or 'attachment_additional_sign_ids' in vals or 'attachment_extra_ids' in vals:
            self._fix_attachment_ownership()
        return res

    def _assign_document_number(self):
        """
        Присваивает номер из последовательности, если он ещё не присвоен.

        Вызывается один раз — при первой ЭЦП-подписи (Утверждающий сотрудник),
        до брендирования файла.

        Идемпотентен: второй и третий подписант в approval_medical_examination
        номер не меняют и последовательность не расходуют.

        sudo(): подписант на этапе Утверждения — не обязательно секретарь,
        а поле name объявлено readonly.
        """
        Sequence = self.env["ir.sequence"].sudo()
        for rec in self:
            if not rec.name or rec.name == "---":
                rec.sudo().name = (
                    Sequence.next_by_code("correspondence.outgoing") or "---"
                )
        return self

    def _fix_attachment_ownership(self):
        """Привязывает вложения к записи для корректной работы прав доступа"""
        attachment_fields = ['attachment_to_sign_ids', 'attachment_additional_sign_ids', 'attachment_extra_ids']

        for record in self:
            for field in attachment_fields:
                attachments = getattr(record, field)
                if attachments:
                    attachments.write({
                        'res_model': record._name,
                        'res_id': record.id
                    })
        return self

    def _compute_display_name(self):
        for record in self:
            is_placeholder = record.name == "---"
            if record.subject and not is_placeholder:
                record.display_name = f"{record.name} - {record.subject}"
            elif record.subject:
                record.display_name = record.subject  # без префикса-плейсхолдера
            else:
                record.display_name = record.name or ""

    # ---------------------------------------------------------
    # Хуки для appstream_approval
    # ---------------------------------------------------------

    def method_on_start(self):
        """Валидация перед запуском согласования (из draft)"""
        allowed_mimetypes = [
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # DOCX
            'application/pdf',  # PDF
        ]
        for rec in self:
            if not rec.subject:
                raise ValidationError(_("Укажите тему документа"))
            if not rec.summary:
                raise ValidationError(_("Укажите краткое содержание документа"))
            # Исходящее письмо для подписания обязательно только для обычного письма
            simple_type = self.env.ref('correspondence.simple', raise_if_not_found=False)
            if rec.type_id == simple_type and not rec.attachment_to_sign_ids:
                raise ValidationError(_("Прикрепите исходящее письмо для подписания"))

            for attachment in rec.attachment_to_sign_ids:
                if attachment.mimetype not in allowed_mimetypes:
                    raise ValidationError(
                        _("Формат вложения для подписания может быть только .pdf или .docx!")
                    )

    def method_in_middle(self):
        """Валидация перед переходом между промежуточными статусами"""
        # Проверяем формат вложений перед подписанием ЭЦП (переход в approval)
        if self.state == 'under_approval':
            allowed_mimetypes = [
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # DOCX
                'application/pdf',  # PDF
            ]
            for attachment in self.attachment_to_sign_ids:
                if attachment.mimetype not in allowed_mimetypes:
                    raise ValidationError(
                        _("Формат вложения для подписания может быть только .pdf или .docx!")
                    )

    def method_on_approved(self):
        """Вызывается после полного согласования (переход в done)"""
        pass

    
    
    def _selection_label(self, fname, lang):
        """Возвращает переведённый лейбл значения Selection-поля."""
        field = self.with_context(lang=lang)._fields[fname]
        selection = dict(field._description_selection(self.env))
        return selection.get(self[fname], '')

    def _on_reject(self, old_state=None, reason=None):
        """При отклонении документа согласующими"""
        current_coordinator = self.get_current_coordinator() if hasattr(self, "get_current_coordinator") else None

        if current_coordinator and hasattr(self, "add_to_history"):
            current_coordinator.commentary = reason
            current_coordinator.agreement_date = datetime.now()
            state_description = {
                state_desc[0]: state_desc[1]
                for state_desc in self._fields['state']._description_selection(self.env)
            }
            self.add_to_history(current_coordinator, state_description.get(old_state), status="Отклонено")

        # Очищаем согласующих (с sudo для обхода прав)
        self.sudo().state_agreement_line_ids.unlink()

        # Сообщение в чаттер здесь НЕ постим: его публикует
        # appstream_approval/wizard/approval_reject_wizard.py:action_reject
        # до вызова record.action_reject() -> _on_reject.
        # Верно и для v3, и для v4.

    def _action_return(self, state, reason):
        """
        Возврат на доработку, доступный ещё и инициатору.

        Фреймворк первым делом делает self.filtered("button_approve_enabled"),
        то есть вернуть документ может только текущий согласующий. Инициатору
        кнопку показать мало — запись отфильтровалась бы и нажатие молча
        ничего не сделало.

        Записи согласующих отдаём фреймворку как есть, свои разбираем сами,
        повторяя его же последовательность действий.
        """
        action = super()._action_return(state, reason)

        by_approver = self.filtered("button_approve_enabled")
        by_initiator = (self - by_approver).filtered(
            lambda r: r.acts_for_initiator
            and r.state in ('under_approval', 'approval', 'approval_medical_examination')
        )
        for record in by_initiator:
            old_state = record.state
            record._remove_approval_activity(action="return", reason=reason)
            record.with_context(reject_reason=reason).write({"state": state})
            record.env.invalidate_all()
            record._on_return(new_state=state, old_state=old_state, reason=reason)
            record._schedule_approval_activity()

        return action

    def _on_return(self, new_state=None, old_state=None, reason=None):
        """При возврате документа на доработку"""
        current_coordinator = self.get_current_coordinator() if hasattr(self, "get_current_coordinator") else None

        if current_coordinator and hasattr(self, "add_to_history"):
            current_coordinator.commentary = reason
            current_coordinator.agreement_date = datetime.now()
            state_description = {
                state_desc[0]: state_desc[1]
                for state_desc in self._fields['state']._description_selection(self.env)
            }
            self.add_to_history(
                current_coordinator,
                state_description.get(old_state),
                "Возвращено на '" + state_description.get(new_state, 'Черновик') + "'"
            )

        # Очищаем согласующих
        self.sudo().state_agreement_line_ids.unlink()

        # Сбрасываем счётчик ЭЦП подписей при возврате на доработку
        if new_state == 'draft':
            self.sudo().number_of_esp_signs = 0

        # Сообщение в чаттер здесь НЕ постим: его публикует
        # appstream_approval/wizard/approval_return_wizard.py:action_return
        # до вызова _action_return -> _on_return. Верно и для v3, и для v4.

        # Создаём activity для инициатора (с sudo для обхода прав)
        if self.create_uid:
            activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
            if activity_type:
                self.sudo().activity_schedule(
                    activity_type_id=activity_type.id,
                    user_id=self.create_uid.id,
                    summary=Markup(_("Документ возвращён на доработку")),
                    note=reason or Markup(_("Требуется внести исправления и отправить повторно на согласование.")),
                )

    # ---------------------------------------------------------
    # Действия (кнопки)
    # ---------------------------------------------------------
    @api.model
    def action_view_outgoing(self, scope="my"):
        """
        Список исходящих для одного из пунктов меню.

        Домены собираются здесь, а не в XML, потому что домен
        ir.actions.act_window вычисляется в урезанном контексте: там есть uid,
        но нет объекта user, а "моё подразделение" одним uid не задать. Заодно
        все области видимости лежат в одном месте — добавить новую значит
        дописать ветку, а не заводить очередное действие с доменом в XML.
        """
        action = self.env["ir.actions.actions"]._for_xml_id(
            "correspondence.action_corr_outgoing"
        )
        uid = self.env.uid

        if scope == "my":
            action["name"] = _("Мои письма")
            action["domain"] = [("create_uid", "=", uid)]

        elif scope == "my_department":
            action["name"] = _("Моё подразделение")
            action["domain"] = [
                ("create_uid", "in", self._my_department_user_ids())
            ]
            # Чужие письма отдела открываются только на чтение, создавать
            # отсюда нечего — новое письмо заводится в "Моих письмах".
            action["context"] = {"create": False}

        elif scope == "assigned":
            action["name"] = _("Поручено")
            action["domain"] = [
                "|", "|",
                ("additional_approver_ids", "in", uid),
                ("state_agreement_line_ids.user_id", "=", uid),
                ("state_agreement_history_line_ids.user_id", "=", uid),
            ]
            action["context"] = {"create": False}

        elif scope == "all":
            action["name"] = _("Все исходящие")
            action["domain"] = []

        else:
            raise UserError(_("Неизвестная область видимости: %s") % scope)

        return action

    def action_view_follow_up_outgoing(self):
        """
        Открывает список follow-up исходящих этого письма.
        В context передаём:
          - default_parent_outgoing_id = self.id (связать новый follow-up с этим)
          - default_incoming_id = self.incoming_id.id (наследовать источник)
        """
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": _("Последующие исходящие"),
            "res_model": "corr.outgoing",
            "domain": [("parent_outgoing_id", "=", self.id)],
            "context": {
                "default_parent_outgoing_id": self.id,
                "default_incoming_id": self.incoming_id.id,
            },
        }
        if self.follow_up_outgoing_count == 1:
            action["view_mode"] = "form"
            action["res_id"] = self.follow_up_outgoing_ids.id
        else:
            action["view_mode"] = "list,form"
        return action
    
    def action_create_follow_up_outgoing(self):
        """
        Открывает новую форму исходящего как follow-up к текущему.
        Заполняет parent_outgoing_id (текущее) и наследует incoming_id.
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Новое исходящее (follow-up)"),
            "res_model": "corr.outgoing",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_parent_outgoing_id": self.id,
                "default_incoming_id": self.incoming_id.id,
            },
        }

    def action_send_to_approval(self):
        """Отправить на согласование (из draft)"""
        self.ensure_one()

        if self.state != 'draft':
            raise UserError(_("Отправить на согласование можно только из Черновика."))

        # Валидация
        self.method_on_start()

        # Закрываем все открытые activity на этом документе
        self.sudo().activity_ids.unlink() # self.sudo().activity_ids.action_feedback(feedback=_("Документ отправлен на согласование"))

        # Определяем нужен ли этап under_approval
        # Пропускаем если: начальник = Директор И нет дополнительных согласующих
        skip_under_approval = False
        initiator = self.create_uid

        if initiator and initiator.employee_id:
            manager = initiator.employee_id.parent_id
            if manager and manager.user_id:
                is_director = manager.user_id.has_group('correspondence.group_correspondence_director')
                if is_director or manager.user_id.id == 2:
                    # Начальник - Директор или OdooBot
                    has_additional = hasattr(self, 'additional_approver_ids') and self.additional_approver_ids
                    if not has_additional:
                        skip_under_approval = True

        if skip_under_approval:
            # Сразу в approval — определяем куда в зависимости от типа
            if self._is_medical_examination_type():
                result = self.get_agreement_lines('approval_medical_examination')
            else:
                result = self.get_agreement_lines('approval')
            self._schedule_approval_activity()

            return result
        else:
            # Обычный путь через under_approval
            return self.action_approve()

    def action_cancel_document(self):
        """Отменить документ (инициатором из draft, under_approval, approval/approval_medical_examination)"""
        self.ensure_one()

        if self.state not in ('draft', 'under_approval', 'approval', 'approval_medical_examination'):
            raise UserError(_("Отменить можно только из Черновика, Согласования или Утверждения."))

        if not self.acts_for_initiator:
            raise UserError(_("Отменить документ может только инициатор или его заместитель."))

        # Очищаем согласующих и activity (sudo: unlink=0 у секретаря)
        self.sudo().state_agreement_line_ids.unlink()
        self._remove_approval_activity()

        self.write({"state": "canceled"})

        self.message_post(
            body=_("Документ отменён инициатором."),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )

    def action_acknowledge(self):
        """
        Ознакомиться и закрыть (из review).

        Строка на Ознакомлении создаётся на инициатора, но могла уйти
        заместителю по делегации — тогда ознакомиться должен он. Поэтому
        пускаем и того, у кого строка "В процессе".
        """
        self.ensure_one()
        if self.state != 'review':
            raise UserError(_("Ознакомиться можно только на этапе Ознакомления."))

        if not (self.acts_for_initiator or self.is_current_approver):
            raise UserError(_(
                "Ознакомиться с документом может только инициатор, его "
                "заместитель или текущий согласующий."
            ))

        # Стандартное согласование (переход в done)
        return self.action_approve()
    
    def _get_medical_refusal_line(self):
        """Строка согласования сотрудника, ожидающая его действия."""
        self.ensure_one()
        if not self.is_employee_signer:
            raise ValidationError(_(
                "Отказаться может только сотрудник, направляемый на "
                "освидетельствование."
            ))
        approver = self.state_agreement_line_ids.filtered(
            lambda l: l.user_id == self.env.user and l.status == 'in_progress'
        )
        if not approver:
            raise ValidationError(_("Вы не являетесь текущим подписантом"))
        return approver

    def action_mark_medical_refusal(self):
        """
        Отмечает намерение отказаться от мед. освидетельствования.

        Сам отказ не совершается: статус не двигается, строка согласования
        остаётся in_progress. Юридическую силу отказ получает только после
        подписания ЭЦП — штатным виджетом sign_esp, надпись на котором
        меняется на "Подписать отказ с ЭЦП".

        Отдельный виджет для этого не нужен: строка сотрудника создаётся с
        need_esp=True, поэтому sign_esp ему и так доступен. Не хватало только
        способа сообщить серверу, что подпись означает отказ, — им и служит
        этот флаг.
        """
        approver = self._get_medical_refusal_line()
        self.sudo().medical_assessment_refusal = True
        approver.sudo().commentary = 'Отказ от прохождения мед. освидетельствования'

    def action_cancel_medical_refusal(self):
        """Снимает отметку об отказе, пока он ещё не подписан."""
        approver = self._get_medical_refusal_line()
        self.sudo().medical_assessment_refusal = False
        approver.sudo().commentary = False


    def get_report_values(self) -> dict:
        # Берём из истории тех кто подписал с ЭЦП
        signed_lines = self.state_agreement_history_line_ids.filtered(lambda l: l.signed and l.qr)
        last_signed = signed_lines[-1] if signed_lines else None

        values = {
            'name': self.name,
            'date': self.create_date.strftime('%d.%m.%Y') if self.create_date else '',
            'summary': self.summary or '',
            'signed_lines': signed_lines,
            'images__insert': [{"img": record.qr, "w": 35, "h": 35} for record in signed_lines],
        }

        # Добавляем данные последнего подписанта
        if last_signed:
            employee = last_signed.user_id.employee_id
            job = employee.job_id if employee else None
            values.update({
                'approver': (last_signed.user_id.name or '') + _(" подписал(а)"),
                'qr__insert': [{"img": last_signed.qr, "w": 20, "h": 20}],
                'signed': True,
                'signer_job_rus': job.with_context(lang='ru_RU').name if job else '',
                'signer_job_kaz': job.with_context(lang='kk_KZ').name if job else '',
                'signer': employee.name if employee else '',
                'sign_date': last_signed.agreement_date.strftime('%d.%m.%Y') if last_signed.agreement_date else '',
            })
        else:
            values.update({
                'approver': '',
                'qr__insert': [],
                'signed': False,
                'signer_job_rus': '',
                'signer_job_kaz': '',
                'signer': '',
                'sign_date': '',
            })

        return values

    def prepare_general_template_report_values(self):
        """Подготовка значений для отчёта «Шаблонное письмо».

        Бланк заполняется целиком из полей формы. Подписывает только
        Утверждающий сотрудник (esp_signer).
        """
        self.ensure_one()
        values = self.get_report_values()

        signed_lines = self.state_agreement_history_line_ids.filtered(lambda l: l.signed)
        signer_signed = signed_lines.filtered(lambda l: l.user_id == self.esp_signer_id)[:1]

        ordered_ids = [signer_signed.id] if signer_signed else []
        ordered_signed = self.env['appstream.approval.agreement.history.line'].browse(ordered_ids)

        signer_qr_insert = []
        signer_sign_date = ''
        signer_name = ''
        signer_job_rus = ''
        signer_job_kaz = ''
        if signer_signed:
            signer_employee = signer_signed.user_id.employee_id
            signer_job = signer_employee.job_id if signer_employee else None
            signer_name = signer_employee.name if signer_employee else ''
            signer_job_rus = signer_job.with_context(lang='ru_RU').name if signer_job else ''
            signer_job_kaz = signer_job.with_context(lang='kk_KZ').name if signer_job else ''
            signer_sign_date = signer_signed.agreement_date.strftime('%d.%m.%Y') if signer_signed.agreement_date else ''
            if signer_signed.qr:
                signer_qr_insert = [{"img": signer_signed.qr, "w": 20, "h": 20}]

        # Дата в шапке бланка — дата регистрации письма, то есть дата подписи
        # утверждающего: номер присваивается в тот же момент. Пока письмо не
        # подписано, показываем дату создания.
        letter_date = signer_sign_date or (
            self.create_date.strftime('%d.%m.%Y') if self.create_date else ''
        )

        values.update({
            'where': self.where or '',
            'whom': self.whom or '',
            'mail_subject_kaz': self.mail_subject_kaz or '',
            'mail_subject_rus': self.mail_subject_rus or '',
            'summary_kaz': self.summary_kaz or '',
            'summary_rus': self.summary_rus or '',
            'date': letter_date,
            # В бланке {{create_uid}} — строка "исполнитель", нужно имя, а не recordset
            'create_uid': self.create_uid.name or '',
            'contact_phone': self.contact_phone or '',
            'contact_email': self.contact_email or '',

            'signer': signer_name,
            'signer_job_rus': signer_job_rus,
            'signer_job_kaz': signer_job_kaz,
            'sign_date': signer_sign_date,
            'qr__insert': signer_qr_insert,

            'state_agreement_lines': ordered_signed,
            'images__insert': [
                {"img": record.qr, "w": 35, "h": 35}
                for record in ordered_signed if record.qr
            ],
        })
        return values

    def prepare_medical_checkup_report_values(self):
        """Подготавливает данные для DOCX отчёта Направление на медицинское осмотр"""
        self.ensure_one()
        values = self.get_report_values()

        # Данные сотрудника
        employee = self.employee_id
        employee_job = employee.job_id if employee else None

        # Подписи: esp_signer → employee (в порядке подписания)
        signed_lines = self.state_agreement_history_line_ids.filtered(
            lambda l: l.signed
        )

        # Собираем в правильном порядке: esp_signer (seq 1) → employee (seq 2)
        signer_signed = signed_lines.filtered(
            lambda l: l.user_id == self.esp_signer_id
        )[:1]
        emp_signed = signed_lines.filtered(
            lambda l: self.employee_id
            and self.employee_id.user_id
            and l.user_id == self.employee_id.user_id
        )[:1]

        ordered_ids = []
        for line in [signer_signed, emp_signed]:
            if line and line.id:
                ordered_ids.append(line.id)
        ordered_signed = self.env['appstream.approval.agreement.history.line'].browse(ordered_ids)

        # QR и дата подписания сотрудника (отдельно для ячейки T0[7])
        emp_qr_insert = []
        emp_sign_date = ''
        if emp_signed and emp_signed.qr:
            emp_qr_insert = [{"img": emp_signed.qr, "w": 20, "h": 20}]
            emp_sign_date = emp_signed.agreement_date.strftime('%d.%m.%Y') if emp_signed.agreement_date else ''

        values.update({
            'employee_id': employee.name if employee else '',
            'employee_job_id': employee_job.name if employee_job else '',
            'medical_checkup_date': self.medical_checkup_date.strftime('%d.%m.%Y') if self.medical_checkup_date else '',
            'checkup_time': self.checkup_time or '',
            'clinic': self.clinic or '',
            'clinic_address': self.clinic_address or '',
            'additional_screenings': self.additional_screenings,
            'reason_for_checkup_additional_screenings_rus': self.reason_for_checkup_additional_screenings_rus or '',
            'reason_for_checkup_additional_screenings_kaz': self.reason_for_checkup_additional_screenings_kaz or '',
            'medical_tittle': 'НАПРАВЛЕНИЕ НА МЕДИЦИНСКИЙ ОСМОТР' if not self.additional_screenings else 'НАПРАВЛЕНИЕ НА ДОПОЛНИТЕЛЬНЫЕ ОБСЛЕДОВАНИЯ',
            'state_agreement_lines': ordered_signed,
            'emp_qr__insert': emp_qr_insert,
            'emp_sign_date': emp_sign_date,
            'images__insert': [
                {"img": record.qr, "w": 35, "h": 35}
                for record in ordered_signed
                if record.qr
            ],
        })
        return values

    def prepare_explanation_request_report_values(self):
        """Подготавливает данные для DOCX отчёта Требование о письменном объяснении"""
        self.ensure_one()
        values = self.get_report_values()

        # Данные сотрудника
        employee = self.employee_id
        employee_job = employee.job_id if employee else None

        # Подписи: esp_signer (seq 1) → employee (seq 2)
        signed_lines = self.state_agreement_history_line_ids.filtered(
            lambda l: l.signed
        )

        signer_signed = signed_lines.filtered(
            lambda l: l.user_id == self.esp_signer_id
        )[:1]
        emp_signed = signed_lines.filtered(
            lambda l: self.employee_id
            and self.employee_id.user_id
            and l.user_id == self.employee_id.user_id
        )[:1]

        ordered_ids = []
        for line in [signer_signed, emp_signed]:
            if line and line.id:
                ordered_ids.append(line.id)
        ordered_signed = self.env['appstream.approval.agreement.history.line'].browse(ordered_ids)

        # QR и дата подписания сотрудника (отдельно для ячейки T0[7])
        emp_qr_insert = []
        emp_sign_date = ''
        if emp_signed and emp_signed.qr:
            emp_qr_insert = [{"img": emp_signed.qr, "w": 20, "h": 20}]
            emp_sign_date = emp_signed.agreement_date.strftime('%d.%m.%Y') if emp_signed.agreement_date else ''

        # QR и данные Утверждающего (перезаписываем get_report_values, т.к. там last_signed = employee)
        signer_qr_insert = []
        signer_sign_date = ''
        signer_name = ''
        signer_job_rus = ''
        signer_job_kaz = ''
        if signer_signed and signer_signed.qr:
            signer_qr_insert = [{"img": signer_signed.qr, "w": 20, "h": 20}]
            signer_sign_date = signer_signed.agreement_date.strftime('%d.%m.%Y') if signer_signed.agreement_date else ''
        if self.esp_signer_id:
            signer_emp = self.esp_signer_id.employee_id
            signer_job = signer_emp.job_id if signer_emp else None
            signer_name = signer_emp.name if signer_emp else ''
            signer_job_rus = signer_job.with_context(lang='ru_RU').name if signer_job else ''
            signer_job_kaz = signer_job.with_context(lang='kk_KZ').name if signer_job else ''

        values.update({
            'employee_id': employee.name if employee else '',
            'employee_job_kaz': employee_job.with_context(lang='kk_KZ').name if employee_job else '',
            'employee_job_rus': employee_job.with_context(lang='ru_RU').name if employee_job else '',
            'explanatory_note_text_rus': self.explanatory_note_text_rus or '',
            'explanatory_note_text_kaz': self.explanatory_note_text_kaz or '',
            'text_attachments_rus': self.text_attachments_rus or '',
            'text_attachments_kaz': self.text_attachments_kaz or '',
            'state_agreement_lines': ordered_signed,
            'images__insert': [
                {"img": record.qr, "w": 35, "h": 35}
                for record in ordered_signed
                if record.qr
            ],
            # Утверждающий (T0[6]): перезаписываем данные из get_report_values
            'qr__insert': signer_qr_insert,
            'signer': signer_name,
            'signer_job_rus': signer_job_rus,
            'signer_job_kaz': signer_job_kaz,
            'sign_date': signer_sign_date,
            # Сотрудник (T0[7])
            'emp_qr__insert': emp_qr_insert,
            'emp_sign_date': emp_sign_date,
        })
        return values

    def prepare_medical_assessment_report_values(self):
        """Подготавливает данные для DOCX отчёта Направление на медицинское освидетельствование"""
        self.ensure_one()
        values = self.get_report_values()

        # Данные сотрудника
        employee = self.employee_id
        employee_job = employee.job_id if employee else None

        # Подписанные линии из истории (для "ДОКУМЕНТ УДОСТОВЕРЕН" секций)
        signed_lines = self.state_agreement_history_line_ids.filtered(lambda l: l.signed and l.qr)

        # Данные утверждающего (esp_signer_id) — "уполномоченный представитель работодателя"
        signer_user = self.esp_signer_id
        signer_employee = signer_user.employee_id if signer_user else None
        signer_job = signer_employee.job_id if signer_employee else None

        # Ищем подписанные записи по ролям для QR кодов
        # Порядок подписания: seq 1 = esp_signer, seq 2 = medical_worker, seq 3 = employee
        emp_user = employee.user_id if employee else None
        med_user = self.env['res.users'].sudo().search([
            ('partner_id', '=', self.medical_worker_id.id)
        ], limit=1) if self.medical_worker_id else None

        emp_signed = signed_lines.filtered(
            lambda l: emp_user and l.user_id.id == emp_user.id
        )[:1] if emp_user else self.env['appstream.approval.agreement.history.line']
        signer_signed = signed_lines.filtered(
            lambda l: signer_user and l.user_id.id == signer_user.id
        )[:1] if signer_user else self.env['appstream.approval.agreement.history.line']
        med_signed = signed_lines.filtered(
            lambda l: med_user and l.user_id.id == med_user.id
        )[:1] if med_user else self.env['appstream.approval.agreement.history.line']

        # Дата подписания утверждающего
        signer_signing_date = ''
        if signer_signed and signer_signed.agreement_date:
            signer_signing_date = signer_signed.agreement_date.strftime('%d.%m.%Y')

        # Дата подписания мед. работника
        med_signing_date = ''
        if med_signed and med_signed.agreement_date:
            med_signing_date = med_signed.agreement_date.strftime('%d.%m.%Y')

        # Дата ознакомления сотрудника
        emp_date = ''
        if emp_signed and emp_signed.agreement_date:
            emp_date = emp_signed.agreement_date.strftime('%d.%m.%Y')

        # Собираем state_agreement_lines в порядке подписания
        # для цикла {%p for a in state_agreement_lines %} и images__insert
        # Порядок: утверждающий → мед. работник → сотрудник
        # recordset |= сортирует по id, поэтому собираем ids явно
        ordered_ids = []
        for line in [signer_signed, med_signed, emp_signed]:
            if line and hasattr(line, 'id') and line.id:
                ordered_ids.append(line.id)
        ordered_signed = self.env['appstream.approval.agreement.history.line'].browse(ordered_ids)

        # with_context(lang='ru_RU'
        if self.language == 'kazakh':
            language_context = 'kk_KZ'
        else:
            language_context = 'ru_RU'

        values.update({
            # Данные сотрудника (шаблон: {{employee_id}}, {{employee_job_id}} и т.д.)
            'employee_id': employee.name if employee else '',
            'employee_job_id': self.employee_job_id.with_context(lang=language_context).name if employee else '',
            'employee_identification_id': (self.employee_identification_id or '') if employee else '',
            'employee_udo_number': self.employee_udo_number if employee else '',
            'employee_udo_issuing_authority': self._selection_label('employee_udo_issuing_authority', language_context) if employee else '',
            # Шаблон использует employee_udo_issuing_date_start (не employee_udo_issuing_date)
            'employee_udo_issuing_date_start': (
                self.employee_udo_issuing_date.strftime('%d.%m.%Y')
                if employee and self.employee_udo_issuing_date else ''
            ),

            # Данные акта
            'act_number': self.act_number or '',
            'act_date': self.act_date.strftime('%d.%m.%Y') if self.act_date else '',

            # Основание
            'reason_for_medical_examination_rus': self.reason_for_medical_examination_rus or '',
            'reason_for_medical_examination_kaz': self.reason_for_medical_examination_kaz or '',

            # Утверждающий (шаблон: {{signer}}, {{signer_job}}, {{signing_date}} для утв.)
            'signer': signer_employee.name if signer_employee else '',
            'signer_job': (
                signer_job.with_context(lang=language_context).name if signer_job else ''
            ),
            'signing_date': signer_signing_date,

            # Медицинский работник (шаблон: {{medical_worker_id}}, {{medical_worker_job}})
            'medical_worker_id': self.medical_worker_id.name if self.medical_worker_id else '',
            'medical_worker_job': self.medical_worker_job_id or '',
            'med_signing_date': med_signing_date,

            # Дата ознакомления сотрудника (шаблон: {{date}})
            'date': emp_date,

            # Отказ от подписи
            'medical_assessment_refusal': self.medical_assessment_refusal or '',

            # QR-коды подписантов в теле документа
            # qr1 = утверждающий (esp_signer), qr2 = сотрудник, qr3 = мед. работник
            'qr1__insert': (
                [{"img": signer_signed.qr, "w": 35, "h": 35}]
                if signer_signed and signer_signed.qr else []
            ),
            'qr2__insert': (
                [{"img": emp_signed.qr, "w": 35, "h": 35}]
                if emp_signed and emp_signed.qr else []
            ),
            'qr3__insert': (
                [{"img": med_signed.qr, "w": 35, "h": 35}]
                if med_signed and med_signed.qr else []
            ),

            # Для секции "ДОКУМЕНТ УДОСТОВЕРЕН" (один цикл, 3 подписанта)
            # Порядок: утверждающий → мед. работник → сотрудник
            'state_agreement_lines': ordered_signed,
            'images__insert': [
                {"img": record.qr, "w": 35, "h": 35}
                for record in ordered_signed if record.qr
            ],
        })
        return values

    def prepare_job_offer_report_values(self):
        """Подготовка значений для отчёта «Предложение о работе».
        Подписывает только Утверждающий сотрудник (esp_signer).
        """
        self.ensure_one()
        values = self.get_report_values()

        # Подписи: только esp_signer
        signed_lines = self.state_agreement_history_line_ids.filtered(
            lambda l: l.signed
        )
        signer_signed = signed_lines.filtered(
            lambda l: l.user_id == self.esp_signer_id
        )[:1]

        ordered_ids = [signer_signed.id] if signer_signed else []
        ordered_signed = self.env['appstream.approval.agreement.history.line'].browse(ordered_ids)

        # Данные Утверждающего
        signer_qr_insert = []
        signer_sign_date = ''
        signer_name = ''
        signer_job_rus = ''
        if signer_signed:
            signer_employee = signer_signed.user_id.employee_id
            signer_job = signer_employee.job_id if signer_employee else None
            signer_name = signer_employee.name if signer_employee else ''
            signer_job_rus = signer_job.with_context(lang='ru_RU').name if signer_job else ''
            signer_sign_date = signer_signed.agreement_date.strftime('%d.%m.%Y') if signer_signed.agreement_date else ''
            if signer_signed.qr:
                signer_qr_insert = [{"img": signer_signed.qr, "w": 20, "h": 20}]

        # Должность — название
        position_name = ''
        if self.job_offer_position_id:
            position_name = self.job_offer_position_id.with_context(lang='ru_RU').name or ''

        # Тип занятости — отображаемое значение (используем общий хелпер)
        time_type_label = self._selection_label('time_type', 'ru_RU') if self.time_type else ''

        values.update({
            # Кандидат
            'candidate_name': self.candidate_name or '',
            'candidate_email': self.candidate_email or '',
            'candidate_phone': self.candidate_phone or '',

            # Условия
            'job_offer_position_id': position_name,
            'time_type': time_type_label,
            'working_hours': self.working_hours or '',
            'work_start_date': self.work_start_date.strftime('%d.%m.%Y') if self.work_start_date else '',
            'contract_period': self.contract_period or '',
            'trial_period': self.trial_period or '',
            'salary': f"{self.custom_salary:,.0f}".replace(',', ' ') if self.custom_salary_bool else self.salary or '',
            'holiday_days': str(self.holiday_days) if self.holiday_days else '',
            'additional_info_1': self.additional_info_1 or '',
            'additional_info_2': self.additional_info_2 or '',

            # Контакты / сроки
            'offer_end_date': self.offer_end_date.strftime('%d.%m.%Y') if self.offer_end_date else '',
            'contact_phone': self.contact_phone or '',
            'contact_email': self.contact_email or '',

            # Подписант
            'signer_job_rus': signer_job_rus,
            'employee_id': signer_name,
            'qr__insert': signer_qr_insert,
            'sign_date': signer_sign_date,
            'signer': signer_name,

            # Сертификаты
            'state_agreement_lines': ordered_signed,
            'images__insert': [
                {"img": record.qr, "w": 35, "h": 35}
                for record in ordered_signed if record.qr
            ],
        })
        return values

    def prepare_reference_letter_report_values(self):
        """Подготовка значений для отчёта "Справка с места работы".
        Подписывает только Утверждающий сотрудник (esp_signer).
        """
        self.ensure_one()
        values = self.get_report_values()

        # Подписи: только esp_signer
        signed_lines = self.state_agreement_history_line_ids.filtered(
            lambda l: l.signed
        )
        signer_signed = signed_lines.filtered(
            lambda l: l.user_id == self.esp_signer_id
        )[:1]

        ordered_ids = [signer_signed.id] if signer_signed else []
        ordered_signed = self.env['appstream.approval.agreement.history.line'].browse(ordered_ids)

        # Данные Утверждающего
        signer_qr_insert = []
        signer_sign_date = ''
        signer_name = ''
        signer_job_rus = ''
        signer_job_kaz = ''
        signer_job_eng = ''
        if signer_signed:
            signer_employee = signer_signed.user_id.employee_id
            signer_job = signer_employee.job_id if signer_employee else None
            signer_name = signer_employee.name if signer_employee else ''
            signer_job_rus = signer_job.with_context(lang='ru_RU').name if signer_job else ''
            signer_job_eng = signer_job.with_context(lang='en_US').name if signer_job else ''
            signer_job_kaz = signer_job.with_context(lang='kk_KZ').name if signer_job else ''
            signer_sign_date = signer_signed.agreement_date.strftime('%d.%m.%Y') if signer_signed.agreement_date else ''
            if signer_signed.qr:
                signer_qr_insert = [{"img": signer_signed.qr, "w": 20, "h": 20}]

        work_schedule_kaz = 'Бес күндік жұмыс аптасы'
        work_schedule_rus = 'Пятидневный рабочий график'
        work_schedule_eng = 'Five-day working week'

        if self.is_vahta:
            work_schedule_kaz = f'Вахталық әдіс, 14 күннен кейін 14 күн'
            work_schedule_rus = f'Вахтовый метод, 14 дней через 14 дней'
            work_schedule_eng = f'Shift method, 14 days on 14 days off'

        values.update({
            'employee_id': self.employee_id.name if self.employee_id else '',
            'employee_id_eng': self.employee_id.with_context(lang='en_US').name if self.employee_id else '',
            'employee_identification_id': self.employee_identification_id if self.employee_identification_id else '',
            'birthday': self.employee_id.birthday.strftime('%d.%m.%Y') if self.employee_id and self.employee_id.birthday else '',
            'employee_job_kaz': self.employee_id.job_id.with_context(lang='kk_KZ').name if self.employee_id and self.employee_id.job_id else '',
            'employee_job_rus': self.employee_id.job_id.with_context(lang='ru_RU').name if self.employee_id and self.employee_id.job_id else '',
            'employee_job_eng': self.employee_id.job_id.with_context(lang='en_US').name if self.employee_id and self.employee_id.job_id else '',
            'contract_date': self.employee_contract_date.strftime('%d.%m.%Y') if self.employee_contract_date else '',
            'salary_needed': self.salary_needed,
            'current_salary': self.current_salary,
            'average_salary': self.average_salary if self.average_salary else '',
            'work_schedule_needed': self.work_schedule_needed,
            'is_vahta': self.is_vahta,
            'work_schedule_kaz': work_schedule_kaz,
            'work_schedule_rus': work_schedule_rus,
            'work_schedule_eng': work_schedule_eng,

            # Подписант
            'signer_job_rus': signer_job_rus,
            'signer_job_kaz': signer_job_kaz,
            'signer_job_eng': signer_job_eng,
            'qr__insert': signer_qr_insert,
            'sign_date': signer_sign_date,
            'signer': signer_name,

            # Сертификаты
            'state_agreement_lines': ordered_signed,
            'images__insert': [
                {"img": record.qr, "w": 35, "h": 35}
                for record in ordered_signed if record.qr
            ],
        })
        return values

    # ---------------------------------------------------------
    # Методы скачивания отчётов
    # ---------------------------------------------------------

    def _get_report_by_type(self, output_format='pdf'):
        """
        Возвращает xmlid отчёта на основе типа письма и формата.
        output_format: 'pdf' или 'docx'
        Для medical_examination выбирает шаблон по языку письма.
        """
        self.ensure_one()

        # Маппинг типов писем на отчёты
        type_to_report = {
            'correspondence.medical_checkup': {
                'pdf': 'correspondence.correspondence_medical_checkup_pdf',
                'docx': 'correspondence.correspondence_medical_checkup_docx',
            },
            'correspondence.explanation': {
                'pdf': 'correspondence.correspondence_explanation_request_pdf',
                'docx': 'correspondence.correspondence_explanation_request_docx',
            },
            'correspondence.medical_examination': {
                'pdf': 'correspondence.correspondence_medical_examination_pdf',
                'docx': 'correspondence.correspondence_medical_examination_docx',
            },
            'correspondence.medical_examination_kaz': {
                'pdf': 'correspondence.correspondence_medical_examination_kaz_pdf',
                'docx': 'correspondence.correspondence_medical_examination_kaz_docx',
            },
            'correspondence.job_offer': {
                'pdf': 'correspondence.correspondence_job_offer_pdf',
                'docx': 'correspondence.correspondence_job_offer_docx',
            },
            'correspondence.job_offer_kaz': {
                'pdf': 'correspondence.correspondence_job_offer_kaz_pdf',
                'docx': 'correspondence.correspondence_job_offer_kaz_docx',
            },
            'correspondence.reference': {
                'pdf': 'correspondence.correspondence_reference_letter_pdf',
                'docx': 'correspondence.correspondence_reference_letter_docx',
            },
            'correspondence.reference_eng': {
                'pdf': 'correspondence.correspondence_reference_letter_eng_pdf',
                'docx': 'correspondence.correspondence_reference_letter_eng_docx',
            },
            'correspondence.general_template': {
                'pdf': 'correspondence.correspondence_general_template_pdf',
                'docx': 'correspondence.correspondence_general_template_docx',
            },
            'correspondence.general_template_kaz': {
                'pdf': 'correspondence.correspondence_general_template_kaz_pdf',
                'docx': 'correspondence.correspondence_general_template_kaz_docx',
            },
            'correspondence.general_template_rus': {
                'pdf': 'correspondence.correspondence_general_template_rus_pdf',
                'docx': 'correspondence.correspondence_general_template_rus_docx',
            },
        }

        # Получаем xmlid типа письма
        type_xmlid = None
        if self.type_id:
            type_xmlid = self.type_id.get_external_id().get(self.type_id.id)

        # Для medical_examination: выбираем шаблон по языку
        if type_xmlid == 'correspondence.medical_examination' and self.language == 'kazakh':
            type_xmlid = 'correspondence.medical_examination_kaz'

        # Для job_offer: выбираем шаблон по языку
        if type_xmlid == 'correspondence.job_offer' and self.language == 'kazakh':
            type_xmlid = 'correspondence.job_offer_kaz'

        # Для reference: выбираем шаблон по языку
        if type_xmlid == 'correspondence.reference' and self.language == 'bilingual2':
            type_xmlid = 'correspondence.reference_eng'

        # Ищем отчёт по типу
        if type_xmlid and type_xmlid in type_to_report:
            return type_to_report[type_xmlid].get(output_format)

        # Для general_template: двуязычный бланк только для каз+рус,
        # казахский — для kazakh, во всех остальных случаях русский
        # (английского бланка пока нет).
        if type_xmlid == 'correspondence.general_template':
            if self.language == 'kazakh':
                type_xmlid = 'correspondence.general_template_kaz'
            elif self.language != 'bilingual1':
                type_xmlid = 'correspondence.general_template_rus'

        # Возвращаем дефолтный отчёт
        return None

    def download_report_pdf(self):
        """Скачать PDF отчёт по типу письма"""
        self.ensure_one()

        report_xmlid = self._get_report_by_type('pdf')
        if not report_xmlid:
            raise UserError(_("Не найден шаблон отчёта для данного типа письма"))

        report = self.env.ref(report_xmlid, raise_if_not_found=False)
        if not report:
            raise UserError(_("Отчёт '%s' не найден") % report_xmlid)

        return report.sudo().report_action(self, data={}, config=False)

    def download_report_docx(self):
        """Скачать DOCX отчёт по типу письма"""
        self.ensure_one()

        report_xmlid = self._get_report_by_type('docx')
        if not report_xmlid:
            raise UserError(_("Не найден шаблон отчёта для данного типа письма"))

        report = self.env.ref(report_xmlid, raise_if_not_found=False)
        if not report:
            raise UserError(_("Отчёт '%s' не найден") % report_xmlid)

        return report.sudo().report_action(self, data={}, config=False)

    
    # ---------------------------------------------------------
    # Методы для портального подписания (medical_examination)
    # ---------------------------------------------------------

    def _get_portal_signers(self):
        """
        Возвращает партнёров для портального подписания.
        Для типа medical_examination - это medical_worker_id.
        """
        self.ensure_one()

        medical_examination_type = self.env.ref(
            'correspondence.medical_examination',
            raise_if_not_found=False
        )

        if self.type_id == medical_examination_type and self.medical_worker_id:
            return self.medical_worker_id

        return self.env['res.partner']

    def _portal_signing_complete(self):
        """
        Fallback: вызывается из portal_signing_mixin если _process_post_approval
        не доступен. В штатном потоке НЕ вызывается — вся логика перехода
        обрабатывается через _process_post_approval.
        """
        self.ensure_one()
        next_state = self._get_next_state_after(self.state)
        if not next_state:
            next_state = 'processing'

        if hasattr(self, "method_in_middle"):
            self.method_in_middle()
        self.sudo().get_agreement_lines(next_state)
        
    # ---------------------------------------------------------
    # Проверки типа документа
    # ---------------------------------------------------------

    def _is_medical_examination_type(self):
        """
        Проверяет, является ли документ типом Направление на медицинское освидетельствование.
        """
        medical_examination_type = self.env.ref(
            'correspondence.medical_examination',
            raise_if_not_found=False
        )
        return self.type_id == medical_examination_type

    def _is_medical_checkup_type(self):
        """
        Проверяет, является ли документ типом Направление на медицинский осмотр.
        """
        medical_checkup_type = self.env.ref(
            'correspondence.medical_checkup',
            raise_if_not_found=False
        )
        return self.type_id == medical_checkup_type

    def _is_explanation_type(self):
        """
        Проверяет, является ли документ типом Требование о предоставлении объяснительной.
        """
        explanation_type = self.env.ref(
            'correspondence.explanation',
            raise_if_not_found=False
        )
        return self.type_id == explanation_type

    def _is_job_offer_type(self):
        """
        Проверяет, является ли документ типом Предложение о работе.
        """
        job_offer_type = self.env.ref(
            'correspondence.job_offer',
            raise_if_not_found=False
        )
        return self.type_id == job_offer_type

    def _is_reference_type(self):
        """
        Проверяет, является ли документ типом Справка с места работы.
        """
        reference_type = self.env.ref(
            'correspondence.reference',
            raise_if_not_found=False
        )
        return self.type_id == reference_type