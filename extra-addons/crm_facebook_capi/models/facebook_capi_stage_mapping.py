from odoo import api, fields, models

class FacebookCapiStageMapping(models.Model):
    _name = "facebook.capi.stage.mapping"
    _description = "Odoo CRM Stage → Meta CAPI Event Mapping"
    _order = "stage_sequence, id"

    stage_id = fields.Many2one("crm.stage", string="CRM Stage", required=True, ondelete="cascade")
    stage_sequence = fields.Integer(related="stage_id.sequence", store=True, string="Sequence")
    event_id = fields.Many2one(
        "facebook.capi.event",
        string="Meta Event",
        help="Select a standard Meta event (QualifiedLead, ConvertedLead, etc.) or an event fetched from your Meta Dataset.",
    )
    event_name = fields.Char(
        string="Meta Event Name",
        required=True,
        help="Standard Meta event (e.g. QualifiedLead, ConvertedLead, Purchase) or custom event name."
    )

    @api.onchange("event_id")
    def _onchange_event_id(self):
        if self.event_id:
            self.event_name = self.event_id.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("event_id") and not vals.get("event_name"):
                event = self.env["facebook.capi.event"].browse(vals["event_id"])
                vals["event_name"] = event.name
            elif vals.get("event_name") and not vals.get("event_id"):
                event = self.env["facebook.capi.event"].search([("name", "=", vals["event_name"])], limit=1)
                if event:
                    vals["event_id"] = event.id
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("event_id") and not vals.get("event_name"):
            event = self.env["facebook.capi.event"].browse(vals["event_id"])
            vals["event_name"] = event.name
        elif vals.get("event_name") and "event_id" not in vals:
            event = self.env["facebook.capi.event"].search([("name", "=", vals["event_name"])], limit=1)
            if event:
                vals["event_id"] = event.id
        return super().write(vals)

    def action_fetch_meta_events(self):
        """Fetch live events from Meta Dataset and seed standard events."""
        res = self.env["facebook.capi.event"].fetch_events_from_meta()
        discovered = res.get("discovered", [])
        msg = "✅ Meta Events synchronized!\n"
        if discovered:
            msg += "Discovered in Dataset: %s\n" % ", ".join(discovered)
        msg += "Total available events in Odoo: %d" % res.get("total_count", 0)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Meta Events Fetched",
                "message": msg,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    value_mode = fields.Selection(
        [
            ("none", "No value"),
            ("expected_revenue", "Expected Revenue"),
            ("fixed", "Fixed amount"),
        ],
        string="Value Calculation",
        default="none",
        required=True,
    )
    fixed_value = fields.Float(string="Fixed Value Amount")
    action_source = fields.Selection(
        [
            ("system_generated", "System Generated (Recommended for CRM Lead Quality)"),
            ("website", "Website"),
            ("phone_call", "Phone Call"),
            ("business_messaging", "Business Messaging (WhatsApp / Messenger)"),
            ("other", "Other"),
        ],
        string="Action Source",
        default="system_generated",
        required=True,
        help="Action source parameter sent to Meta CAPI. 'system_generated' is recommended for CRM stage automation.",
    )
    active = fields.Boolean(default=True)

    if hasattr(models, "Constraint"):
        _stage_uniq = models.Constraint("unique(stage_id)", "Each CRM stage can only have one CAPI event mapping.")
    else:
        _sql_constraints = [
            ("stage_uniq", "unique(stage_id)", "Each CRM stage can only have one CAPI event mapping."),
        ]
