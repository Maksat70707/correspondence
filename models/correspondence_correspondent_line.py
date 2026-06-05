from odoo import api, fields, models


class CorrespondenceCorrespondentLine(models.Model):
    _name = "correspondence.correspondent.line"
    _description = "Корреспондент"

    active = fields.Boolean(default=True)

    # Привязка к документу (одно из двух)
    outgoing_id = fields.Many2one(
        "corr.outgoing",
        string="Исходящий документ",
        ondelete="cascade",
    )

    incoming_id = fields.Many2one(
        "corr.incoming",
        string="Входящий документ",
        ondelete="cascade",
    )

    name = fields.Char(
        string="Корреспондент",
        required=True,
    )


    shipment_method_ids = fields.Many2many(
        'correspondence.shipment.method',
        relation='corr_correspondent_ship_rel',
        column1='correspondent_id',
        column2='shipment_method_id',
        string='Методы отправки',
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
