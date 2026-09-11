from datetime import timedelta
from odoo import api, fields, models

class FacebookCapiLog(models.Model):
    _name = "facebook.capi.log"
    _description = "Meta Conversions API Send Log"
    _order = "create_date desc"

    lead_id = fields.Many2one("crm.lead", string="CRM Lead", ondelete="set null", index=True)
    account_id = fields.Many2one("facebook.capi.account", string="Dataset Account", ondelete="set null", index=True)
    event_name = fields.Char(string="Event Name", index=True)
    payload = fields.Text(string="Request Payload (PII Hashed)")
    response_code = fields.Integer(string="HTTP Response Code")
    response_body = fields.Text(string="HTTP Response Body")
    status = fields.Selection(
        [("sent", "Sent"), ("failed", "Failed"), ("skipped", "Skipped")],
        string="Status",
        index=True,
    )

    @api.model
    def _gc_logs(self, days=30):
        cutoff = fields.Datetime.now() - timedelta(days=days)
        old_logs = self.sudo().search([("create_date", "<", cutoff)])
        old_logs.unlink()
