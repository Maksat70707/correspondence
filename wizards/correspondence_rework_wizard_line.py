from odoo import api, models, fields
from datetime import timedelta


class CorrespondenceReworkWizardLine(models.TransientModel):
    _name = 'correspondence.rework.wizard.line'
    _description = 'Rework wizard line'

    wizard_id = fields.Many2one(
        'correspondence.rework.wizard',
        required=True,
        ondelete='cascade'
    )
    user_id = fields.Many2one('res.users', required=True)
    resolution_id = fields.Many2one('correspondence.resolution.option', required=True)
    deadline = fields.Date(required=True)
    report_needed = fields.Boolean(string="Требуется отчёт")

    @api.onchange('resolution_id')
    def _onchange_resolution_id(self):
        if self.resolution_id:
            if self.resolution_id.days_for_completion:
                self.deadline = fields.Date.today() + timedelta(days=self.resolution_id.days_for_completion)
            self.report_needed = self.resolution_id.report_needed
