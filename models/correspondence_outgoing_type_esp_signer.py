from odoo import models, fields

class OutgoingType(models.Model):
    _name = "correspondence.outgoing.type"
    _description = "Тип письма для исходящей корреспонденции"
    _order = "sequence, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    esp_signer_ids = fields.Many2many(
        "res.users",
        string="Утверждающие сотрудники",
    )

    sequence = fields.Integer(
        string="Порядок",
        default=10,
    )