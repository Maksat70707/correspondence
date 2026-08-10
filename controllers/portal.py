# -*- coding: utf-8 -*-

import base64
import logging
from werkzeug.exceptions import NotFound
import json
import requests
from datetime import datetime, timezone

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

    Подписание ЭЦП происходит через эндпоинты appstream_approval:
      • /esp/get_sign_obj/<id>     — получить документ для подписи
      • /sign_esp                  — верификация и регистрация подписи
      • /esp/save_signed_pdf/<id>  — сохранение подписанной CMS
    Никакого NCANode и кастомного XML-флоу — всё локально через cryptography.
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

    @http.route(['/correspondence/sign/esp/success'], type='jsonrpc', auth="user", website=True)
    def sign_esp_success(self, **kwargs):
        """Обработка успешного подписания ЭЦП с портала (через NCANode)"""
        data = json.loads(request.httprequest.data.decode('utf-8'))

        document_id = int(data.get('document_id'))
        uid = int(data.get('uid'))  # partner_id
        xml_signature = data.get('xml')

        document = request.env['corr.outgoing'].sudo().browse(document_id)

        if not document.exists():
            return {"status": 500, "message": "Документ не найден!"}

        # Извлекаем сертификат из XML
        xml_split = xml_signature.split("X509Certificate>", 1)[1]
        x509 = xml_split[0:xml_split.index("</")]

        # Проверяем сертификат через NCANode
        params = json.dumps({
            "revocationCheck": ["OCSP"],
            "certs": [x509.replace('\n', '')]
        })

        ncanode_link = request.env['ir.config_parameter'].sudo().get_param('appstream_approval.ncanode_link')
        if not ncanode_link:
            return {"status": 500, "message": "NCANode не настроен в системе"}

        try:
            req = requests.post(
                ncanode_link,
                params,
                headers={'Content-Type': 'application/json', 'accept': 'application/json'}
            )
            result = json.loads(req.content)

            if result['status'] != 200:
                return {"status": result['status'], "message": result.get('message', 'Ошибка проверки')}

            signer = result['signers'][0]

            if not signer['valid']:
                return {"status": 500, "message": "ЭЦП ключ недействителен!"}

            start_date = datetime.fromisoformat(signer['notBefore'])
            end_date = datetime.fromisoformat(signer['notAfter'])
            now = datetime.now(timezone.utc)

            if not start_date < now < end_date:
                return {"status": 500, "message": "Срок ключа ЭЦП истек!"}

            # Проверяем ИИН/БИН
            partner = request.env['res.partner'].sudo().browse(uid)
            partner_iin = partner.vat

            ins = []
            if 'iin' in signer['subject']:
                ins.append(signer['subject']['iin'])
            if 'bin' in signer['subject']:
                ins.append(signer['subject']['bin'])

            if not partner_iin or partner_iin not in ins:
                return {
                    'status': 500,
                    'message': "ИИН/БИН ключа ЭЦП не совпадает с данными в профиле!"
                }

            certificate_data = {
                'serial_number': signer['serialNumber'],
                'start_date': start_date.date(),
                'end_date': end_date.date(),
                'issued_by': signer['issuer']['dn'],
                'issued_to': signer['subject']['dn'],
                'valid': signer['valid'],
            }

            user = request.env['res.users'].sudo().search([('partner_id', '=', uid)], limit=1)
            if not user:
                return {"status": 500, "message": "Пользователь не найден!"}

            result = document.portal_sign_esp(user.id, certificate_data, xml_signature)

            if result.get('status') == 200:
                result['redirect_url'] = '/correspondence/signed/success'

            return result

        except Exception as e:
            _logger.exception("Error in sign_esp_success")
            return {"status": 500, "message": str(e)}