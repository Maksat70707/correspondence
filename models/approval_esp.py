# -*- coding: utf-8 -*-
import base64

from odoo import models


class ApprovalEsp(models.Model):
    """Подстановка {{...}} и в файлы, заменённые по замечанию к подписи.

    Обычный путь документа на подпись — контроллер /sign_esp:
    _esp_field_sources письма (там и подстановка) -> _create_documents.
    Мастер «Исправить документы» (замечание согласующего к файлу) идёт мимо
    него: appstream.approval.esp.document._replace_file кладёт новый файл
    прямо в _create_documents, и письмо уходило под ЭЦП с незаполненными
    {{name}}, {{date}} и прочими полями.

    Подстановка идемпотентна: в уже заполненном файле полей не осталось,
    fill_template возвращает None и байты не меняются — на обычном пути
    повторный вызов ничего не трогает и хеш документа прежний.
    """

    _inherit = "appstream.approval.esp"

    def _create_documents(self, sources):
        record = self._record() if self.model == "corr.outgoing" else None
        if record:
            template_fields = record._template_sources()
            for source in sources:
                raw = source.get("datas")
                if source.get("field") not in template_fields or not raw:
                    continue
                filled = record.fill_template(base64.b64decode(raw), source.get("name") or "")
                if filled:
                    source["datas"] = base64.b64encode(filled)
        return super()._create_documents(sources)


class ApprovalEspDocument(models.Model):
    """Проверка полей {{...}} в файле, заменённом по замечанию к подписи.

    При отправке на согласование шаблон проверяет check_templates. Мастер
    «Исправить документы» кладёт новый файл мимо неё, и опечатка вроде
    {{nmae}} уходила под ЭЦП как есть. Проверяем в момент замены: ошибку
    видит тот, кто загружает файл, и может сразу его поправить.
    """

    _inherit = "appstream.approval.esp.document"

    def _replace_file(self, datas, filename):
        self.ensure_one()
        record = self.esp_id._record() if self.esp_id.model == "corr.outgoing" else None
        if record and datas and self.source_field in record._template_sources():
            record._check_template_file(filename or self.name, base64.b64decode(datas))
        return super()._replace_file(datas, filename)
