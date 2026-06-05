from odoo import models, fields

class Resolution(models.Model):
    _name = "correspondence.resolution.option"
    _description = "Резолюция"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    report_needed = fields.Boolean(string="Требуется отчет")

    days_for_completion = fields.Integer(
        string="Количество дней для выполнения",
        help="Количество дней до срока выполнения, за которое необходимо отправить уведомление ответственному лицу.",
    )