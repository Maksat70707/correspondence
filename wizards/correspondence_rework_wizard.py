from odoo import models, fields, _
from odoo.exceptions import AccessError


class CorrespondenceReworkWizard(models.TransientModel):
    _name = 'correspondence.rework.wizard'
    _description = 'Rework Incoming'

    incoming_id = fields.Many2one(
        'corr.incoming',
        required=True,
        readonly=True,
    )

    reason = fields.Text(string='Причина доработки', required=True)

    line_ids = fields.One2many(
        'correspondence.rework.wizard.line',
        'wizard_id',
        string='Новые задачи',
    )

    def action_confirm(self):
        self.ensure_one()

        user = self.env.user
        if not (
            user.has_group('correspondence.group_correspondence_director')
            or user.has_group('correspondence.group_correspondence_admin')
            or user.has_group('correspondence.group_correspondence_secretary')
        ):
            raise AccessError(_('Недостаточно прав.'))

        tasks = []
        for line in self.line_ids:
            tasks.append({
                'user_id': line.user_id.id,
                'resolution_id': line.resolution_id.id,
                'deadline': line.deadline,
                'report_needed': line.report_needed,
            })

        # Delegate to business method (keeps wizard thin)
        self.incoming_id.action_send_to_rework(self.reason, tasks)
        return {'type': 'ir.actions.act_window_close'}
