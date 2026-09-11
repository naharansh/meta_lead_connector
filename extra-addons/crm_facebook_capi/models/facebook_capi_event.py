import logging
import requests
from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
GRAPH_V25 = "https://graph.facebook.com/v25.0"

STANDARD_META_EVENTS = [
    ("QualifiedLead", "Qualified Lead (Meta Lead Quality — Recommended for Qualified stage)", 10),
    ("ConvertedLead", "Converted Lead (Meta Lead Quality — Recommended for Won / Closed stage)", 20),
    ("Lead", "Lead (Standard initial lead generation)", 30),
    ("Contact", "Contact (Sales team contacted the lead)", 40),
    ("Schedule", "Schedule (Call, Demo or Meeting scheduled)", 50),
    ("SubmitApplication", "Submit Application (Application form submitted)", 60),
    ("Purchase", "Purchase / Deal Won (Closed-Won deal with revenue)", 70),
    ("CompleteRegistration", "Complete Registration", 80),
    ("InitiateCheckout", "Initiate Checkout (Proposal sent / Payment link issued)", 90),
    ("ViewContent", "View Content (Proposal or presentation viewed)", 100),
]


class FacebookCapiEvent(models.Model):
    _name = "facebook.capi.event"
    _description = "Meta Conversions API Event Definition"
    _order = "sequence, name"

    name = fields.Char(string="Event Name", required=True, index=True)
    description = fields.Char(string="Description")
    event_type = fields.Selection(
        [
            ("standard", "Standard Meta Event"),
            ("meta_fetched", "Fetched from Meta Dataset"),
            ("custom", "Custom Event"),
        ],
        string="Source / Type",
        default="standard",
        required=True,
    )
    sequence = fields.Integer(string="Sequence", default=10)

    if hasattr(models, "Constraint"):
        _name_uniq = models.Constraint("unique(name)", "Event name must be unique.")
    else:
        _sql_constraints = [
            ("name_uniq", "unique(name)", "Event name must be unique."),
        ]

    @api.model
    def seed_standard_events(self):
        """Seed official Meta Standard Lead Quality Events if not already present."""
        for event_name, desc, seq in STANDARD_META_EVENTS:
            existing = self.search([("name", "=", event_name)], limit=1)
            if not existing:
                self.create({
                    "name": event_name,
                    "description": desc,
                    "event_type": "standard",
                    "sequence": seq,
                })

    @api.model
    def fetch_events_from_meta(self, pixel_id=None, access_token=None):
        """Fetch all active events recorded by Meta for the given dataset/pixel.
        If credentials are not provided, auto-discovers from active CAPI accounts or instances.
        """
        self.seed_standard_events()

        if not pixel_id or not access_token:
            acc = self.env["facebook.capi.account"].search([
                ("pixel_id", "!=", False),
                ("access_token", "!=", False),
            ], limit=1)
            if acc:
                pixel_id = acc.pixel_id.strip()
                access_token = acc.access_token.strip()
            else:
                inst = self.env["facebook.instance"].search([
                    ("capi_dataset_id", "!=", False),
                    ("capi_access_token", "!=", False),
                ], limit=1)
                if inst:
                    pixel_id = inst.capi_dataset_id.strip()
                    access_token = inst.capi_access_token.strip()

        if not pixel_id or not access_token:
            raise UserError(
                "No Meta Dataset credentials found. Configure your Dataset ID and Access Token "
                "in CRM → Configuration → Meta CAPI Accounts first."
            )

        url = "%s/%s/stats" % (GRAPH_V25, pixel_id)
        try:
            resp = requests.get(url, params={
                "access_token": access_token,
                "aggregation": "event",
            }, timeout=15)
            data = resp.json()
        except Exception as e:
            raise UserError("Failed to connect to Meta Graph API: %s" % str(e))

        if "error" in data:
            _logger.warning("Meta API warning while fetching events: %s", data["error"])
            return {
                "discovered": [],
                "created_count": 0,
                "total_count": self.search_count([]),
                "warning": data["error"].get("message", str(data["error"])),
            }

        items = data.get("data", [])
        discovered = set()
        for item in items:
            for sub in item.get("data", []):
                val = sub.get("value")
                if val:
                    discovered.add(val)

        created_count = 0
        for ev in discovered:
            rec = self.search([("name", "=", ev)], limit=1)
            if not rec:
                self.create({
                    "name": ev,
                    "description": "Active event fetched from Meta Dataset %s" % pixel_id,
                    "event_type": "meta_fetched",
                    "sequence": 50,
                })
                created_count += 1
            elif rec.event_type == "custom":
                rec.write({"event_type": "meta_fetched"})

        return {
            "discovered": list(discovered),
            "created_count": created_count,
            "total_count": self.search_count([]),
        }
