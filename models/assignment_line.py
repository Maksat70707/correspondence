# -*- coding: utf-8 -*-

from odoo import fields, models


class AssignmentLine(models.Model):
    """Поручение по входящему письму.

    Само поручение — модуль assignment. Здесь только ссылка на письмо:
    по ней поручение находит свой документ (срок, задачи исполнителям,
    автозакрытие письма — хуки corr.incoming), а правила доступа
    корреспонденции ходят через письмо.
    """

    _inherit = "assignment.line"

    incoming_id = fields.Many2one(
        "corr.incoming",
        string="Входящее письмо",
        ondelete="cascade",
        index=True,
    )

    def _user_can_manage(self):
        """Поручения без документа, заведённые из меню корреспонденции, как
        и раньше правят директор и администратор корреспонденции."""
        if super()._user_can_manage():
            return True
        if self._assignment_document():
            return False
        user = self.env.user
        return user.has_group("correspondence.group_correspondence_director") \
            or user.has_group("correspondence.group_correspondence_admin")
