from odoo import models, fields, api
from odoo.exceptions import UserError
import requests
import time

FB_API = "https://graph.facebook.com/v25.0"


class FacebookInstance(models.Model):
    _name = 'facebook.instance'
    _description = 'Facebook Instance'

    name = fields.Char(string="Name", default="Facebook Instances", required=True)
    access_token = fields.Char(string="Access Token")
    is_connected = fields.Boolean(string="Is Connected", default=False)
    active_scheduler = fields.Boolean(string="Active Scheduler", default=False)
    keep_logs = fields.Selection([
        ('1', '1 Month'),
        ('2', '2 Months'),
        ('3', '3 Months'),
        ('6', '6 Months'),
        ('12', '12 Months')
    ], string="Keep Logs (Months)", default='1')
    log_scheduler = fields.Boolean(string="Log Scheduler", default=False)
    page_ids = fields.One2many('facebook.page', 'instance_id', string="Facebook Pages")

    capi_dataset_id = fields.Char(string="CAPI Dataset ID",
        help="Meta Pixel / Dataset ID used for the Conversions API (CAPI).")
    capi_access_token = fields.Char(string="CAPI Access Token",
        help="Conversions API (CAPI) access token generated in Meta Business Manager (System User or Dataset token).")
    capi_is_connected = fields.Boolean(string="CAPI Is Connected", default=False)
    capi_last_test_date = fields.Datetime(string="Last CAPI Test Date")

    def _fb_get(self, endpoint, token=None, params=None):
        """Make a GET request to the Facebook Graph API"""
        token = token or self.access_token
        if not token:
            raise ValueError("⚠️ No Access Token set.")
        url = f"{FB_API}/{endpoint}"
        params = params or {}
        params['access_token'] = token
        res = requests.get(url, params=params, timeout=15)
        return res.json()

    def action_sync_facebook_pages(self):
        """Fetch pages from Facebook and update facebook.page records"""
        self.ensure_one()
        if not self.access_token:
            raise UserError("No Access Token set. Paste your token and save first.")

        data = self._fb_get("me/accounts", params={'fields': 'id,name,access_token'})
        if 'error' in data:
            self.is_connected = False
            raise UserError(f"Sync failed: {data['error'].get('message')}")

        pages_data = data.get('data', [])
        PageModel = self.env['facebook.page']
        
        synced_page_ids = []
        for page in pages_data:
            page_id = page.get('id')
            page_name = page.get('name')
            page_token = page.get('access_token')

            existing_page = PageModel.search([
                ('page_id', '=', page_id),
                ('instance_id', '=', self.id)
            ], limit=1)

            if existing_page:
                existing_page.write({
                    'name': page_name,
                    'access_token': page_token,
                })
                synced_page_ids.append(existing_page.id)
            else:
                new_page = PageModel.create({
                    'name': page_name,
                    'page_id': page_id,
                    'access_token': page_token,
                    'instance_id': self.id,
                })
                synced_page_ids.append(new_page.id)

        self.is_connected = True
        
        # Create log record
        self.env['facebook.logger'].create({
            'name': 'Page Sync',
            'log_type': 'info',
            'message': f"Synced {len(pages_data)} pages for instance {self.name}."
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': f"Synced {len(pages_data)} page(s) successfully.",
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}
            },
        }

    def action_test_capi_connection(self):
        """Validate the CAPI Dataset ID + Access Token against the Meta Graph API."""
        self.ensure_one()
        if not self.capi_dataset_id:
            raise UserError("⚠️ No CAPI Dataset ID set. Paste your dataset (pixel) ID and save first.")
        if not self.capi_access_token:
            raise UserError("⚠️ No CAPI Access Token set. Paste your Conversions API access token and save first.")

        dataset_id = self.capi_dataset_id.strip()
        token = self.capi_access_token.strip()

        dataset_name = None
        # Step 1: Try reading dataset metadata (requires ads_management)
        try:
            data = self._fb_get(dataset_id, token=token, params={'fields': 'id,name'})
            if 'error' not in data:
                dataset_name = data.get('name') or dataset_id
        except Exception:
            data = {}

        # Step 2: Fallback to Conversions API /events validation ping
        if not dataset_name:
            events_url = f"{FB_API}/{dataset_id}/events"
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
                self.capi_is_connected = False
                raise UserError(f"Connection error: {e}")

            if "error" in events_data:
                self.capi_is_connected = False
                err = events_data["error"]
                msg = err.get("message", str(err))
                if err.get("error_user_title"):
                    msg = f"{err['error_user_title']}: {err.get('error_user_msg', msg)}"
                self.env['facebook.logger'].create({
                    'name': 'CAPI Connection Test',
                    'log_type': 'error',
                    'message': f"Instance: {self.name} (Dataset: {dataset_id})\n{msg}",
                })
                if "facebook.capi.log" in self.env:
                    self.env["facebook.capi.log"].sudo().create({
                        "event_name": "Test Connection",
                        "status": "failed",
                        "response_code": 400,
                        "response_body": f"Instance: {self.name} (Dataset: {dataset_id})\n{msg}",
                    })
                raise UserError(f"❌ CAPI Connection failed: {msg}")

            if events_data.get("events_received", 0) > 0:
                dataset_name = f"{dataset_id} (Conversions API Verified)"

        self.capi_is_connected = True
        self.capi_last_test_date = fields.Datetime.now()

        self.env['facebook.logger'].create({
            'name': 'CAPI Connection Test',
            'log_type': 'info',
            'message': f"Instance: {self.name} (Dataset: {dataset_id})\n"
                       f"Connected to dataset: {dataset_name}",
        })
        if "facebook.capi.log" in self.env:
            self.env["facebook.capi.log"].sudo().create({
                "event_name": "Test Connection",
                "status": "sent",
                "response_code": 200,
                "response_body": f"Instance: {self.name} (Dataset: {dataset_id})\nConnected to dataset: {dataset_name}",
            })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': f"✅ CAPI connected to dataset: {dataset_name}",
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }


class FacebookPage(models.Model):
    _name = 'facebook.page'
    _description = 'Facebook Page'

    name = fields.Char(string="Page Name", required=True)
    page_id = fields.Char(string="Page ID", required=True)
    access_token = fields.Char(string="Page Access Token")
    instance_id = fields.Many2one('facebook.instance', string="Facebook Instance", ondelete='cascade')
    active_scheduler = fields.Boolean(related='instance_id.active_scheduler', string="Active Scheduler", readonly=True)
    leadform_ids = fields.One2many('crm.facebook.leadform', 'page_id', string="Lead Forms")

    def action_sync_facebook_forms(self):
        self.ensure_one()
        campaign_model = self.env['crm.facebook.campaign']
        campaign_model.action_fetch_leadforms(page=self)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Odoo Facebook Forms',
            'res_model': 'crm.facebook.leadform',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'target': 'current',
            'context': {'create': False},
            'domain': [('page_id', '=', self.id)],
        }
