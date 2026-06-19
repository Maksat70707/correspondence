# -*- coding: utf-8 -*-
"""
Override _get_document_to_sign для моделей correspondence.

Дефолтная реализация в appstream_approval отдаёт первый файл из
cur_record.attachment_ids (M2M через mail.thread). Для corr.outgoing
это не то, что нужно: сигнинг-файл лежит либо в attachment_to_sign_ids,
либо генерируется из шаблонного отчёта по типу документа. Делегируем
на сам record через _get_signing_document().
"""
import logging
from odoo.addons.appstream_approval.controllers.sign_esp import SignEsp

_logger = logging.getLogger(__name__)

_CORRESPONDENCE_MODELS = ('corr.outgoing',)


class CorrespondenceSignEsp(SignEsp):

    def _get_document_to_sign(self, cur_record):
        if cur_record._name not in _CORRESPONDENCE_MODELS:
            return super()._get_document_to_sign(cur_record)

        if not hasattr(cur_record, '_get_signing_document'):
            _logger.warning(
                "Model %s in correspondence scope but lacks _get_signing_document()",
                cur_record._name,
            )
            return False

        try:
            return cur_record._get_signing_document()
        except Exception:
            _logger.exception(
                "Failed to prepare signing document for %s(%s)",
                cur_record._name, cur_record.id,
            )
            return False