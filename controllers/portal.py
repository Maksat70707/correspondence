# -*- coding: utf-8 -*-

import base64
import logging
from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import pager as portal_pager
from odoo.addons.portal.controllers import portal

_logger = logging.getLogger(__name__)


class CustomerPortal(portal.CustomerPortal):
    """Расширение портала для корреспонденции"""

    def _prepare_home_portal_values(self, counters):
        """Добавляем счётчик корреспонденции на портал"""
        values = super()._prepare_home_portal_values(counters)
        user = request.env.user
        partner = user.partner_id

        # Считаем документы где текущий партнёр — медицинский работник
        values['correspondence_count'] = request.env['corr.outgoing'].sudo().search_count([
            ('medical_worker_id', '=', partner.id),
            ('state', 'in', ['approval_medical_examination', 'processing', 'review', 'done']),
        ])
        return values


class CorrespondencePortalController(http.Controller):
    """
    Контроллер для работы с корреспонденцией через портал.

    Подписание ЭЦП своего кода здесь больше НЕ имеет. С appstream_approval
    19.0.0.2 портальная подпись входит в сам фреймворк: кнопка с классом
    o_esp_portal_sign (portal_sign_esp.js в web.assets_frontend) открывает
    тот же диалог, что и в бэкенде, и уходит в штатный /sign_esp.

    Свой маршрут /correspondence/sign/esp/success убран: он слал POST прямо
    по appstream_approval.ncanode_link со старым телом {"certs": [...]}, а в
    19.0.0.2 этот параметр хранит БАЗОВЫЙ адрес сервиса — методы теперь
    /xml/verify и /cms/verify. Тот запрос ушёл бы в корень NCANode.

    Что даёт штатный путь взамен: предпросмотр файла до подписи (ст. 50 п. 3
    подп. 1 Цифрового кодекса), QR для eGov Mobile — медработнику NCALayer
    ставить не нужно, — сверка ИИН/БИН ключа с карточкой контрагента,
    проверка самой подписи, а не только сертификата, и сохранение подписи в
    «Подписи ЭЦП» со страницей проверки.
    """

    @http.route(['/my/correspondence', '/my/correspondence/page/<int:page>'],
                type='http', auth="user", website=True)
    def portal_my_correspondence(self, page=1, **kw):
        """Список документов корреспонденции для портального пользователя"""
        user = request.env.user
        partner = user.partner_id

        correspondence_obj = request.env['corr.outgoing'].sudo()
        domain = [
            ('medical_worker_id', '=', partner.id),
            ('state', 'in', ['approval_medical_examination', 'processing', 'review', 'done']),
        ]

        count = correspondence_obj.search_count(domain)
        records_per_page = 20

        pager = portal_pager(
            url="/my/correspondence",
            total=count,
            page=page,
            step=records_per_page,
        )

        documents = correspondence_obj.search(
            domain,
            offset=pager['offset'],
            order="id desc",
            limit=records_per_page,
        )

        return request.render("correspondence.portal_correspondence_list", {
            'documents': documents,
            'pager': pager,
            'page_name': 'correspondence',
        })

    @http.route(['/my/correspondence/<int:document_id>'],
                type='http', auth='user', website=True)
    def portal_correspondence_detail(self, document_id):
        """Детальный просмотр документа корреспонденции"""
        document = request.env['corr.outgoing'].sudo().browse(document_id)
        current_partner = request.env.user.partner_id

        # Проверка доступа
        if not document.exists() or document.medical_worker_id != current_partner:
            raise NotFound()

        # Проверяем подписан ли уже и может ли сейчас подписать
        signed = True
        can_sign = False
        for agreement_line in document.state_agreement_line_ids:
            if agreement_line.user_id.id == request.env.user.id:
                if agreement_line.status == 'agreed':
                    signed = True
                    can_sign = False
                elif agreement_line.status == 'in_progress':
                    signed = False
                    can_sign = True
                else:
                    # waiting — ещё не их очередь
                    signed = False
                    can_sign = False
                break

        return request.render('correspondence.portal_correspondence_detail', {
            'document': document,
            'signed': signed,
            'can_sign': can_sign,
            'current_partner': current_partner,
            'page_name': 'correspondence_detail',
        })

    @http.route(['/correspondence/signed/success'], type='http', auth="user", website=True)
    def correspondence_signed_success(self):
        """Страница успешного подписания"""
        return request.render("correspondence.portal_correspondence_signed", {
            'page_name': 'correspondence_signed',
        })

    @http.route('/correspondence/download/file/<int:attachment_id>',
                type='http', auth='user')
    def download_attachment(self, attachment_id):
        """
        Скачивание вложения портальным пользователем.

        Безопасность: проверяем что
        1. Вложение существует
        2. Привязано к документу corr.outgoing
        3. Документ принадлежит текущему партнёру (он медработник по нему)
        4. Вложение действительно лежит на одном из полей подписания этого документа

        Без этих проверок auth='public' + record.sudo().datas давал
        классический IDOR — любой неаутентифицированный пользователь, перебирая
        id, мог скачать любое ir.attachment в системе.
        """
        att = request.env['ir.attachment'].sudo().browse(attachment_id)
        if not att.exists():
            raise NotFound()

        # Вложение должно быть от исходящего документа корреспонденции
        if att.res_model != 'corr.outgoing':
            raise NotFound()

        document = request.env['corr.outgoing'].sudo().browse(att.res_id)
        if not document.exists():
            raise NotFound()

        # Текущий пользователь должен быть медработником этого документа
        if document.medical_worker_id != request.env.user.partner_id:
            raise NotFound()

        # И вложение должно быть в одном из полей подписания этого документа
        # (а не любое привязанное к этому corr.outgoing — там может быть что-то ещё)
        allowed_ids = (
            document.attachment_to_sign_ids.ids
            + document.attachment_additional_sign_ids.ids
        )
        if att.id not in allowed_ids:
            raise NotFound()

        file_content = base64.b64decode(att.datas)
        filename = att.name

        headers = [
            ('Content-Type', 'application/octet-stream'),
            ('Content-Disposition', http.content_disposition(filename)),
            ('Content-Length', len(file_content)),
        ]

        return request.make_response(file_content, headers=headers)
