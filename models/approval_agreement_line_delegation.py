# -*- coding: utf-8 -*-
from odoo import fields, models

# Только корреспонденция: другие модули на этом же фреймворке живут своей
# жизнью, переписывать их строки согласования не надо.
CORR_MODELS = ("corr.incoming", "corr.outgoing")


class ApprovalAgreementLine(models.Model):
    """
    Передаёт строку согласования заместителю в момент её активации.

    Фреймворк подставляет заместителя только при СОЗДАНИИ строки
    (appstream_approval/models/approval_agreement_line.py, create). Этого мало:
    документ уходит на согласование заранее, а делегацию оформляют позже —
    когда человек уходит в отпуск. Строки, созданные до делегации, остаются на
    нём, и заместитель не видит ни документа, ни кнопок.

    Поэтому перепроверяем делегацию ещё раз в момент, когда строка переходит
    в "В процессе" — то есть когда очередь реально дошла до этого человека.

    Почему подменяется владелец строки, а не разрешается заместителю нажимать
    чужие кнопки: на владельца строки завязано слишком многое.

      * Правило доступа corr.outgoing/corr.incoming пускает к документу по
        state_agreement_line_ids.user_id — без своей строки заместитель
        документ даже не откроет.
      * user_can_approve во фреймворке сверяет env.user со списком владельцев
        in_progress-строк; из него растут все button_*_enabled.
      * Контроллер /sign_esp ищет строку по user_id подписанта. Не найдя,
        он пишет в пустой recordset — молча, без ошибки: сертификат и QR
        никуда не попадут, а документ при этом согласуется.
      * Активность планируется на владельца строки.

    Подмена владельца чинит всё это разом. Проверку "действую за другого"
    пришлось бы протаскивать в каждое из перечисленных мест отдельно, причём
    в /sign_esp — вообще в чужом модуле.
    """

    _inherit = "appstream.approval.agreement.line"

    original_user_id = fields.Many2one(
        "res.users",
        string="Исходный согласующий",
        readonly=True,
        help="Кому строка предназначалась до передачи заместителю.",
    )
    delegated = fields.Boolean(
        string="По делегации",
        readonly=True,
    )

    def write(self, vals):
        res = super().write(vals)
        # Внутренний write ниже статус не трогает, поэтому рекурсии не будет.
        if vals.get("status") == "in_progress":
            self._corr_apply_delegation()
        return res

    def _corr_find_delegate(self, user):
        """
        Активный заместитель пользователя или пустой recordset.

        sudo() здесь обязателен: строку активирует предыдущий согласующий, и
        прав на delegation.line у него, как правило, нет. Во фреймворке тот же
        поиск идёт без sudo — из-за чего подмена при создании строки может
        тихо не сработать и вернуть пустой результат вместо заместителя.
        """
        if not user or "delegation.line" not in self.env:
            return self.env["res.users"]

        today = fields.Date.today()
        delegation = self.env["delegation.line"].sudo().search(
            [
                ("user_id", "=", user.id),
                ("from_date", "<=", today),
                ("to_date", ">=", today),
            ],
            limit=1,
        )
        return delegation.delegator_user_id if delegation else self.env["res.users"]

    def _corr_apply_delegation(self):
        for line in self:
            if line.model not in CORR_MODELS:
                continue
            # delegated: строку уже передавали, второй раз не трогаем —
            # иначе при цепочке делегаций можно уехать по кругу.
            if line.delegated or line.skip_delegation or line.signed:
                continue
            if line.status != "in_progress":
                continue

            substitute = line._corr_find_delegate(line.user_id)
            if not substitute or substitute == line.user_id:
                continue

            # Заместитель уже согласует этот документ сам — вторая строка дала
            # бы ему две кнопки и две активности.
            duplicate = self.sudo().search_count([
                ("model", "=", line.model),
                ("res_id", "=", line.res_id),
                ("user_id", "=", substitute.id),
                ("id", "!=", line.id),
                ("status", "in", ("waiting", "in_progress")),
            ])
            if duplicate:
                continue

            line.sudo().write({
                "original_user_id": line.user_id.id,
                "delegated": True,
                "user_id": substitute.id,
            })
