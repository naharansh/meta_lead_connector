import logging
import time
import requests
from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
GRAPH_V25 = "https://graph.facebook.com/v25.0"


class FacebookCapiAccount(models.Model):
    _name = "facebook.capi.account"
    _description = "Per-Team Meta Dataset Routing Account"

    name = fields.Char(string="Account Name", required=True)
    team_id = fields.Many2one("crm.team", string="Sales Team",
        help="Leads from this sales team will route to this Meta Dataset.")
    page_id = fields.Many2one("facebook.page", string="Facebook Page",
        help="Select the Facebook Page whose Instance holds the CAPI credentials. "
             "Dataset ID and Token will auto-fill from the page's Instance.")
    instance_id = fields.Many2one("facebook.instance", string="Facebook Instance",
        related="page_id.instance_id", store=True, readonly=True,
        help="Resolved from the selected page.")

    pixel_id = fields.Char(string="Dataset / Pixel ID",
        help="Meta Dataset / Pixel ID. Auto-filled from the page's Instance; override if needed.")
    access_token = fields.Char(string="CAPI Access Token",
        help="System User Access Token. Auto-filled from the page's Instance; override if needed.")
    is_default = fields.Boolean(string="Is Default Account",
        help="Fallback for leads with no team / no Facebook page linkage.")
    is_connected = fields.Boolean(string="Connected", readonly=True, copy=False)
    last_test_date = fields.Datetime(string="Last Test", readonly=True, copy=False)

    if hasattr(models, "Constraint"):
        _team_uniq = models.Constraint("unique(team_id)",
            "Each sales team can only be mapped to one Meta Dataset account.")
    else:
        _sql_constraints = [
            ("team_uniq", "unique(team_id)",
             "Each sales team can only be mapped to one Meta Dataset account."),
        ]

    @api.onchange("page_id")
    def _onchange_page_id(self):
        """Auto-populate pixel_id and access_token from the page's Facebook Instance."""
        if self.page_id and self.page_id.instance_id:
            inst = self.page_id.instance_id
            if inst.capi_dataset_id and not self.pixel_id:
                self.pixel_id = inst.capi_dataset_id
            if inst.capi_access_token and not self.access_token:
                self.access_token = inst.capi_access_token
            if not self.name:
                self.name = self.page_id.name

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.pixel_id and rec.access_token:
                try:
                    self.env["facebook.capi.event"].fetch_events_from_meta(
                        pixel_id=rec.pixel_id.strip(),
                        access_token=rec.access_token.strip(),
                    )
                except Exception as e:
                    _logger.warning("Could not auto-fetch Meta events on account creation: %s", e)
        return records

    def action_sync_from_instance(self):
        """Force-sync pixel_id and access_token from the linked Facebook Instance."""
        self.ensure_one()
        if not self.page_id or not self.page_id.instance_id:
            raise UserError("No Facebook Page or Instance linked. Select a page first.")
        inst = self.page_id.instance_id
        if not inst.capi_dataset_id:
            raise UserError(
                "The linked Facebook Instance has no CAPI Dataset ID set.\n"
                "Go to: Facebook Integration → All Facebook Instances → open the instance → fill in CAPI Dataset ID."
            )
        if not inst.capi_access_token:
            raise UserError(
                "The linked Facebook Instance has no CAPI Access Token set.\n"
                "Go to: Facebook Integration → All Facebook Instances → open the instance → fill in CAPI Access Token."
            )
        self.write({
            "pixel_id": inst.capi_dataset_id,
            "access_token": inst.capi_access_token,
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Synced",
                "message": "Dataset ID and Access Token synced from Facebook Instance: %s" % inst.name,
                "type": "success",
                "sticky": False,
            },
        }

    def action_test_capi_connection(self):
        """Validate CAPI credentials.
        First tries reading dataset info (id, name). If permissions on GET are restricted
        (common for Conversions API-only tokens), it validates by posting a test event
        to the /events endpoint using test_event_code.
        """
        self.ensure_one()
        if not self.pixel_id:
            raise UserError("No Dataset / Pixel ID set. Fill it in and save first.")
        if not self.access_token:
            raise UserError("No CAPI Access Token set. Fill it in and save first.")

        pixel_id = self.pixel_id.strip()
        token = self.access_token.strip()

        dataset_name = None
        # Step 1: Try reading dataset metadata (requires ads_management / business_management)
        try:
            url = "%s/%s" % (GRAPH_V25, pixel_id)
            resp = requests.get(url, params={
                "access_token": token,
                "fields": "id,name",
            }, timeout=15)
            data = resp.json()
            if "error" not in data:
                dataset_name = data.get("name") or pixel_id
        except Exception:
            data = {}

        # Step 2: Fallback to Conversions API /events validation ping
        if not dataset_name:
            events_url = "%s/%s/events" % (GRAPH_V25, pixel_id)
            test_code = (
                self.env["ir.config_parameter"].sudo().get_param("facebook_capi.test_event_code")
                or "TEST00000"
            )
            test_payload = {
                "data": [{
                    "event_name": "Lead",
                    "event_time": int(time.time()),
                    "action_source": "website",
                    "user_data": {
                        "em": ["855f96e983f1f8e8be944692b6f719fd54329826cb62e98015efee8e2e071dd4"]
                    }
                }],
                "test_event_code": test_code.strip() if test_code else "TEST00000",
                "access_token": token,
            }
            try:
                resp_events = requests.post(events_url, json=test_payload, timeout=15)
                events_data = resp_events.json()
            except Exception as e:
                self.write({"is_connected": False})
                raise UserError("Connection error: %s" % str(e))

            if "error" in events_data:
                self.write({"is_connected": False})
                err = events_data["error"]
                msg = err.get("message", str(err))
                if err.get("error_user_title"):
                    msg = "%s: %s" % (err["error_user_title"], err.get("error_user_msg", msg))
                raise UserError("❌ CAPI Test Failed:\n%s" % msg)

            if events_data.get("events_received", 0) > 0:
                dataset_name = "%s (Conversions API Verified)" % pixel_id

        self.write({
            "is_connected": True,
            "last_test_date": fields.Datetime.now(),
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Connection Successful",
                "message": "✅ Connected to Meta dataset: %s" % dataset_name,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_fetch_meta_events(self):
        """Fetch active events recorded on this dataset from Meta."""
        self.ensure_one()
        if not self.pixel_id or not self.access_token:
            raise UserError("Dataset ID and Access Token must be set before fetching events.")
        res = self.env["facebook.capi.event"].fetch_events_from_meta(
            pixel_id=self.pixel_id.strip(),
            access_token=self.access_token.strip()
        )
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

