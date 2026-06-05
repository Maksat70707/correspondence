from odoo import api, fields, models


class OutgoingRecipients(models.Model):
    _name = "correspondence.outgoing.recipients"
    _description = "Получатели исходящей корреспонденции"

    active = fields.Boolean(default=True)

    outgoing_id = fields.Many2one(
        "corr.outgoing",
        string="Исходящий документ",
        required=True,
        ondelete="cascade",
    )

    recipient = fields.Char(
        string="Получатель",
        required=True,
    )

    shipment_method_ids = fields.Many2many(
        'correspondence.shipment.method',
        relation='corr_out_recipient_ship_rel',
        column1='recipient_id',
        column2='shipment_method_id',
        string='Методы отправки',
        required=True,
    )

    full_name = fields.Char(
        string="ФИО",
    )

    position = fields.Char(
        string="Должность",
    )

    odoo_user_id = fields.Many2one(
        "res.users",
        string="Пользователь Odoo",
    )

    address = fields.Char(
        string="Адрес",
    )

    phone = fields.Char(
        string="Телефон",
    )

    email = fields.Char(
        string="Email",
    )
