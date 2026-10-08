# -*- coding: utf-8 -*-

from odoo import models, Command, fields

import logging

_logger = logging.getLogger(__name__)


class PortalSigningMixin(models.AbstractModel):
    """
    Миксин для подписания документов портальными пользователями (внешними
    контрагентами).

    Основной сценарий: medical_worker_id подписывает с ЭЦП на портале как
    3-й подписант в статусе approval_medical_examination.

    Саму подпись с 19.0.0.2 ведёт appstream_approval: кнопка
    o_esp_portal_sign на портальной странице открывает штатный диалог
    (предпросмотр файла, NCALayer или QR для eGov Mobile) и уходит в
    /sign_esp. Там проверяется подпись, ИИН/БИН ключа сверяется с
    карточкой контрагента, подпись сохраняется в «Подписи ЭЦП», а затем
    вызывается action_approve() — то есть ровно тот же путь, что у
    подписанта в бэкенде: after_script -> add_to_history ->
    _process_post_approval.

    Здесь остаётся только то, что знает модель: кто подписант, как
    завести ему строку согласования и как это показать.
    """
    _name = "portal.signing.mixin"
    _description = "Portal Signing Mixin"

    portal_sign_skip = fields.Boolean(
        string="Подписание на другой платформе",
        default=False,
        help="Если включено, портальные пользователи не подписывают в Odoo"
    )

    def _get_portal_signers(self):
        """
        Возвращает список партнёров (res.partner), которые должны подписать документ.
        Переопределите этот метод в наследующей модели.
        """
        return self.env['res.partner']

    def _get_portal_signers_users(self):
        """Возвращает пользователей (res.users) для партнёров-подписантов."""
        partners = self._get_portal_signers()
        users = self.env['res.users']
        for partner in partners:
            user = self.env['res.users'].sudo().search([
                ('partner_id', '=', partner.id)
            ], limit=1)
            if user:
                users |= user
        return users

    def _create_portal_signing_lines(self, state='sign_portal', sequence=1):
        """
        Создаёт agreement lines для портальных подписантов.
        Returns: (agreement_lines, sequence, init_approvers)
        """
        agreement_lines = []
        init_approvers = []

        if self.portal_sign_skip:
            executor = getattr(self, 'user_id', None) or self.create_uid
            if executor:
                init_approvers.append(executor)
                agreement_lines.append(
                    Command.create({
                        "sequence": sequence,
                        "model": self._name,
                        "user_id": executor.id,
                        "status": "in_progress",
                        "need_esp": False,
                    })
                )
            return agreement_lines, sequence, init_approvers

        portal_users = self._get_portal_signers_users()
        for user in portal_users:
            if user.id == 2:
                continue
            init_approvers.append(user)
            self.action_notify("agreement", user)
            agreement_lines.append(
                Command.create({
                    "sequence": sequence,
                    "model": self._name,
                    "user_id": user.id,
                    "status": "in_progress",
                    "need_esp": True,
                    "all_approve": True,
                })
            )
        return agreement_lines, sequence, init_approvers

    def get_portal_signing_url(self):
        """Возвращает URL для портального подписания."""
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f"{base_url}/my/correspondence/{self.id}"

    def is_current_user_portal_signer(self):
        """Проверяет, является ли текущий пользователь портальным подписантом."""
        self.ensure_one()
        return self.state_agreement_line_ids.filtered(
            lambda l: l.user_id == self.env.user and l.status == 'in_progress'
        )

    def has_user_signed(self, user_id=None):
        """Проверяет, подписал ли пользователь документ."""
        self.ensure_one()
        if user_id is None:
            user_id = self.env.user.id
        return bool(self.state_agreement_line_ids.filtered(
            lambda l: l.user_id.id == user_id and l.status == 'agreed'
        ))
