from odoo import models


class ApprovalReturnWizardInherit(models.TransientModel):
    _inherit = 'approval.return.wizard'

    def additional_statement(self, res, model, record):
        """
        Добавляет статус 'revision' (Доработка заявки) в список доступных
        статусов для возврата документа.
        """
        res = super().additional_statement(res, model, record)
        
        if model == 'corr.incoming':
            # Для входящей корреспонденции: возврат на Доработку заявки
            if record.state == 'review':
                res = [('revision', 'Доработка заявки')]
        
        return res