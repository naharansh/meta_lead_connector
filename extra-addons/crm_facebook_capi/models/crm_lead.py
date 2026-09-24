from odoo import api, fields, models

class CrmLead(models.Model):
    _inherit = "crm.lead"

    x_facebook_last_event = fields.Char(string="Last Meta Event", copy=False)
    x_facebook_event_status = fields.Selection(
        [("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed"), ("skipped", "Skipped")],
        string="Meta Event Status",
        copy=False,
        index=True,
    )
    x_facebook_sent_date = fields.Datetime(string="Meta Event Sent On", copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)

    def write(self, vals):
        track_stage = "stage_id" in vals
        track_type = "type" in vals
        old_stage = {l.id: l.stage_id.id for l in self} if track_stage else {}
        old_type = {l.id: l.type for l in self} if track_type else {}
        res = super().write(vals)
        service = self.env["facebook.capi.service"].sudo()
        for lead in self:
            if track_stage and lead.stage_id.id != old_stage.get(lead.id):
                service._on_stage_change(lead)
            if track_type and old_type.get(lead.id) == "lead" and lead.type == "opportunity":
                service._on_stage_change(lead)
        return res

    def action_send_meta_capi(self):
        service = self.env["facebook.capi.service"].sudo()
        lead_count = len(self)
        sent = 0
        for lead in self:
            service._on_stage_change(lead)
            if lead.x_facebook_event_status == "sent":
                sent += 1
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Meta CAPI",
                "message": "%s/%s lead(s) sent to Meta." % (sent, lead_count),
                "type": "success" if sent == lead_count else "warning",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
