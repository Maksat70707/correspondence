"""Подписанты писем подписывают файлы, а не данные записи.

Раньше QR и боковая подпись вшивались в «Исходящее письмо для подписания» и
«Дополнительные документы» кодом модуля. Теперь каждый файл подписывается
отдельно («XML с хешем документа»), а в поле после подписи встаёт печатная
версия модуля согласования — с QR на каждой странице, листом «ДОКУМЕНТ
УДОСТОВЕРЕН» и номером письма на полях.

Настройки этапов и групп — НАЧАЛЬНЫЕ: дальше их ведёт администратор, поэтому
в данных модуля их нет (при noupdate=0 обновление затирало бы правки). Этап
или группу, где уже что-то указано, не трогаем.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

STAGES = ("correspondence.corr_outgoing_workflow_approval",
          "correspondence.corr_outgoing_workflow_approval_medical_examination")

FIELDS = ("attachment_to_sign_ids", "attachment_additional_sign_ids")


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # ir.model.fields ищем через _get, а не по xmlid field_corr_outgoing__…:
    # имя xmlid — деталь реализации ORM, а _get работает и без него.
    ModelFields = env["ir.model.fields"]
    fields = ModelFields
    for name in FIELDS:
        field = ModelFields._get("corr.outgoing", name)
        if field:
            fields |= field
        else:
            _logger.warning("Поле corr.outgoing.%s не найдено — пропускаем", name)
    if not fields:
        return

    values = {"esp_type": "xml_doc", "esp_document_field_ids": [(6, 0, fields.ids)]}

    # Групп согласующих у корреспонденции в данных модуля нет (строки строит
    # код модели), но администратор мог завести их руками.
    groups = env["appstream.approval.group"].with_context(active_test=False).search(
        [("model_id.model", "=", "corr.outgoing")])
    for group in groups:
        if not group.esp_document_field_ids:
            group.write(values)

    for xmlid in STAGES:
        stage = env.ref(xmlid, raise_if_not_found=False)
        if stage and not stage.esp_type and not stage.esp_document_field_ids:
            stage.write(values)
        elif not stage:
            _logger.warning("Этап %s не найден — настройки ЭЦП не заданы", xmlid)
