from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    fb_capi_test_event_code = fields.Char(
        string="Meta CAPI Test Event Code",
        config_parameter="facebook_capi.test_event_code",
        help="Copy from Meta Events Manager → your Dataset → Test Events tab. "
             "Leave blank in production.",
    )
    fb_capi_verify_token = fields.Char(
        string="Webhook Verify Token",
        config_parameter="facebook_capi.verify_token",
        help="Secret token for Meta Lead Ads webhook subscription verification (optional).",
    )
