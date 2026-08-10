# -*- coding: utf-8 -*-

import logging

from odoo.addons.appstream_approval.controllers.sign_esp import SignEsp

_logger = logging.getLogger(__name__)


class CorrespondenceSignEsp(SignEsp):
    """
    Переопределяет хук `_get_document_to_sign` из appstream_approval.

    Зачем
    -----
    Базовый `/esp/get_sign_obj` определяет тип подписи так:

        approval_group_id = getattr(cur_record, "approval_group_id", False)
        esp_type = approval_group_id.esp_type if approval_group_id else "xml"

    У `corr.outgoing` поля `approval_group_id` нет, поэтому esp_type всегда
    получался "xml", и NCALayer подписывал `generate_signable_xml()` — дамп
    полей записи, а не сам документ из `attachment_to_sign_ids`.

    Хук `_get_document_to_sign()` в базовом контроллере оставлен пустым
    (`return False`) именно для таких переопределений: если он возвращает
    данные, ветка выбирает esp_type="cms" и подписывает их напрямую.

    Логика цепочки подписей
    -----------------------
    Второй и последующие подписанты должны до-подписывать УЖЕ существующую CMS,
    а не исходный файл — иначе `/esp/save_signed_pdf` перезапишет вложение и
    подпись предыдущего подписанта потеряется. Поэтому модельный метод
    `_get_esp_document_to_sign()` сначала отдаёт сохранённую CMS из
    `appstream.approval.esp` (так же, как это делает базовый `get_pdf_report`).
    """

    def _get_document_to_sign(self, cur_record):
        if hasattr(cur_record, "_get_esp_document_to_sign"):
            try:
                document = cur_record._get_esp_document_to_sign()
            except Exception:
                _logger.exception(
                    "Не удалось получить документ для подписи ЭЦП (%s, id=%s)",
                    cur_record._name, cur_record.id,
                )
                document = False
            if document:
                return document
        return super()._get_document_to_sign(cur_record)
