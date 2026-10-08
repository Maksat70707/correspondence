# docx / PyPDF2 / reportlab / PIL больше не нужны: QR и лист с данными
# сертификата печатает модуль согласования на печатной версии документа.
from odoo import _, models, Command, fields, api
from datetime import datetime
from odoo.exceptions import ValidationError

import logging

_logger = logging.getLogger(__name__)


class CorrOutgoingApproveProcessMixin(models.AbstractModel):
    _name = "corr.outgoing.approve.process.mixin"
    _description = "Correspondence Outgoing approve process mixin"

    number_of_esp_signs = fields.Integer(
        string="Количество ЭЦП подписей",
        default=0,
        copy=False,
    )

    # ==================================================================
    # Маршрутизация статусов
    # ==================================================================

    def _get_next_state_after(self, current_state):
        """
        Возвращает следующий статус после текущего по workflow.

        Стандартный тип:
            under_approval → approval → processing → review → done

        medical_examination:
            under_approval → approval_medical_examination → processing → review → done
        """
        is_med = (
            hasattr(self, '_is_medical_examination_type')
            and self._is_medical_examination_type()
        )

        state_sequence = {
            'under_approval': 'approval_medical_examination' if is_med else 'approval',
            'approval': 'processing',
            'approval_medical_examination': 'processing',
            'processing': 'review',
            'review': 'done',
        }
        return state_sequence.get(current_state)

    # ==================================================================
    # Создание линий согласования
    # ==================================================================

    def get_agreement_lines(self, state="under_approval"):
        """Создаёт линии согласования для указанного статуса."""
        # Защита от повторного создания если текущий user уже in_progress
        current_user_line = self.state_agreement_line_ids.filtered(
            lambda l: l.user_id.id == self.env.uid and l.status == 'in_progress'
        )
        if current_user_line:
            return True

        if hasattr(self, "method_on_start"):
            self.method_on_start()

        # Получаем need_esp из workflow
        workflow = self.env['appstream.approval.workflow'].sudo().search([
            ('approval_id.model_id.model', '=', self._name),
            ('state', '=', state)
        ], limit=1)
        need_esp = workflow.need_esp if workflow else False

        # Очищаем текущие линии
        self.sudo().state_agreement_line_ids.unlink()

        # Создаём линии из before_approval_groups (модельная логика)
        before_lines, sequence, init_approvers = (
            self.get_agreement_lines_by_before_approval_groups(
                status=state, need_esp=need_esp
            )
        )
        agreement_lines = before_lines
        start = sequence + 1 if before_lines else sequence

        # Создаём линии из approval_group_ids workflow (XML-конфигурация)
        approval_lines = self.get_agreement_lines_by_approval_groups(
            start=start, init_approvers=init_approvers, need_esp=need_esp
        )
        agreement_lines += approval_lines

        # Если линии пустые - пропускаем статус
        if not agreement_lines:
            next_state = self._get_next_state_after(state)
            if next_state and next_state != 'done':
                return self.get_agreement_lines(next_state)
            elif next_state == 'done':
                self.action_notify("approved")
                return self.sudo().write({"state": "done"})
            return self.sudo().write({"state": state})

        self.sudo().state_agreement_line_ids = agreement_lines

        if hasattr(self, "check_agreement_line_status"):
            self.check_agreement_line_status()

        return self.sudo().write({"state": state})

    def get_agreement_lines_by_before_approval_groups(
        self, agreement_lines=None, sequence=1, init_approvers=None,
        status=None, need_esp=False
    ):
        """
        Создаёт линии согласования в зависимости от статуса.
        Это основная логика маршрутизации людей по статусам.
        """
        agreement_lines = [] if agreement_lines is None else agreement_lines
        init_approvers = [] if init_approvers is None else init_approvers
        approver_ids = []

        # ==============================================================
        # under_approval — руководитель + дополнительные согласующие
        # ==============================================================
        if status == 'under_approval':
            initiator = self.create_uid
            if initiator and initiator.employee_id:
                manager = initiator.employee_id.parent_id
                if manager and manager.user_id:
                    manager_user = manager.user_id
                    is_director = manager_user.has_group(
                        'correspondence.group_correspondence_director'
                    )
                    if not is_director and manager_user.id != 2:
                        approver_ids.append(manager_user)

        # ==============================================================
        # review — инициатор (ознакомление)
        # ==============================================================
        if status == 'review':
            approver_ids.append(self.create_uid)

        # ==============================================================
        # approval — esp_signer_id подписывает с ЭЦП (стандартные типы)
        #   для medical_checkup: + employee_id (ЭЦП в системе, сотрудник)
        # ==============================================================
        if status == 'approval':
            if hasattr(self, 'esp_signer_id') and self.esp_signer_id:
                signer = self.esp_signer_id
                if signer.id != 2 and signer not in init_approvers:
                    init_approvers.append(signer)
                    self.action_notify("agreement", signer)
                    agreement_lines.append(
                        Command.create({
                            "sequence": sequence,
                            "model": self._name,
                            "user_id": signer.id,
                            "status": "in_progress",
                            "need_esp": True,
                        })
                    )
                    sequence += 1

            # Для мед. осмотра и объяснительной: employee_id подписывает после утверждающего
            if (
                hasattr(self, '_is_medical_checkup_type')
                and (self._is_medical_checkup_type() or self._is_explanation_type())
                and hasattr(self, 'employee_id')
                and self.employee_id
                and self.employee_id.user_id
            ):
                emp_user = self.employee_id.user_id
                if emp_user.id != 2 and emp_user not in init_approvers:
                    init_approvers.append(emp_user)
                    agreement_lines.append(
                        Command.create({
                            "sequence": sequence,
                            "model": self._name,
                            "user_id": emp_user.id,
                            "status": "waiting",
                            "need_esp": True,
                            "all_approve": True,
                        })
                    )

            return agreement_lines, sequence, init_approvers

        # ==============================================================
        # approval_medical_examination — 3 подписанта с ЭЦП
        #   seq 1: esp_signer_id        (ЭЦП в системе, утверждающий)
        #   seq 2: medical_worker_id    (ЭЦП на портале, мед. работник)
        #   seq 3: employee_id.user_id  (ЭЦП в системе, сотрудник)
        # ==============================================================
        if status == 'approval_medical_examination':
            return self._build_approval_medical_examination_lines()

        # ==============================================================
        # processing — секретарь
        # ==============================================================
        if status == 'processing':
            secretary_users = self.env['res.users'].sudo().search([
                ('group_ids', 'in', self.env.ref(
                    'correspondence.group_correspondence_secretary'
                ).id),
                ('id', '!=', 2),
            ])
            for secretary in secretary_users:
                if secretary not in init_approvers:
                    init_approvers.append(secretary)
                    self.action_notify("agreement", secretary)
                    agreement_lines.append(
                        Command.create({
                            "sequence": sequence,
                            "model": self._name,
                            "user_id": secretary.id,
                            "status": "in_progress",
                            "need_esp": need_esp,
                        })
                    )
                    break  # Один секретарь
            return agreement_lines, sequence, init_approvers

        # ==============================================================
        # Стандартная логика для approver_ids (under_approval, review)
        # ==============================================================
        for approver_id in approver_ids:
            init_approvers.append(approver_id)
            self.action_notify("agreement", approver_id)
            agreement_lines.append(
                Command.create({
                    "sequence": sequence,
                    "model": self._name,
                    "user_id": approver_id.id,
                    "status": "in_progress" if sequence == 1 else "waiting",
                    "need_esp": need_esp,
                })
            )

        # Дополнительные согласующие для under_approval
        if (
            status == 'under_approval'
            and hasattr(self, 'additional_approver_ids')
            and self.additional_approver_ids
        ):
            sequence += 1 if approver_ids else 0
            for user in self.additional_approver_ids:
                if user.id in [u.id for u in init_approvers] or user.id == 2:
                    continue
                init_approvers.append(user)
                agreement_lines.append(
                    Command.create({
                        "sequence": sequence,
                        "model": self._name,
                        "user_id": user.id,
                        "status": (
                            "in_progress"
                            if sequence == 1 and not approver_ids
                            else "waiting"
                        ),
                        "all_approve": True,
                        "need_esp": need_esp,
                    })
                )

        return agreement_lines, sequence, init_approvers

    def _build_approval_medical_examination_lines(self):
        """
        Создаёт 3 линии подписания с ЭЦП для medical_examination:
            seq 1 — esp_signer_id        (ЭЦП в системе, утверждающий)
            seq 2 — medical_worker_id    (ЭЦП на портале, мед. работник)
            seq 3 — employee_id.user_id  (ЭЦП в системе, сотрудник)
        """
        agreement_lines = []
        init_approvers = []
        sequence = 1

        # 1. Утверждающий (esp_signer_id) — ЭЦП в системе
        if hasattr(self, 'esp_signer_id') and self.esp_signer_id:
            signer = self.esp_signer_id
            if signer.id != 2 and signer not in init_approvers:
                init_approvers.append(signer)
                self.action_notify("agreement", signer)
                agreement_lines.append(
                    Command.create({
                        "sequence": sequence,
                        "model": self._name,
                        "user_id": signer.id,
                        "status": "in_progress",
                        "need_esp": True,
                        "all_approve": True,
                    })
                )
                sequence += 1

        # 2. Медицинский работник (medical_worker_id) — ЭЦП на портале
        if hasattr(self, 'medical_worker_id') and self.medical_worker_id:
            partner = self.medical_worker_id
            user = self.env['res.users'].sudo().search([
                ('partner_id', '=', partner.id)
            ], limit=1)
            if user and user.id != 2 and user not in init_approvers:
                init_approvers.append(user)
                agreement_lines.append(
                    Command.create({
                        "sequence": sequence,
                        "model": self._name,
                        "user_id": user.id,
                        "status": "waiting" if agreement_lines else "in_progress",
                        "need_esp": True,
                        "all_approve": True,
                    })
                )
                sequence += 1

        # 3. Сотрудник (employee_id) — ЭЦП в системе
        if (
            hasattr(self, 'employee_id')
            and self.employee_id
            and self.employee_id.user_id
        ):
            emp_user = self.employee_id.user_id
            if emp_user.id != 2 and emp_user not in init_approvers:
                init_approvers.append(emp_user)
                agreement_lines.append(
                    Command.create({
                        "sequence": sequence,
                        "model": self._name,
                        "user_id": emp_user.id,
                        "status": "waiting",
                        "need_esp": True,
                        "all_approve": True,
                    })
                )

        return agreement_lines, sequence, init_approvers

    # ==================================================================
    # Линии из approval_group_ids workflow
    # ==================================================================

    def get_agreement_lines_by_approval_groups(
        self, agreement_lines=None, start=1, init_approvers=None, need_esp=False
    ):
        """Получает согласующих из approval_group_ids workflow."""
        agreement_lines = [] if agreement_lines is None else agreement_lines
        init_approvers = [] if init_approvers is None else init_approvers
        approval_groups = self.state_id.approval_group_ids
        approval_groups = self.filter_connection(approval_groups)
        approval_groups = approval_groups.sorted("sequence")
        seen = set()
        sequence = start
        for approval_group in approval_groups:
            approval_group_users = self.check_group(approval_group.group_ids)
            users = approval_group.user_ids or approval_group_users
            users = users.filtered(
                lambda u: u not in init_approvers and u.id not in seen and u.id != 2
            )
            seen.update(users.ids)
            flag = False
            for user_id in users:
                if sequence == 1:
                    self.action_notify("agreement", user_id)
                flag = True
                agreement_lines.append(
                    Command.create({
                        "sequence": sequence,
                        "model": self._name,
                        "user_id": user_id.id,
                        "status": "in_progress" if sequence == 1 else "waiting",
                        "all_approve": approval_group.all_approve,
                        "need_esp": approval_group.need_esp or need_esp,
                    })
                )
            if flag:
                sequence += 1
        return agreement_lines

    # ==================================================================
    # Согласование: after_script + _process_post_approval
    # ==================================================================

    def get_current_coordinator(self):
        return self.state_agreement_line_ids.filtered(
            lambda line: line.user_id == self.env.user and line.status == "in_progress"
        )

    def after_script(self, next_state, cur_state):
        """
        Обрабатывает согласование через систему (кнопки в UI).
        Вызывается из after_script в XML workflow.

        Подписание ЭЦП — и в бэкенде, и на портале — приходит сюда же:
        /sign_esp фреймворка сохраняет подпись и вызывает action_approve().
        """
        current_coordinator = self.get_current_coordinator()
        # Extension hook: подкласс (например, correspondence_extra) может
        # определить additional_condition() для дополнительной валидации
        # на этом этапе. В базовом модуле метод не определён.
        if hasattr(self, "additional_condition"):
            self.additional_condition()
        if current_coordinator:
            # Помечаем как agreed
            current_coordinator.sudo().status = "agreed"
            current_coordinator.sudo().agreement_date = datetime.now()

            # Записываем в историю (+ номер письма, если подписано ЭЦП)
            state_description = {
                sd[0]: sd[1]
                for sd in self._fields['state']._description_selection(self.env)
            }
            self.add_to_history(
                current_coordinator, state_description.get(cur_state)
            )

            # Удаляем activity текущего пользователя
            self._remove_approval_activity(user_id=self.env.uid)

            # Единая логика перехода
            self._process_post_approval(
                current_coordinator,
                next_state=next_state,
                cur_state=cur_state,
            )
        else:
            raise ValidationError(_("Вы не являетесь текущим согласующим"))
        
    def _process_post_approval(self, coordinator, next_state=None, cur_state=None, from_portal=False):
        """
        Единая логика после согласования — вызывается из after_script
        (кнопка в UI и подпись ЭЦП через /sign_esp, в бэкенде и на портале).

        Алгоритм:
        1. Удаляет параллельных (не all_approve) при той же sequence
        2. Если ещё есть in_progress — остаёмся на cur_state
        3. Активирует следующих по sequence (waiting → in_progress)
        4. Если все согласовали — переходим на next_state через get_agreement_lines

        Активити для следующих подписантов планируется фреймворком
        appstream_approval в _action_approve после выполнения after_script.
        """
        if cur_state is None:
            cur_state = self.state
        if next_state is None:
            next_state = self._get_next_state_after(cur_state)

        # 1. Удаляем параллельных без all_approve
        approvers_to_remove = self.state_agreement_line_ids.filtered(
            lambda line: line.status == "in_progress"
            and line.id != coordinator.id
            and not line.all_approve
        )
        if approvers_to_remove:
            approvers_to_remove.sudo().unlink()

        # 2. Если ещё есть in_progress — остаёмся на текущем статусе
        still_in_progress = self.state_agreement_line_ids.filtered(
            lambda line: line.status == "in_progress" and line.id != coordinator.id
        )
        if still_in_progress:
            self.sudo().write({"state": cur_state})
            return

        # 3. Ищем следующих по sequence (waiting)
        next_sequences = [
            line.sequence for line in self.state_agreement_line_ids
            if line.sequence > coordinator.sequence
        ]
        next_sequence = min(next_sequences) if next_sequences else -1

        next_coordinators = self.state_agreement_line_ids.filtered(
            lambda line: line.status == "waiting"
            and line.id != coordinator.id
            and line.sequence == next_sequence
        )

        if next_coordinators:
            # Активируем следующих подписантов
            for nc in next_coordinators:
                nc.sudo().status = "in_progress"
                self.action_notify("agreement", nc.user_id)
                
            # Планируем activity ТОЛЬКО при портальном подписании.
            # При системном пути activity планируется фреймворком
            # (_action_approve → schedule_activity после after_script).
            if from_portal:
                self._schedule_approval_activity(
                    users=next_coordinators.mapped('user_id')
                )
            self.sudo().write({"state": cur_state})
        else:
            # 4. Все согласовали — переход на следующий статус
            if next_state == "done":
                self.action_notify("approved")
                self.sudo().write({"state": next_state})
                if hasattr(self, "method_on_approved"):
                    self.method_on_approved()
            elif next_state:
                if hasattr(self, "method_in_middle"):
                    self.method_in_middle()
                self.get_agreement_lines(next_state)
            else:
                self.action_notify("approved")
                self.sudo().write({"state": "done"})

    # ==================================================================
    # История согласования
    # ==================================================================

    @api.model
    def add_to_history(self, current_coordinator, state=False, status="Согласовано"):
        """Добавляет запись в историю согласования."""
        # Сотрудник подписывает отказ тем же штатным виджетом sign_esp, что и
        # согласие, — отличить их можно только по флагу, выставленному заранее
        # кнопкой "Отказаться от мед. освидетельствования".
        if (
            status == "Согласовано"
            and getattr(self, "medical_assessment_refusal", False)
            and getattr(self, "employee_id", False)
            and current_coordinator.user_id == self.employee_id.user_id
        ):
            status = "Отказ от мед. освидетельствования"

        if hasattr(self, "state_agreement_history_line_ids"):
            new_status = status
            if state:
                if status in ["Согласовано", "Отклонено", "Уведомлено об ошибке"]:
                    new_status += " на статусе '" + state + "'"
                else:
                    new_status += " со статуса '" + state + "'"
            self.sudo().state_agreement_history_line_ids = [
                Command.create({
                    "model": self._name,
                    "user_id": current_coordinator.user_id.id,
                    "status": new_status,
                    "commentary": current_coordinator.commentary,
                    "agreement_date": current_coordinator.agreement_date,
                    "date": current_coordinator.date,
                    "certificate_start_date": current_coordinator.certificate_start_date,
                    "certificate_end_date": current_coordinator.certificate_end_date,
                    "serial_number": current_coordinator.serial_number,
                    "issued_by": current_coordinator.issued_by,
                    "issued_to": current_coordinator.issued_to,
                    "certificate_status": current_coordinator.certificate_status,
                    "signed": current_coordinator.signed,
                    "qr": current_coordinator.qr,
                    # Поля сертификата: есть и в v3, и в v4.
                    "fio": current_coordinator.fio,
                    "iin": current_coordinator.iin,
                    "bin_": current_coordinator.bin_,
                    "organization": current_coordinator.organization,
                    "certificate_template": current_coordinator.certificate_template,
                    "uuid": current_coordinator.uuid,
                })
            ]


        # ===============================================================
        # Подпись ЭЦП: номер письма и счётчик подписей
        # ===============================================================
        # QR и боковая подпись в файлы больше не вшиваются. После подписи
        # модуль согласования сам кладёт в поле печатную версию: QR на
        # каждой странице, лист «ДОКУМЕНТ УДОСТОВЕРЕН» и номер письма на
        # полях (_esp_copy_stamp_lines). Делать это ещё и здесь — значит
        # получить двойной QR и менять подписанные файлы после подписи.
        if current_coordinator.signed:
            # Номер должен существовать ДО печатной версии: он печатается
            # на ней как "{номер} от {дата}".
            if hasattr(self, '_assign_document_number'):
                self._assign_document_number()
            self.sudo().number_of_esp_signs += 1
    def additional_filter(self, init_approvers=None):
        return init_approvers

    def filter_connection(self, approval_groups=None):
        return approval_groups

    def check_group(self, group_ids):
        return group_ids.user_ids

    def action_notify(self, notif_type, approver_id=None, reason=None):
        """Отправка уведомлений."""
        for record in self:
            user, template = record.create_uid, False
            template = self.env.ref(
                "correspondence.corr_outgoing_mail_template",
                raise_if_not_found=False
            )
            if not template:
                return
            template = template.sudo()

            if notif_type == "agreement":
                user = approver_id
            elif notif_type in ["rework", "done", "approved"]:
                user = record.create_uid

            if user:
                try:
                    template.with_context(
                        object=record,
                        notif_type=notif_type,
                        reason=reason,
                        user=user,
                    ).send_mail(
                        record.id,
                        force_send=True,
                        email_values={
                            'model': None,
                            'res_id': None,
                            'email_to': user.email or user.partner_id.email or '',
                        },
                    )
                except Exception as e:
                    _logger.warning(
                        "Ошибка отправки уведомления пользователю %s: %s",
                        user.name, str(e)
                    )
