"""Начальные настройки ЭЦП этапов исходящей корреспонденции.

Подписанты писем подписывают файлы, а не данные записи: каждый файл
отдельно («XML с хешем документа»), после подписи в поле встаёт печатная
версия модуля согласования — с QR на каждой странице, листом «ДОКУМЕНТ
УДОСТОВЕРЕН» и номером письма на полях.

Настройки — НАЧАЛЬНЫЕ: дальше их ведёт администратор, поэтому в XML-данных
модуля их нет (при noupdate=0 обновление затирало бы правки). Этап или
группу, где уже что-то указано, не трогаем.

Применяются в двух местах:
  * post_init_hook — на чистой установке. Скрипты миграции на установке не
    выполняются, и без хука новая база (тестовая, вторая компания,
    восстановление) подписывала бы данные записи вместо файла письма, не
    подставляла бы файл в подпись и не ставила бы печатную версию в поле;
  * migrations/19.0.1.0.1 — на существующих базах.
"""
import logging

_logger = logging.getLogger(__name__)

ESP_STAGES = (
    "correspondence.corr_outgoing_workflow_approval",
    "correspondence.corr_outgoing_workflow_approval_medical_examination",
)

ESP_FIELDS = ("attachment_to_sign_ids", "attachment_additional_sign_ids")


def apply_initial_esp_settings(env):
    """Тип подписи и поля с файлами на подпись для этапов и групп писем."""
    # ir.model.fields ищем через _get, а не по xmlid field_corr_outgoing__…:
    # имя xmlid — деталь реализации ORM, а _get работает и без него.
    ModelFields = env["ir.model.fields"]
    fields = ModelFields
    for name in ESP_FIELDS:
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

    for xmlid in ESP_STAGES:
        stage = env.ref(xmlid, raise_if_not_found=False)
        if stage and not stage.esp_type and not stage.esp_document_field_ids:
            stage.write(values)
            _logger.info("Настройки ЭЦП этапа %s заданы: подписываются файлы письма", xmlid)
        elif not stage:
            _logger.warning("Этап %s не найден — настройки ЭЦП не заданы", xmlid)


def post_init_hook(env):
    apply_initial_esp_settings(env)
