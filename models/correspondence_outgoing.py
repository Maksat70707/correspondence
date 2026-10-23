import base64
import logging
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from datetime import datetime
from markupsafe import Markup

from odoo.addons.appstream_approval.models import esp_copy

from . import corr_preview

_logger = logging.getLogger(__name__)

# Номер до регистрации. Пока он стоит в name, документ считается
# незарегистрированным, а в печатную форму уходит name_draft.
PLACEHOLDER_NUMBER = "---"

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
        "corr.docx.template.mixin",
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

    name_draft = fields.Char(
        string="Предварительный номер",
        copy=False,
        readonly=True,
        help="Номер, прочитанный при сборке документа на подпись, но ещё "
             "не выданный. Именно он уходит в печатную форму: настоящий "
             "номер выдаётся только при сохранении подписи.",
    )

    registration_date = fields.Date(
        string="Дата регистрации",
        copy=False,
        readonly=True,
        help="Проставляется при сборке документа на подпись и закрепляется "
             "регистрацией. Печатается в шапке письма.",
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

    # Закрывает письмо от своего подразделения: снимает только правило
    # rule_corr_outgoing_employee_department. Участники письма (автор,
    # согласующие, утверждающий) и роли с полным доступом (секретарь,
    # директор, администратор) видят его, как и раньше. Менять отметку
    # может автор (или его заместитель) и администратор — см. write.
    is_confidential = fields.Boolean(
        string="Конфиденциально",
        tracking=True,
        help="Письмо не видно коллегам по подразделению. Его видят только "
             "автор, согласующие и утверждающий, а также секретарь, директор "
             "и администратор корреспонденции.",
    )
    can_change_confidential = fields.Boolean(
        string="Может менять «Конфиденциально»",
        compute="_compute_can_change_confidential",
    )

    # Предпросмотр: то, что уйдёт на подпись, одним PDF с пометкой
    # «ПРЕДПРОСМОТР». Одно вложение на письмо, перезаписывается при каждом
    # нажатии; показывается тем же просмотрщиком, что окно подписи.
    preview_file = fields.Binary(
        string="Предпросмотр",
        attachment=True,
        copy=False,
        readonly=True,
    )
    can_preview = fields.Boolean(
        string="Есть что показать в предпросмотре",
        compute="_compute_can_preview",
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
    @api.depends("create_uid")
    def _compute_can_change_confidential(self):
        for rec in self:
            rec.can_change_confidential = rec._can_change_confidential()

    def _can_change_confidential(self):
        """Отметку «Конфиденциально» меняет автор письма или тот, кто его
        замещает, и администратор корреспонденции.

        Согласующие письмо правят, но снимать отметку им нельзя: так
        начальник открыл бы чужое письмо всему подразделению.
        """
        self.ensure_one()
        if self.env.su or self.env.user.has_group("correspondence.group_correspondence_admin"):
            return True
        return self.acts_for_initiator

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
        if "is_confidential" in vals:
            self._check_change_confidential(bool(vals["is_confidential"]))
        res = super().write(vals)

        # Номер здесь НЕ выдаётся. Выдают его _esp_after_signature и
        # add_to_history — то есть сохранение подписи ЭЦП; оба идемпотентны.
        # До этого в печатной форме стоит предварительный номер из
        # name_draft (_refresh_name_draft при сборке документа на подпись).
        #
        # Раньше здесь стоял `if vals.get('state') == 'processing'`. Это давало
        # два бага: (1) номера ещё нет к моменту, когда он нужен на документе;
        # (2) любой транзитный write состояния 'processing' — а _action_approve
        # выставляет state ДО выполнения before/after_script — сжигал номер
        # раньше времени.

        if 'attachment_to_sign_ids' in vals or 'attachment_additional_sign_ids' in vals or 'attachment_extra_ids' in vals:
            self._fix_attachment_ownership()
        return res

    def _check_change_confidential(self, value):
        """Проверка на сервере: форма лишь прячет флажок, а писать письмо
        правило доступа даёт и согласующим."""
        denied = self.filtered(
            lambda rec: rec.is_confidential != value and not rec._can_change_confidential()
        )
        if denied:
            raise UserError(_(
                "Отметку «Конфиденциально» меняет автор письма (или тот, кто "
                "его замещает) и администратор корреспонденции: %s"
            ) % ", ".join(denied.mapped("display_name")))

    # ---------------------------------------------------------
    # Номер исходящего письма
    # ---------------------------------------------------------
    #
    # Номер читается при сборке документа на подпись и выдаётся только при
    # сохранении подписи. Между этими моментами он живёт в name_draft и
    # именно оттуда попадает в печатную форму.
    #
    # Так сделано потому, что документ на подпись замораживается первой
    # подписью: поменять байты позже нельзя. Выдавать номер при сборке —
    # значит терять его на каждом закрытом окне подписи и каждом возврате
    # на доработку, а в журнале регистрации дырок быть не должно.

    def _number_sequence(self):
        """Последовательность, из которой берётся номер исходящего."""
        return self.env["ir.sequence"].sudo().search(
            [("code", "=", "correspondence.outgoing"),
             ("company_id", "in", [self.env.company.id, False])],
            order="company_id", limit=1)

    def _peek_next_number(self):
        """Следующий номер последовательности БЕЗ его выдачи.

        Счётчик не трогается, поэтому закрытое окно подписи, отказ и
        возврат на доработку номер не расходуют."""
        self.ensure_one()
        sequence = self._number_sequence()
        if not sequence:
            return False
        if sequence.use_date_range:
            # У диапазона свой счётчик, в родительской записи лежит номер
            # другого года. Сам диапазон создаётся при первой выдаче.
            today = fields.Date.context_today(self)
            date_range = sequence.date_range_ids.filtered(
                lambda rng: rng.date_from <= today <= rng.date_to)[:1]
            number = date_range.number_next_actual if date_range else 1
        else:
            # Именно number_next_actual: у реализации standard колонка
            # number_next хранит не то, что выдаст последовательность.
            number = sequence.number_next_actual
        # Строку собирает сама последовательность — префикс, нули, %(year)s.
        # Склеить руками значит разойтись с тем, что выдаст next_by_id.
        return sequence.get_next_char(number)

    def _refresh_name_draft(self):
        """Обновить предварительный номер и дату перед сборкой документа.

        Дата регистрации проставляется здесь же: в шапке письма печатается
        именно она, и она должна совпадать в подписанном файле и при
        повторном скачивании."""
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.name and rec.name != PLACEHOLDER_NUMBER:
                continue  # уже зарегистрирован
            values = {}
            number = rec._peek_next_number()
            if number and number != rec.name_draft:
                values["name_draft"] = number
            if rec.registration_date != today:
                values["registration_date"] = today
            if values:
                rec.sudo().write(values)
        return self

    # ---------------------------------------------------------
    # Подстановка значений в загруженное письмо
    # ---------------------------------------------------------

    # Справочник полей подстановки: (имя, что подставляется, пример) —
    # из него собирается подсказка исполнителю (кнопка «Поля подстановки»).
    # Имена совпадают с теми, что стоят в генерируемых печатных формах:
    # {{ name }}, {{ date }}, {{ signer }}, — чтобы словарь был один и тот
    # же и в бланках, и в письмах, которые пишут вручную.
    TEMPLATE_FIELDS = [
        # регистрация
        ("name", "Номер исходящего", "ИСХ-2026-0147"),
        ("date", "Дата письма (она же дата регистрации)", "13.10.2026"),
        ("today", "Сегодняшняя дата", "13.10.2026"),
        # о чём письмо
        ("subject", "Тема", "О предоставлении сведений"),
        ("summary", "Краткое содержание", "Текст из карточки письма"),
        ("summary_rus", "Текст письма рус", "Текст письма"),
        ("summary_kaz", "Текст письма каз", "Хат мәтіні"),
        ("letter_body", "Текст письма", "Основной текст"),
        ("mail_subject_rus", "Тема письма рус", "О предоставлении сведений"),
        ("mail_subject_kaz", "Тема письма каз", "Мәліметтерді ұсыну туралы"),
        ("language", "Язык письма", "Русский"),
        ("letter_type", "Тип письма", "Обычное письмо"),
        # адресат
        ("where", "Куда", "ТОО «Казгеология»"),
        ("whom", "Кому", "Генеральному директору"),
        ("recipient", "Получатели списком", "ТОО «Казгеология», АО «КазМунайГаз»"),
        ("recipient_person", "ФИО получателей", "Иванов И.И."),
        ("recipient_position", "Должности получателей", "Генеральный директор"),
        ("recipient_address", "Адреса получателей", "г. Астана, ул. Кунаева 12"),
        # письмо, на которое отвечаем
        ("incoming_number", "Номер входящего", "ВХ-2026-0091"),
        ("incoming_date", "Дата входящего", "01.10.2026"),
        ("incoming_external_number", "Исходящий номер отправителя", "04-17/318"),
        # сотрудник, о котором письмо
        ("employee", "Сотрудник", "Иванов Иван Иванович"),
        ("employee_iin", "ИИН сотрудника", "900101300123"),
        ("employee_job", "Должность сотрудника", "Главный инженер"),
        ("department", "Подразделение сотрудника", "Производственный отдел"),
        # исполнитель
        ("executor", "Исполнитель (создал письмо)", "Петров Пётр Петрович"),
        ("executor_job", "Должность исполнителя", "Специалист"),
        ("contact_phone", "Телефон для связи", "+7 777 000 00 00"),
        ("contact_email", "Почта для связи", "petrov@caspiyneft.kz"),
        # утверждающий
        ("signer", "Утверждающий", "Сидоров Сидор Сидорович"),
        ("signer_job_rus", "Должность утверждающего, рус", "Генеральный директор"),
        ("signer_job_kaz", "Должность утверждающего, каз", "Бас директор"),
        ("signer_job_eng", "Должность утверждающего, англ", "General Director"),
        # организация
        ("company", "Организация", "ТОО «Каспий нефть»"),
        ("company_bin", "БИН организации", "123456789012"),
    ]

    # Второе имя для тех же значений. Нужны, потому что в документообороте
    # и в черновиках писем прижились другие названия: номер там {{number}},
    # исполнитель — {{author}}. Проще принимать оба, чем заставлять
    # переписывать уже готовые письма.
    TEMPLATE_ALIASES = {
        "number": "name",
        "reg_date": "date",
        "author": "executor",
        "author_position": "executor_job",
        "author_phone": "contact_phone",
        "author_email": "contact_email",
        "addressee": "recipient",
        "addressee_full_name": "recipient_person",
        "position": "employee_job",
        "signer_position": "signer_job_rus",
        # в бланке «Шаблонное письмо» исполнитель называется так
        "create_uid": "executor",
    }

    def _template_sources(self):
        """Поля с файлами, которые сотрудник готовит сам: в них и может
        быть {{...}}. Печатные формы собирает отчёт, им подстановка не нужна."""
        return ["attachment_to_sign_ids", "attachment_additional_sign_ids"]

    def _template_values(self):
        """Поля, доступные в загруженном письме.

        Номер, дата и подписант берутся из тех же значений, что и
        печатные формы, — чтобы в обоих случаях подставлялось одно и то
        же. Остальное — реквизиты исходящего письма.

        sudo(): должность и ИИН лежат за hr.version, он открыт кадровикам.
        Исполнитель оформляет письмо на сотрудника и вправе видеть его
        должность в бланке, но не всю карточку."""
        self.ensure_one()
        record = self.sudo()
        values = dict(record.get_report_values())
        recipients = self.correspondent_line_ids
        employee = record.employee_id
        executor = record.create_uid
        values.update({
            "today": fields.Date.context_today(self).strftime('%d.%m.%Y'),
            "subject": self.subject or "",
            "language": dict(self._fields["language"]._description_selection(
                self.env)).get(self.language, "") if self.language else "",
            "letter_type": self.type_id.name or "",
            # текст письма
            "summary_rus": self.summary_rus or "",
            "summary_kaz": self.summary_kaz or "",
            "letter_body": self.letter_body or "",
            "mail_subject_rus": self.mail_subject_rus or "",
            "mail_subject_kaz": self.mail_subject_kaz or "",
            # адресат
            "where": self.where or "",
            "whom": self.whom or "",
            "recipient": ", ".join(filter(None, recipients.mapped("name"))),
            "recipient_person": ", ".join(filter(None, recipients.mapped("full_name"))),
            "recipient_position": ", ".join(filter(None, recipients.mapped("position"))),
            "recipient_address": ", ".join(filter(None, recipients.mapped("address"))),
            # сотрудник, о котором письмо
            "employee": employee.name or "",
            "employee_iin": record.employee_identification_id or "",
            "employee_job": record.employee_job_id.name or "",
            "department": employee.department_id.name or "",
            # исполнитель и контакты
            "executor": executor.name or "",
            "executor_job": executor.employee_id.job_id.name or "",
            "contact_phone": self.contact_phone or "",
            "contact_email": self.contact_email or "",
            # организация
            "company": self.env.company.name or "",
            "company_bin": self.env.company.vat or "",
            # письмо, на которое отвечаем
            "incoming_number": record.incoming_id.name or "",
            "incoming_date": (
                record.incoming_id.doc_arrival_date.strftime('%d.%m.%Y')
                if record.incoming_id.doc_arrival_date else ""
            ),
            "incoming_external_number": record.incoming_id.external_number or "",
        })
        values = {key: ("%s" % value if value else "")
                  for key, value in values.items()}
        for alias, canonical in self.TEMPLATE_ALIASES.items():
            values.setdefault(alias, values.get(canonical, ""))
        return values

    def _esp_field_sources(self, name):
        """Подстановка делается здесь — при сборке файла на подпись.

        Меняются только данные, уходящие в подпись: вложение в поле
        остаётся шаблоном, поэтому после доработки письмо можно отправить
        ещё раз. Позже менять нечего — байты заморожены первой подписью."""
        sources = super()._esp_field_sources(name)
        if name not in self._template_sources():
            return sources
        for source in sources:
            raw = source.get("datas")
            if not raw:
                continue
            filled = self.fill_template(base64.b64decode(raw), source.get("name") or "")
            if filled:
                source["datas"] = base64.b64encode(filled)
        return sources

    def _report_number(self):
        """Номер для печатной формы: настоящий, а пока его нет —
        предварительный."""
        self.ensure_one()
        if self.name and self.name != PLACEHOLDER_NUMBER:
            return self.name
        number = self.name_draft
        if not number and self.env.context.get("corr_preview"):
            # в предпросмотре черновика номера ещё нет — следующий по
            # последовательности, он ничего не занимает
            number = self._peek_next_number()
        return number or self.name or ""

    def _assign_document_number(self):
        """Выдаёт номер из последовательности, если он ещё не присвоен.

        Вызывается при сохранении подписи ЭЦП (_esp_after_signature) и из
        add_to_history. Идемпотентен: второй и третий подписант номер не
        меняют и последовательность не расходуют.

        Выданный номер сверяется с тем, что ушёл в подписанный файл: файл
        к этому моменту заморожен, и расхождение иначе уехало бы в архив
        незамеченным. Разойтись они могут, если между сборкой документа и
        подписанием номер забрало другое письмо — тем вероятнее, чем
        дольше пауза, а на портале она бывает долгой.

        sudo(): подписант на этапе Утверждения — не обязательно секретарь,
        а name и registration_date объявлены readonly."""
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.name and rec.name != PLACEHOLDER_NUMBER:
                continue
            sequence = rec._number_sequence()
            number = sequence.next_by_id() if sequence else False
            rec.sudo().write({
                "name": number or PLACEHOLDER_NUMBER,
                "registration_date": rec.registration_date or today,
            })
            if number and rec.name_draft and number != rec.name_draft:
                rec.message_post(body=_(
                    "В документе на подпись номер %(draft)s, а зарегистрирован "
                    "%(real)s. Документ нужно собрать и подписать заново.",
                    draft=rec.name_draft, real=number))
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
        for rec in self:
            if not rec.subject:
                raise ValidationError(_("Укажите тему документа"))
            if not rec.summary:
                raise ValidationError(_("Укажите краткое содержание документа"))
            # Исходящее письмо для подписания обязательно только для обычного письма
            simple_type = self.env.ref('correspondence.simple', raise_if_not_found=False)
            if rec.type_id == simple_type and not rec.attachment_to_sign_ids:
                raise ValidationError(_("Прикрепите исходящее письмо для подписания"))

        # Опечатка в поле {{...}} иначе доедет до подписи и закрепится
        # ЭЦП навсегда: подставлять будем уже в замороженный документ.
        self.check_templates()

    def method_in_middle(self):
        """Валидация перед переходом между промежуточными статусами.

        Проверять формат вложений больше не нужно: подписывается хеш файла
        («XML с хешем документа»), и сам файл никуда не передаётся, так что
        подписать можно вложение любого формата. Раньше требовались .pdf
        или .docx, потому что QR и лист с данными сертификата вшивались в
        сам файл и модуль умел это только для этих двух форматов.
        """
        return

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

        Кто чей — решается ДО вызова фреймворка: после возврата
        button_approve_enabled считается уже по новому статусу, и запись,
        которую вернул согласующий-инициатор на промежуточный этап, попадала
        во вторую ветку и обрабатывалась дважды (вторая активность «возвращён
        на доработку», повторное аннулирование подписей).
        """
        by_approver = self.filtered("button_approve_enabled")
        by_initiator = (self - by_approver).filtered(
            lambda r: r.acts_for_initiator
            and r.state in ('under_approval', 'approval', 'approval_medical_examination')
        )

        action = super()._action_return(state, reason)

        for record in by_initiator:
            old_state = record.state
            record._remove_approval_activity(action="return", reason=reason)
            record.with_context(reject_reason=reason).write({"state": state})
            # appstream_approval 19.0.0.2 аннулирует подписи тех этапов,
            # которые документ будет проходить заново. Для согласующих это
            # делает super(), а своя ветка повторяет шаги фреймворка — без
            # этой строки подпись утверждающего, поставленная до возврата,
            # осталась бы действующей и на странице проверки по QR.
            record._esp_annul_stages(old_state, state)
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

        # Возврат на промежуточный этап (с Утверждения на Согласование):
        # фреймворк только переписывает статус, before_script этапа не
        # выполняется, а строки мы только что удалили — письмо оставалось
        # на этапе без единого согласующего, и двигать его было некому.
        # Собираем маршрут этапа заново, как при обычном входе в него.
        # Активность «на доработку» инициатору в этом случае не ставится:
        # править ему нечего, письмо ушло к согласующим этапа, а закрыть
        # такую активность было бы нечем — повторной отправки из Черновика
        # не будет.
        if new_state and new_state in dict(self._approval_states()):
            self.with_context(corr_route_rebuild=True).get_agreement_lines(new_state)
            return

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
          - default_is_confidential — конфиденциальность предыдущего письма
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
                # Продолжение конфиденциальной переписки — тоже конфиденциально.
                "default_is_confidential": self.is_confidential,
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
        Заполняет parent_outgoing_id (текущее), наследует incoming_id и
        отметку «Конфиденциально».
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
                # Продолжение конфиденциальной переписки — тоже конфиденциально.
                "default_is_confidential": self.is_confidential,
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


    # ---------------------------------------------------------
    # Значения для печатных форм
    # ---------------------------------------------------------
    #
    # Ни QR, ни дат подписания, ни данных сертификатов здесь больше нет.
    # Документ на подпись формируется ДО первой подписи и замораживается
    # ею, поэтому подставить в него что-либо из истории подписания нельзя:
    # вышла бы пустая строка в подписанном файле и заполненная — при
    # повторном скачивании, то есть два разных документа.
    #
    # Кто и когда подписал, показывает печатная версия appstream_approval:
    # QR и надпись вдоль поля на каждой странице плюс лист «ДОКУМЕНТ
    # УДОСТОВЕРЕН» с данными сертификатов.

    def _report_letter_date(self):
        """Дата в шапке письма: дата регистрации, а пока номер не
        присвоен — дата создания."""
        self.ensure_one()
        date = self.registration_date or (
            self.create_date.date() if self.create_date else False)
        if self.env.context.get("corr_preview") and (
                not self.name or self.name == PLACEHOLDER_NUMBER):
            # при сборке на подпись дата станет сегодняшней
            # (_refresh_name_draft) — её и показываем
            date = fields.Date.context_today(self)
        return date.strftime('%d.%m.%Y') if date else ''

    def _report_signer_values(self):
        """ФИО и должность Утверждающего — из назначенного подписанта
        (esp_signer_id), а не из истории подписания: на момент формирования
        документа там ещё пусто."""
        self.ensure_one()
        employee = self.esp_signer_id.employee_id if self.esp_signer_id else None
        job = employee.job_id if employee else None
        return {
            'signer': employee.name if employee else '',
            'signer_job_rus': job.with_context(lang='ru_RU').name if job else '',
            'signer_job_kaz': job.with_context(lang='kk_KZ').name if job else '',
            'signer_job_eng': job.with_context(lang='en_US').name if job else '',
        }

    def get_report_values(self) -> dict:
        """Значения, общие для всех печатных форм."""
        self.ensure_one()
        values = {
            'name': self._report_number(),
            'date': self._report_letter_date(),
            'summary': self.summary or '',
        }
        values.update(self._report_signer_values())
        return values

    def prepare_general_template_report_values(self):
        """Подготовка значений для отчёта «Шаблонное письмо».

        Бланк заполняется целиком из полей формы."""
        self.ensure_one()
        values = self.get_report_values()
        values.update({
            'where': self.where or '',
            'whom': self.whom or '',
            'mail_subject_kaz': self.mail_subject_kaz or '',
            'mail_subject_rus': self.mail_subject_rus or '',
            'summary_kaz': self.summary_kaz or '',
            'summary_rus': self.summary_rus or '',
            # В бланке {{create_uid}} — строка «исполнитель», нужно имя, а не recordset
            'create_uid': self.create_uid.name or '',
            'contact_phone': self.contact_phone or '',
            'contact_email': self.contact_email or '',
        })
        return values

    def prepare_medical_checkup_report_values(self):
        """Подготавливает данные для DOCX отчёта Направление на медицинский осмотр"""
        self.ensure_one()
        values = self.get_report_values()

        employee = self.employee_id
        employee_job = employee.job_id if employee else None

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
        })
        return values

    def prepare_explanation_request_report_values(self):
        """Подготавливает данные для DOCX отчёта Требование о письменном объяснении"""
        self.ensure_one()
        values = self.get_report_values()

        employee = self.employee_id
        employee_job = employee.job_id if employee else None

        values.update({
            'employee_id': employee.name if employee else '',
            'employee_job_kaz': employee_job.with_context(lang='kk_KZ').name if employee_job else '',
            'employee_job_rus': employee_job.with_context(lang='ru_RU').name if employee_job else '',
            'explanatory_note_text_rus': self.explanatory_note_text_rus or '',
            'explanatory_note_text_kaz': self.explanatory_note_text_kaz or '',
            'text_attachments_rus': self.text_attachments_rus or '',
            'text_attachments_kaz': self.text_attachments_kaz or '',
        })
        return values

    def prepare_medical_assessment_report_values(self):
        """Подготавливает данные для DOCX отчёта Направление на медицинское
        освидетельствование.

        Подписывают трое: уполномоченный представитель работодателя
        (esp_signer_id), медицинский работник (medical_worker_id) и сам
        работник (employee_id). Все трое известны до подписания, поэтому
        таблица подписей бланка заполняется из полей записи."""
        self.ensure_one()
        values = self.get_report_values()

        employee = self.employee_id
        language_context = 'kk_KZ' if self.language == 'kazakh' else 'ru_RU'

        signer_employee = self.esp_signer_id.employee_id if self.esp_signer_id else None
        signer_job = signer_employee.job_id if signer_employee else None

        values.update({
            # Данные работника
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

            # Уполномоченный представитель работодателя. signer_job — одна
            # колонка бланка, поэтому язык один, а не rus/kaz из общих значений.
            'signer_job': (
                signer_job.with_context(lang=language_context).name if signer_job else ''
            ),

            # Медицинский работник
            'medical_worker_id': self.medical_worker_id.name if self.medical_worker_id else '',
            'medical_worker_job': self.medical_worker_job_id or '',

            # Отказ от подписи
            'medical_assessment_refusal': self.medical_assessment_refusal or '',
        })
        return values

    def prepare_job_offer_report_values(self):
        """Подготовка значений для отчёта «Предложение о работе»."""
        self.ensure_one()
        values = self.get_report_values()

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

            # В этом бланке подпись выводится через {{employee_id}}
            'employee_id': values['signer'],
        })
        return values

    def prepare_reference_letter_report_values(self):
        """Подготовка значений для отчёта «Справка с места работы»."""
        self.ensure_one()
        values = self.get_report_values()

        work_schedule_kaz = 'Бес күндік жұмыс аптасы'
        work_schedule_rus = 'Пятидневный рабочий график'
        work_schedule_eng = 'Five-day working week'

        if self.is_vahta:
            work_schedule_kaz = 'Вахталық әдіс, 14 күннен кейін 14 күн'
            work_schedule_rus = 'Вахтовый метод, 14 дней через 14 дней'
            work_schedule_eng = 'Shift method, 14 days on 14 days off'

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

        # Для general_template: двуязычный бланк только для каз+рус,
        # казахский — для kazakh, во всех остальных случаях русский
        # (английского бланка пока нет).
        #
        # Проверка должна стоять ВЫШЕ поиска по type_to_report: ключ
        # 'correspondence.general_template' в словаре есть, поэтому раньше
        # метод возвращал двуязычный бланк, не доходя до выбора по языку,
        # и ветка ниже не срабатывала никогда.
        if type_xmlid == 'correspondence.general_template':
            if self.language == 'kazakh':
                type_xmlid = 'correspondence.general_template_kaz'
            elif self.language != 'bilingual1':
                type_xmlid = 'correspondence.general_template_rus'

        # Ищем отчёт по типу
        if type_xmlid and type_xmlid in type_to_report:
            return type_to_report[type_xmlid].get(output_format)

        # Возвращаем дефолтный отчёт
        return None

    # Кнопок «Скачать PDF/DOCX» на форме больше нет: они отдавали
    # НЕподписанный пере-рендер письма, то есть второй документ рядом с
    # подписанным. Сами отчёты никуда не делись — они привязаны к модели
    # (binding_type=report) и доступны из меню «Печать»; подписанное письмо
    # берётся по ссылке «Скачать печатную версию» на вкладке «Согласующие».
    #
    # _get_report_by_type ОСТАЁТСЯ: им пользуется _esp_extra_documents,
    # чтобы выбрать печатную форму для подписания. Удалить его нельзя.
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

    # ---------------------------------------------------------
    # Подпись ЭЦП: что подписывается и что печатается на копии
    # ---------------------------------------------------------

    def _esp_has_printed_form(self):
        """Письмо формируется печатной формой (направления, объяснительная,
        приглашение на работу, справка, шаблонное письмо): подписывается
        она, а не файл «Исходящее письмо для подписания».

        Здесь перечислены все типы, у которых письмо генерируется. У
        остальных (обычное, гарантийное, уведомление об изменении условий)
        письмо загружают файлом — у них show_main_attachment = True.
        """
        return any(getattr(self, check)() for check in (
            '_is_explanation_type', '_is_medical_examination_type',
            '_is_medical_checkup_type', '_is_job_offer_type', '_is_reference_type',
            '_is_general_template_type',
        ) if hasattr(self, check))

    def _esp_document_fields(self, line=None):
        """У писем с печатной формой загруженный файл письма не подписывается:
        подписывается сформированный отчёт. Дополнительные документы
        подписываются всегда.

        Заодно это самая ранняя точка сборки документа на подпись —
        фреймворк зовёт её при каждом открытии окна подписи, до того как
        файл сформирован. Здесь и обновляем предварительный номер: позже
        байты уже заморожены первой подписью."""
        self._refresh_name_draft()
        names = super()._esp_document_fields(line)
        if self._esp_has_printed_form():
            names = [name for name in names if name != 'attachment_to_sign_ids']
        return names

    def _esp_extra_documents(self):
        """Печатная форма письма (PDF, а если PDF не собрался — DOCX) —
        документ на подпись вместе с дополнительными файлами. Её видят до
        подписи и сотрудники, и медработник на портале."""
        documents = super()._esp_extra_documents()
        if not self._esp_has_printed_form():
            return documents
        # Номер здесь НЕ выдаётся: в шаблон уходит предварительный из
        # name_draft (его обновил _esp_document_fields). Иначе каждое
        # открытие окна подписи, закрытое без подписи, съедало бы номер.
        for output_format in ('pdf', 'docx'):
            xmlid = self._get_report_by_type(output_format)
            report = xmlid and self.env.ref(xmlid, raise_if_not_found=False)
            if not report:
                continue
            try:
                content, extension = report.render_docx(xmlid, [self.id], data={})
            except Exception:
                _logger.exception("Печатная форма %s не сформирована в %s",
                                  self.id, output_format)
                continue
            name = (self.display_name or self.subject or 'document').replace('/', '-')
            return documents + [('%s.%s' % (name, extension), base64.b64encode(content))]
        return documents

    # ---------------------------------------------------------
    # Предпросмотр
    # ---------------------------------------------------------
    # До первой подписи: после неё файлы заморожены, есть печатная версия.
    PREVIEW_STATES = ('draft', 'under_approval', 'approval', 'approval_medical_examination')

    @api.depends('state', 'type_id', 'esp_attachment_ids',
                 'attachment_to_sign_ids', 'attachment_additional_sign_ids')
    def _compute_can_preview(self):
        """Пока письмо не подписано и есть что показать: печатная форма
        типа или файлы на подпись, которые переводятся в PDF."""
        for rec in self:
            if rec.state not in self.PREVIEW_STATES:
                rec.can_preview = False
                continue
            files = rec.sudo().esp_attachment_ids | rec.sudo().attachment_to_sign_ids \
                | rec.sudo().attachment_additional_sign_ids
            rec.can_preview = rec._esp_has_printed_form() or any(
                esp_copy.supported(file.name, file.mimetype) for file in files)

    def _preview_signing_stage(self):
        """Этап, на котором письмо подпишут: Утверждение или, у направления
        на мед. освидетельствование, его отдельный этап."""
        self.ensure_one()
        xmlid = ('correspondence.corr_outgoing_workflow_approval_medical_examination'
                 if self._is_medical_examination_type()
                 else 'correspondence.corr_outgoing_workflow_approval')
        return self.env.ref(xmlid, raise_if_not_found=False)

    def _preview_document_fields(self):
        """Поля с файлами, которые подпишут, — как _esp_document_fields на
        этапе подписания, но без обновления предварительного номера: он
        пишется в письмо, а предпросмотр смотрят и те, кто его не правит."""
        self.ensure_one()
        stage = self._preview_signing_stage()
        configured = self.env['ir.model.fields'].sudo()
        if stage:
            stage = stage.sudo()
            configured |= stage.esp_document_field_ids | stage.approval_group_ids.esp_document_field_ids
        names = ['esp_attachment_ids']
        for field in configured.sorted('id'):
            if field.model == self._name and field.name in self._fields \
                    and field.name not in names:
                names.append(field.name)
        if self._esp_has_printed_form():
            names = [name for name in names if name != 'attachment_to_sign_ids']
        return names

    def action_preview(self):
        """Кнопка «Предпросмотр»: то, что уйдёт на подпись, — печатная форма
        письма и файлы с подставленными данными — одним PDF с пометкой
        «ПРЕДПРОСМОТР».

        Генерация та же, что при подписании (_esp_extra_documents и
        _esp_field_sources), но записи подписания не создаются: окно
        подписи, открытое сейчас у утверждающего, не пересобирается.
        """
        self.ensure_one()
        if self.state not in self.PREVIEW_STATES:
            raise UserError(_("Предпросмотр есть до подписания. У подписанного "
                              "письма смотрите печатную версию."))
        attachment = self._store_preview(self._build_preview_pdf())
        return {
            "type": "ir.actions.client",
            "tag": "correspondence.preview",
            "name": _("Предпросмотр"),
            "target": "new",
            "params": {
                "url": "/web/content/%d?unique=%s" % (attachment.id, attachment.checksum),
            },
            "context": {"dialog_size": "extra-large"},
        }

    def _build_preview_pdf(self):
        self.ensure_one()
        # sudo: файлы и печатную форму собираем как при подписании — там
        # фреймворк тоже работает под sudo; доступ к письму проверен вызовом
        record = self.sudo().with_context(corr_preview=True)
        sources = [{'name': name, 'datas': datas}
                   for name, datas in record._esp_extra_documents()]
        if record._esp_has_printed_form() and not sources:
            raise UserError(_("Печатная форма письма не сформировалась — "
                              "подробности в журнале сервера. Обратитесь к "
                              "администратору."))
        for name in record._preview_document_fields():
            sources += record._esp_field_sources(name)
        pdfs, failed = [], []
        for source in sources:
            name, datas = source.get('name') or '', source.get('datas')
            if not datas or not esp_copy.supported(name):
                continue  # картинки и прочее в PDF не переводятся
            pdf = esp_copy.document_pdf(base64.b64decode(datas), name)
            if pdf is None:
                failed.append(name)
            else:
                pdfs.append(pdf)
        if failed:
            raise UserError(
                _("Не удалось перевести в PDF: %s.\nНа сервере нужен LibreOffice — "
                  "тот же, что строит печатные версии. Обратитесь к администратору.")
                % ", ".join(failed))
        if not pdfs:
            raise UserError(_("Нечего показать: нет ни печатной формы, ни файлов на подпись."))
        try:
            return corr_preview.stamp_preview(
                pdfs,
                _("ПРЕДПРОСМОТР"),
                _("Предпросмотр: письмо не подписано, номер и данные могут измениться"),
            )
        except Exception as error:  # noqa: BLE001 — запароленный или битый PDF
            _logger.exception("Предпросмотр письма %s не собран", self.id)
            raise UserError(_("Не удалось собрать предпросмотр: %s") % error) from error

    def _store_preview(self, pdf):
        """PDF в поле preview_file: одно вложение на письмо. Пишем вложение
        напрямую, а не через write письма: предпросмотр смотрят и те, у кого
        права записи нет."""
        self.ensure_one()
        Attachment = self.env['ir.attachment'].sudo()
        name = (self.display_name or self.subject or 'letter').replace('/', '-')
        values = {'name': _('Предпросмотр %s.pdf') % name, 'raw': pdf,
                  'mimetype': 'application/pdf'}
        attachment = Attachment.search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('res_field', '=', 'preview_file'),
        ], limit=1)
        if attachment:
            attachment.write(values)
        else:
            attachment = Attachment.create(dict(
                values, res_model=self._name, res_id=self.id, res_field='preview_file'))
        return attachment

    def _esp_after_signature(self, qr_image):
        # Письма без печатной формы подписывают загруженный файл, и через
        # _esp_extra_documents не проходят — номер им присваиваем здесь.
        # Вызов до построения печатной версии: номер печатается на её полях.
        self._assign_document_number()
        return super()._esp_after_signature(qr_image)

    def _esp_copy_stamp_lines(self, document):
        """Номер и дата письма на полях печатной версии — как раньше в
        боковой подписи: у самого письма, у дополнительных файлов — без
        номера."""
        lines = super()._esp_copy_stamp_lines(document)
        signed = document.signature_ids[:1].signed_at
        date = fields.Datetime.context_timestamp(self, signed).strftime('%d.%m.%Y') \
            if signed else ''
        if document.source_field == 'attachment_additional_sign_ids':
            return lines + ["от %s" % date]
        return lines + ["%s от %s" % (self.name or '', date)]

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

    def _is_general_template_type(self):
        """
        Проверяет, является ли документ типом Шаблонное письмо.
        """
        general_template_type = self.env.ref(
            'correspondence.general_template',
            raise_if_not_found=False
        )
        return self.type_id == general_template_type