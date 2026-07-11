from odoo import models, fields, api
from odoo.exceptions import UserError
import requests

FB_API = "https://graph.facebook.com/v25.0"


class FacebookAccount(models.Model):
    _name = 'crm.facebook.account'
    _description = 'Facebook Account'

    name = fields.Char(required=True)
    app_id = fields.Char()
    app_secret = fields.Char()
    access_token = fields.Char(string="Access Token")
    page_id = fields.Char(string="Page ID")
    connection_valid = fields.Boolean(string="Connection Valid", default=False)

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

    def action_test_connection(self):
        """Test connection"""
        self.ensure_one()
        data = self._fb_get("me")
        if 'error' in data:
            raise UserError(f"Connection failed: {data['error'].get('message')}")
        self.connection_valid = True
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': f"Connected as {data.get('name')} (ID {data.get('id')})",
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}
            },
        }

    def _get_working_token(self, page_id=None):
        """Try to get a working token for the page. Returns (token, source) or (None, None)."""
        self.ensure_one()
        page_id = page_id or self.page_id
        token = self.access_token
        if not token or not page_id:
            return None, None

        # 1. Try stored token directly
        resp = self._fb_get(f"{page_id}/leadgen_forms", params={'fields': 'id'})
        if 'error' not in resp:
            return token, 'stored'

        # 2. Try to get Page Access Token via /me/accounts
        resp = self._fb_get("me/accounts", token=token,
                            params={'fields': 'id,access_token'})
        for page in resp.get('data', []):
            if page['id'] == page_id and page.get('access_token'):
                return page['access_token'], 'me_accounts'

        # 3. Try through business account pages
        business_id = self._get_business_id(token)
        if business_id:
            page_token = self._get_page_token_from_business(business_id, page_id, token)
            if page_token:
                return page_token, 'business'

        return None, None

    def _get_business_id(self, token):
        """Get the business ID from the token"""
        resp = self._fb_get("me", token=token, params={'fields': 'business{id,name}'})
        return resp.get('business', {}).get('id')

    def _get_page_token_from_business(self, business_id, page_id, token):
        """Try to get a Page Access Token through the business account"""
        for endpoint in ['owned_pages', 'client_pages', 'pages']:
            resp = self._fb_get(f"{business_id}/{endpoint}", token=token,
                                params={'fields': 'id,name,access_token'})
            for page in resp.get('data', []):
                if page['id'] == page_id and page.get('access_token'):
                    return page['access_token']
        return None

    def action_verify_token(self):
        """Verify token works and auto-resolve Page Access Token if needed"""
        self.ensure_one()
        if not self.access_token:
            raise UserError("No Access Token set. Paste your token and save first.")
        if not self.page_id:
            raise UserError("No Page ID set. Enter your Facebook Page ID and save first.")

        working_token, source = self._get_working_token()

        if working_token:
            if source in ('business', 'me_accounts'):
                self.sudo().write({'access_token': working_token, 'connection_valid': True})
                msg = (f"Page Access Token resolved via {source}!\n\n"
                       f"Page: {self.page_id}\n"
                       f"Leadgen Forms: Accessible\n\n"
                       f"You can now sync lead forms.")
            else:
                self.sudo().write({'connection_valid': True})
                msg = (f"Token verified! Access to leadgen_forms on page {self.page_id} is working.\n\n"
                       f"Token source: Stored token")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Token Verified',
                    'message': msg,
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}
                },
            }

        self.sudo().write({'connection_valid': False})
        raise UserError(
            "❌ Token verification failed.\n\n"
            "The token cannot access leadgen_forms on this page.\n\n"
            "Your token is a SYSTEM_USER token. To fix this:\n\n"
            "1. Go to business.facebook.com/settings\n"
            "2. Click 'System Users' under 'Users'\n"
            "3. Find your system user (yuvmedia)\n"
            "4. Click 'Add Assets' -> 'Pages'\n"
            f"5. Select page {self.page_id}\n"
            "6. Assign 'Manage Page' + 'leads_retrieval' permissions\n"
            "7. Click 'Save Changes'\n"
            "8. Come back and click 'Verify Token'"
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.access_token and rec.page_id:
                rec._auto_resolve_token()
        return records

    def write(self, vals):
        result = super().write(vals)
        if 'access_token' in vals or 'page_id' in vals:
            for rec in self:
                if rec.access_token and rec.page_id:
                    rec._auto_resolve_token()
        return result

    def _auto_resolve_token(self):
        """Auto-resolve Page Access Token without user clicking Verify Token"""
        self.ensure_one()
        try:
            working_token, source = self._get_working_token()
            if working_token and source in ('business', 'me_accounts'):
                self.sudo().write({'access_token': working_token, 'connection_valid': True})
            elif working_token:
                self.sudo().write({'connection_valid': True})
        except Exception:
            pass

    def action_sync_leadforms(self):
        """Fetch lead forms from Facebook and open the lead forms list"""
        self.ensure_one()
        # Auto-resolve token before syncing
        working_token, source = self._get_working_token()
        if working_token and source in ('business', 'me_accounts'):
            self.sudo().write({'access_token': working_token})
        return self.env['crm.facebook.campaign'].action_fetch_leadforms(account=self)

    def action_debug_token(self):
        """Debug the current Access Token"""
        self.ensure_one()
        token = self.access_token
        page_id = self.page_id

        if not token:
            raise UserError("No access token found. Set an Access Token first.")

        lines = ["=== Record Fields ===\n"]
        lines.append(f"Page ID:       {page_id or '(not set)'}")
        lines.append(f"Access Token:  {(token[:20] + '...') if len(token) > 20 else token}")
        lines.append(f"App ID:        {self.app_id or '(not set)'}")
        lines.append(f"App Secret:    {'***set***' if self.app_secret else '(not set)'}")
        lines.append("")

        # Token info
        token_resp = self._fb_get("debug_token", params={'input_token': token, 'access_token': token})
        token_data = token_resp.get('data', {})
        token_type = token_data.get('type', 'N/A')
        scopes = token_data.get('scopes', [])

        lines.append("=== Token Info ===\n")
        lines.append(f"Valid:    {token_data.get('is_valid', 'N/A')}")
        lines.append(f"App ID:   {token_data.get('app_id', 'N/A')}")
        lines.append(f"Type:     {token_type}")
        expires_at = token_data.get('expires_at')
        if expires_at and expires_at != 0:
            from datetime import datetime
            lines.append(f"Expires:  {datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            lines.append("Expires:  never (permanent)")
        lines.append(f"Scopes:   {', '.join(scopes) if scopes else '(none)'}")
        has_leads = 'leads_retrieval' in scopes
        lines.append(f"leads_retrieval: {'YES' if has_leads else 'MISSING'}")
        lines.append("")

        # /me
        me_resp = self._fb_get("me")
        lines.append(f"=== /me ===\n")
        lines.append(f"ID:   {me_resp.get('id', 'N/A')}")
        lines.append(f"Name: {me_resp.get('name', 'N/A')}")
        lines.append("")

        # Business
        business_id = self._get_business_id(token)
        if business_id:
            lines.append(f"Business ID: {business_id}\n")

            # List all business pages
            for ep in ['owned_pages', 'client_pages', 'pages']:
                resp = self._fb_get(f"{business_id}/{ep}", params={'fields': 'id,name,access_token'})
                pages = resp.get('data', [])
                if pages:
                    lines.append(f"=== {ep} ===\n")
                    for p in pages:
                        has_token = 'YES' if p.get('access_token') else 'NO'
                        marker = " <-- TARGET" if p['id'] == page_id else ""
                        lines.append(f"  {p.get('name', '?')} (ID: {p['id']}, Token: {has_token}){marker}")
                    lines.append("")
        else:
            lines.append("No business account found.\n")

        # /me/accounts
        me_accounts_resp = self._fb_get("me/accounts", token=token,
                                        params={'fields': 'id,name,access_token'})
        me_pages = me_accounts_resp.get('data', [])
        if me_pages:
            lines.append(f"=== /me/accounts (Page Tokens) ===\n")
            for p in me_pages:
                has_token = 'YES' if p.get('access_token') else 'NO'
                marker = " <-- TARGET" if p['id'] == page_id else ""
                lines.append(f"  {p.get('name', '?')} (ID: {p['id']}, Token: {has_token}){marker}")
            lines.append("")
        else:
            lines.append("=== /me/accounts ===\n")
            lines.append("  No pages returned. Error or empty.\n")

        # Try to get working token
        lines.append("=== Token Resolution ===\n")
        working_token, source = self._get_working_token()
        if working_token:
            lines.append(f"Working token found via: {source}")
            lines.append(f"Token: {working_token[:20]}...")
            lines.append("Status: READY TO SYNC")
        else:
            lines.append("No working token found for this page.")
            lines.append("The system user needs page access in Business Manager.")
        lines.append("")

        if not has_leads:
            lines.append("=== FIX: Missing leads_retrieval ===\n")
            lines.append("1. Meta for Developers -> App -> Permissions -> Request 'leads_retrieval'")
            lines.append("2. Regenerate token after approval\n")

        if page_id and not working_token:
            lines.append("=== FIX: Page not accessible ===\n")
            lines.append("1. business.facebook.com/settings -> System Users")
            lines.append("2. Find system user -> Add Assets -> Pages")
            lines.append(f"3. Select page {page_id}")
            lines.append("4. Assign 'Manage Page' + 'leads_retrieval'")
            lines.append("5. Save -> return here -> Verify Token")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Token Diagnostic',
                'message': '\n'.join(lines),
                'type': 'success' if working_token else 'warning',
                'sticky': True,
            },
        }
