# -*- coding: utf-8 -*-

from odoo import _, models, Command, fields, api
from odoo.exceptions import ValidationError
from datetime import datetime
import base64
import qrcode
from io import BytesIO

import logging

_logger = logging.getLogger(__name__)


class PortalSigningMixin(models.AbstractModel):
    """
    Миксин для подписания документов портальными пользователями (внешними контрагентами).

    Основной сценарий: medical_worker_id подписывает с ЭЦП на портале
    как 3-й подписант в статусе approval_medical_examination.

    Подписание обрабатывается через единую логику _process_post_approval,
    что гарантирует корректный переход между подписантами и статусами.
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

    def portal_sign_esp(self, user_id, certificate_data, xml_signature):
        """
        Обрабатывает подписание через портал.
        Вызывается из контроллера после успешной проверки ЭЦП.

        Унифицировано с системным подписанием:
        1. Находит agreement_line текущего подписанта
        2. Записывает данные сертификата и QR
        3. Добавляет в историю (+ вшивает ЭЦП в файл)
        4. Вызывает _process_post_approval для корректного перехода

        Returns: dict с status и message
        """
        self.ensure_one()

        # 1. Находим agreement line для этого пользователя
        approver = self.state_agreement_line_ids.filtered(
            lambda l: l.user_id.id == user_id and l.status == 'in_progress'
        )
        if not approver:
            return {
                'status': 500,
                'message': "Вы не текущий согласующий или уже подписали документ!"
            }

        # 2. Генерируем QR код
        qr_image = self._generate_signature_qr(user_id)

        # 3. Записываем данные сертификата
        approver.sudo().write({
            'serial_number': certificate_data.get('serial_number'),
            'certificate_start_date': certificate_data.get('start_date'),
            'certificate_end_date': certificate_data.get('end_date'),
            'issued_by': certificate_data.get('issued_by'),
            'issued_to': certificate_data.get('issued_to'),
            'certificate_status': 'Валидный' if certificate_data.get('valid') else 'Невалидный',
            'signed': True,
            'qr': qr_image,
            'status': 'agreed',
            'agreement_date': datetime.now(),
        })

        # 4. Добавляем в историю с меткой текущего статуса
        #    (add_to_history также вшивает ЭЦП в файл через _embed_esp_in_attachments)
        if hasattr(self, 'add_to_history'):
            state_description = {
                sd[0]: sd[1]
                for sd in self._fields['state']._description_selection(self.env)
            }
            cur_state_label = state_description.get(self.state, '')
            self.sudo().add_to_history(approver, cur_state_label)

        # 5. Удаляем activity текущего подписанта
        self._remove_approval_activity(user_id=user_id)

        # 6. Используем единую логику перехода:
        #    _process_post_approval обрабатывает:
        #    - удаление non-all_approve параллельных
        #    - проверку оставшихся in_progress
        #    - активацию следующих waiting → in_progress
        #    - переход на следующий статус когда все подписали
        if hasattr(self, '_process_post_approval'):
            self.sudo()._process_post_approval(approver, from_portal=True)
        else:
            # Fallback если _process_post_approval не определён
            remaining = self.state_agreement_line_ids.filtered(
                lambda l: l.status in ('in_progress', 'waiting')
            )
            if not remaining:
                self._portal_signing_complete()

        return {
            'status': 200,
            'message': "Документ успешно подписан!"
        }

    def _generate_signature_qr(self, user_id):
        """Генерирует QR-код для подписи."""
        config_parameter = self.env['ir.config_parameter'].sudo().search_read(
            [('key', '=', 'web.base.url')], ['value']
        )
        base_url = config_parameter[0]['value'] if config_parameter else 'http://localhost:8069'

        data = f'{base_url}/signature/{self._name}/{self.id}/{user_id}'

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4
        )
        qr.add_data(data)
        qr.make(fit=True)

        temp = BytesIO()
        img = qr.make_image(fill_color='black', back_color='white')
        img.save(temp, format="PNG")
        return base64.b64encode(temp.getvalue())

    def _portal_signing_complete(self):
        """
        Вызывается когда все портальные пользователи подписали документ.
        Переопределяется в наследующей модели.
        """
        pass

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
