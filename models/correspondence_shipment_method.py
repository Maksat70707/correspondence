from odoo import models, fields

class ShipmentMethod(models.Model):
    _name = "correspondence.shipment.method"
    _description = "Метод отправки/получения корреспонденции"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)