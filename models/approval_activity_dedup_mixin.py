# -*- coding: utf-8 -*-
from odoo import models


class CorrApprovalActivityDedupMixin(models.AbstractModel):
    """
    Схлопывает дубли активностей «Waiting Approval» — одна на пользователя.

    Почему они появляются. `approval_mixin._action_approve()` внутри одного
    блока `if old_state != self.state:` вызывает `_schedule_approval_activity()`
    ДВАЖДЫ:

        if nextstate.before_script:
            safe_eval(before_script)
            if nextstate.schedule_activity:
                self._schedule_approval_activity()      # <-- первый раз
        if old_state_id.after_script:
            safe_eval(after_script)
            if old_state_id.schedule_activity:
                self._schedule_approval_activity()      # <-- второй раз

    У всех статусов маршрута корреспонденции `schedule_activity = True`,
    поэтому срабатывают оба. А `_schedule_approval_activity()` игнорирует
    переданный список пользователей и всегда берёт текущие строки в статусе
    `in_progress`:

        users3 = self.state_agreement_line_ids.filtered(
            lambda x: x.status == 'in_progress').mapped('user_id')
        if users3:
            users = users3

    При групповом согласовании (несколько согласующих с одинаковой sequence и
    all_approve) документ после первой подписи остаётся на прежнем статусе —
    `_process_post_approval` делает `write({"state": cur_state})`. В итоге оба
    вызова адресуются одному и тому же набору оставшихся согласующих, и каждый
    получает по две активности. С двумя оставшимися это выглядит как четыре
    строки, с одним — как две.

    Чинить точечно нечем: оба вызова в чужом модуле, а отключать
    `schedule_activity` у статуса нельзя — тогда активности не создадутся при
    реальном переходе. Поэтому подчищаем результат: после любого планирования
    оставляем по одной активности на пользователя (самую раннюю по id).

    Миксин добавляется в `_inherit` ПЕРЕД `appstream.approval.mixin`. В Odoo
    приоритет у РАННИХ элементов списка, а не у поздних: при сборке класса
    (`odoo/orm/model_classes.py`) получается
    `bases = [класс_модели] + [родители в порядке _inherit]`, и C3-линеаризация
    отдаёт предпочтение тому, кто идёт раньше. Если поставить миксин после
    `appstream.approval.mixin`, метод фреймворка перекроет наш и
    переопределение окажется мёртвым кодом.

    Остальные миксины корреспонденции стоят после фреймворка без последствий:
    `add_to_history`, `get_agreement_lines`, `after_script`,
    `_process_post_approval`, `action_notify` в `appstream.approval.mixin`
    не определены, перекрывать нечего.
    """

    _name = "corr.approval.activity.dedup.mixin"
    _description = "Дедупликация активностей согласования"

    def _schedule_approval_activity(self, users=None):
        res = super()._schedule_approval_activity(users=users)
        self._dedupe_approval_activities()
        return res

    def _dedupe_approval_activities(self):
        """Оставляет по одной активности согласования на пользователя."""
        activity_type = self.env.ref(
            "appstream_approval.activity_type_approval", raise_if_not_found=False
        )
        if not activity_type:
            return

        Activity = self.env["mail.activity"].sudo()
        for record in self:
            # Активности могут висеть не на самой записи: см.
            # _get_activity_record() во фреймворке.
            activity_record = record._get_activity_record()
            if not activity_record:
                continue

            activities = Activity.search(
                [
                    ("res_model", "=", activity_record._name),
                    ("res_id", "=", activity_record.id),
                    ("activity_type_id", "=", activity_type.id),
                ],
                order="id",
            )

            seen_user_ids = set()
            duplicates = Activity
            for activity in activities:
                if activity.user_id.id in seen_user_ids:
                    duplicates |= activity
                else:
                    seen_user_ids.add(activity.user_id.id)

            if duplicates:
                duplicates.unlink()
