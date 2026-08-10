from odoo import _, models, Command, fields, api
from datetime import datetime
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class CorrIncomingApproveProcessMixin(models.AbstractModel):
    _name = "corr.incoming.approve.process.mixin"
    _description = "Correspondence Incoming approve process mixin"

    def get_agreement_lines(self, state="review"):
        """
        Создаёт список согласующих и переводит в указанный статус.
        
        Этапы с согласованием: review, report
        Этапы без согласования: execution, rework, revision
        """
        if hasattr(self, "method_on_start"):
            self.method_on_start()

        # Удаляем старые линии.
        # sudo() обязателен: по ir.model.access.csv у секретаря на
        # appstream.approval.agreement.line unlink=0, а у сотрудника
        # ещё и create=0. Без него отправка входящего на согласование
        # падает с AccessError у всех, кроме админа.
        self.sudo().state_agreement_line_ids.unlink()

        # Этапы без согласования — просто переводим в статус
        if state in ('execution', 'rework', 'revision'):
            return self.sudo().write({"state": state})

        # Этапы с согласованием (review, report)
        agreement_lines = []

        # Получаем согласующих до основных групп (для переопределения)
        before_approval_groups_agreement_lines, sequence, init_approvers = (
            self.get_agreement_lines_by_before_approval_groups(state)
        )
        agreement_lines = before_approval_groups_agreement_lines
        start = sequence + 1 if before_approval_groups_agreement_lines else sequence

        # Получаем согласующих из групп в настройках workflow
        approval_groups_agreement_lines = self.get_agreement_lines_by_approval_groups(
            start=start, init_approvers=init_approvers
        )
        agreement_lines += approval_groups_agreement_lines

        # Назначаем согласующих (sudo() — см. комментарий к unlink выше)
        self.sudo().state_agreement_line_ids = agreement_lines
        
        # Принудительно сохраняем записи
        self.env.flush_all()

        # Активируем первых согласующих (меняем waiting -> in_progress для sequence=1)
        if hasattr(self, "check_agreement_line_status"):
            self.check_agreement_line_status()
            self.env.flush_all()

        # НЕ меняем state здесь - это делает workflow или вызывающий код
        return True

    def get_agreement_lines_by_before_approval_groups(
        self, state=None, agreement_lines=None, sequence=1, init_approvers=None
    ):
        """
        Получает согласующих ДО основных групп.
        Можно переопределить для добавления специфичных согласующих.
        """
        agreement_lines = [] if agreement_lines is None else agreement_lines
        init_approvers = [] if init_approvers is None else init_approvers
        return agreement_lines, sequence, init_approvers

    def get_agreement_lines_by_approval_groups(
        self, agreement_lines=None, start=1, init_approvers=None
    ):
        """
        Получает согласующих из групп, настроенных в workflow.
        """
        agreement_lines = [] if agreement_lines is None else agreement_lines
        init_approvers = [] if init_approvers is None else init_approvers

        # Получаем группы согласующих из текущего state_id
        if not self.state_id or not self.state_id.approval_group_ids:
            return agreement_lines

        approval_groups = self.state_id.approval_group_ids
        approval_groups = self.filter_connection(approval_groups)
        approval_groups = approval_groups.sorted("sequence")

        seen = set()
        sequence = start

        for approval_group in approval_groups:
            # Получаем пользователей из группы
            approval_group_users = self.check_group(approval_group.group_ids)
            users = approval_group.user_ids or approval_group_users

            # Фильтруем уже добавленных и системных пользователей
            users = users.filtered(
                lambda u: u not in init_approvers and u.id not in seen and u.id != 2
            )
            seen.update(users.ids)

            flag = False
            for user_id in users:
                flag = True
                # Все создаются со статусом waiting, check_agreement_line_status потом активирует первых
                agreement_lines.append(
                    Command.create(
                        {
                            "sequence": sequence,
                            "model": self._name,
                            "user_id": user_id.id,
                            "status": "waiting",
                            "all_approve": approval_group.all_approve,
                            "need_esp": approval_group.need_esp,
                        },
                    )
                )
            if flag:
                sequence += 1

        return agreement_lines

    def get_current_coordinator(self):
        """Возвращает текущего согласующего (текущий пользователь)"""
        return self.state_agreement_line_ids.filtered(
            lambda line: line.user_id == self.env.user and line.status == "in_progress"
        )

    def after_script(self, next_state, cur_state):
        """
        Универсальный метод обработки согласования.
        Вызывается из workflow after_script.
        
        :param next_state: следующий статус после полного согласования
        :param cur_state: текущий статус (для записи в историю)
        """
        # Находим текущего согласующего
        current_coordinator = self.get_current_coordinator()
        
        # Extension hook: подкласс (например, correspondence_extra) может
        # определить additional_condition() для дополнительной валидации
        # на этом этапе. В базовом модуле метод не определён.
        if hasattr(self, "additional_condition"):
            self.additional_condition()
        
        if current_coordinator:
            # Помечаем как согласовано
            current_coordinator.status = "agreed"
            current_coordinator.agreement_date = datetime.now()
            
            # Добавляем в историю
            state_description = {
                state_desc[0]: state_desc[1]
                for state_desc in self._fields['state']._description_selection(self.env)
            }
            self.add_to_history(current_coordinator, state_description.get(cur_state))
            
            # Удаляем других согласующих с тем же sequence, если all_approve=False
            approvers_to_remove = self.state_agreement_line_ids.filtered(
                lambda line: line.status == "in_progress"
                and line.id != current_coordinator.id
                and not line.all_approve
            )
            if approvers_to_remove:
                approvers_to_remove.unlink()
            
            # Проверяем, есть ли ещё согласующие in_progress (параллельное согласование)
            for line in self.state_agreement_line_ids:
                if line.status == "in_progress" and line.id != current_coordinator.id:
                    # Остаёмся на текущем этапе
                    self.write({"state": cur_state})
                    return
            
            # Ищем следующих согласующих в очереди
            next_sequences = [
                line.sequence for line in self.state_agreement_line_ids
                if line.sequence > current_coordinator.sequence
            ]
            next_sequence = min(next_sequences) if next_sequences else -1
            
            next_coordinators = self.state_agreement_line_ids.filtered(
                lambda line: line.status == "waiting"
                and line.id != current_coordinator.id
                and line.sequence == next_sequence
            )
            
            if next_coordinators:
                # Активируем следующих согласующих
                for next_coordinator in next_coordinators:
                    next_coordinator.status = "in_progress"
                self.env.flush_all()
                
                # Остаёмся на текущем этапе
                # Activity создастся автоматически через _schedule_approval_activity в appstream_approval
                self.write({"state": cur_state})
            else:
                # Все согласовали — переходим на следующий этап
                if next_state == "done":
                    # Финальное согласование — уведомляем инициатора
                    self.action_notify("approved")
                    self.write({"state": next_state})
                    # Вызываем хук после полного согласования
                    if hasattr(self, "method_on_approved"):
                        self.method_on_approved()
                elif next_state == "execution":
                    # Переход на исполнение — проверяем задания
                    if not self.assignment_line_ids:
                        raise ValidationError(
                            _("Невозможно перейти на исполнение без заданий. Создайте хотя бы одно задание.")
                        )
                    # Activity для исполнителей создаётся в correspondence_assignment_line
                    self.write({"state": next_state})
                else:
                    # Промежуточный переход — запускаем новое согласование
                    if hasattr(self, "method_in_middle"):
                        self.method_in_middle()
                    self.get_agreement_lines(next_state)
        else:
            raise ValidationError(_("Вы не являетесь текущим согласующим"))

    @api.model
    def add_to_history(self, current_coordinator, state=False, status="Согласовано"):
        """Добавляет запись в историю согласований"""
        if hasattr(self, "state_agreement_history_line_ids"):
            new_status = status
            if state:
                if status in ["Согласовано", "Отклонено", "Уведомлено об ошибке"]:
                    new_status += " на статусе '" + state + "'"
                else:
                    new_status += " со статуса '" + state + "'"

            # sudo(): у секретаря и сотрудника create=0 на
            # appstream.approval.agreement.history.line. В исходящих
            # это уже сделано, здесь было упущено.
            self.sudo().state_agreement_history_line_ids = [
                Command.create(
                    {
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
                        # Поля, добавленные в appstream_approval v4.
                        # Их читает страница /signature_uuid/<uuid> — без переноса
                        # она отрендерится с пустыми ФИО / ИИН / организацией.
                        "fio": current_coordinator.fio,
                        "iin": current_coordinator.iin,
                        "bin_": current_coordinator.bin_,
                        "organization": current_coordinator.organization,
                        "certificate_template": current_coordinator.certificate_template,
                        "uuid": current_coordinator.uuid,
                    },
                )
            ]

    def additional_filter(self, init_approvers=None):
        """Дополнительная фильтрация согласующих (для переопределения)"""
        return init_approvers

    def filter_connection(self, approval_groups=None):
        """Фильтрация по связи (для переопределения)"""
        return approval_groups

    def check_group(self, group_ids):
        """Получает пользователей из групп безопасности"""
        return group_ids.user_ids

    def action_notify(self, notif_type, approver_id=None, reason=None):
        """
        Отправляет уведомления (email/сообщение).
        Activity для согласующих создаётся через _schedule_approval_activity в appstream_approval.
        """
        for record in self:
            user = record.create_uid
            template = False

            # Получаем шаблон уведомления
            try:
                template = self.env.ref("correspondence.corr_incoming_mail_template")
            except Exception:
                _logger.warning("Mail template 'correspondence.corr_incoming_mail_template' not found")
                return


            # Определяем получателя
            if notif_type in ("agreement", "execution"):
                user = approver_id
            elif notif_type == "approved":
                user = record.create_uid

            if not user:
                continue

            # Создаём activity только для исполнителей (не для согласующих!)
            # Activity для согласующих создаётся через _schedule_approval_activity
            if notif_type == "execution":
                record._create_activity_for_user(user, notif_type, reason)

            # Отправляем email/сообщение
            template.with_context(
                object=record, notif_type=notif_type, reason=reason, user=user
            ).send_mail(
                record.id,
                force_send=True,
                email_values={
                    'model': None,
                    'res_id': None,
                    'email_to': user.email or user.partner_id.email or '',
                },
            )

    def _create_activity_for_user(self, user, notif_type, reason=None):
        """Создаёт activity (действие) для пользователя"""
        self.ensure_one()

        # execution-активити управляются через _sync_execution_activities
        # (Вариант B миграции: одна активити на пару (документ, юзер),
        # вместо одной на каждую строку задания)
        if notif_type == "execution":
            self._sync_execution_activities()
            return

        activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        if not activity_type:
            _logger.warning("Activity type 'mail.mail_activity_data_todo' not found")
            return

        # Определяем текст activity в зависимости от типа
        if notif_type == "approved":
            summary = _("Документ согласован")
            note = _("Документ успешно прошёл согласование.")
        elif notif_type == "rework":
            summary = _("Документ возвращён на доработку")
            note = reason or _("Требуется внести исправления.")
        else:
            summary = _("Требуется ваше действие")
            note = reason or ""
        
        # Создаём activity
        self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=user.id,
            summary=summary,
            note=note,
        )