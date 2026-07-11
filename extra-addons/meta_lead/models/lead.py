from odoo import models, fields, api
# pyrefly: ignore [missing-import]
from odoo.exceptions import UserError
import json
import re
import requests

FB_API = "https://graph.facebook.com/v25.0"


class FacebookCampaign(models.Model):
    _name = 'crm.facebook.campaign'
    _description = 'Facebook Campaign'

    name = fields.Char(string="Campaign Name")
    fb_form_id = fields.Char(string="Facebook Form ID")
    page_id = fields.Char(string="Page ID")
    status = fields.Selection(
        [('active', 'Active'), ('paused', 'Paused')],
        string="Status"
    )

    leadform_ids = fields.One2many(
        'crm.facebook.leadform',
        'campaign_id',
        string="Lead Forms"
    )

    def action_fetch_leadforms(self, account=None):
        """Fetch lead forms for the configured page and open lead forms list view.
        If account is provided, fetch only for that account. Otherwise, fetch for all accounts."""
        if account:
            accounts = account
        else:
            accounts = self.env['crm.facebook.account'].search(
                [('page_id', '!=', False), ('access_token', '!=', False)])

        if not accounts:
            raise UserError(
                "⚠️ No Facebook account configured.\n"
                "Go to CRM > Facebook Accounts > create an account with your "
                "Access Token and Page ID, then click 'Sync Lead Forms'.")

        leadform_model = self.env['crm.facebook.leadform']
        synced_count = 0
        errors = []

        for acc in accounts:
            page_id = acc.page_id

            working_token, source = acc._get_working_token()
            if working_token:
                access_token = working_token
                if source in ('business', 'me_accounts'):
                    acc.sudo().write({'access_token': working_token})
            else:
                errors.append(f"Page {page_id}: no working token found")
                continue

            url = f"{FB_API}/{page_id}/leadgen_forms"
            resp = requests.get(url, params={
                'access_token': access_token,
                'fields': 'id,name,status,leads_count'
            }, timeout=15).json()

            if 'error' in resp:
                err = resp['error']
                errors.append(f"Page {page_id}: {err.get('message')}")
                continue

            for form in resp.get('data', []):
                campaign = self.search([('fb_form_id', '=', form['id'])])
                if not campaign:
                    campaign = self.create({
                        'name': form.get('name'),
                        'fb_form_id': form['id'],
                        'page_id': page_id,
                        'status': 'active',
                    })

                leadform = leadform_model.search([('form_id', '=', form['id'])])
                vals = {
                    'name': form.get('name'),
                    'form_id': form['id'],
                    'campaign_id': campaign.id,
                    'account_id': acc.id,
                    'leads_count': form.get('leads_count', 0),
                }
                if leadform:
                    leadform.write(vals)
                else:
                    leadform_model.create(vals)

            synced_count += 1

        msg = f"Synced {synced_count} account(s)."
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors)

        return {
            'type': 'ir.actions.act_window',
            'name': 'Lead Forms',
            'res_model': 'crm.facebook.leadform',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'target': 'current',
            'context': {'create': False},
        }

    def action_view_leadforms(self):
        """Open lead forms linked to this campaign"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Lead Forms',
            'res_model': 'crm.facebook.leadform',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)],
            'context': {'default_campaign_id': self.id},
        }


class FacebookLeadForm(models.Model):
    _name = 'crm.facebook.leadform'
    _description = 'Facebook Lead Form'

    name = fields.Char(required=True)
    form_id = fields.Char(string="Form ID", required=True)
    campaign_id = fields.Many2one('crm.facebook.campaign', string="Campaign")
    account_id = fields.Many2one('crm.facebook.account', string="Facebook Account")
    leads_count = fields.Integer(string="Leads Count", default=0)
    last_fetched = fields.Datetime(string="Last Fetched Time", default=False)
    last_fetch_summary = fields.Text(string="Last Fetch Summary")
    lead_ids = fields.One2many('crm.lead', 'fb_form_id', string="Fetched Leads")

    @staticmethod
    def _normalize_phone(phone):
        """Strip spaces, dashes, parentheses and leading + for comparison"""
        if not phone:
            return ''
        return re.sub(r'[\s\-\(\)\+]', '', phone)

    def action_fetch_leads(self):
        """Fetch leads for this form and create crm.lead records"""
        self.ensure_one()
        account = self.account_id
        if not account:
            account = self.env['crm.facebook.account'].search(
                [('page_id', '!=', False), ('access_token', '!=', False)], limit=1)
        if not account or not account.access_token:
            raise UserError(
                "⚠️ No Facebook account found.\n"
                "Set up an account with your Access Token and Page ID, "
                "then click 'Verify Token'.")

        working_token, source = account._get_working_token(self.campaign_id.page_id if self.campaign_id else None)
        access_token = working_token or account.access_token
        if working_token and source == 'business':
            account.sudo().write({'access_token': working_token})

        url = f"{FB_API}/{self.form_id}/leads"
        params = {
            'access_token': access_token,
            'fields': 'id,created_time,field_data',
            'limit': 100,
        }

        all_leads = []
        next_url = None
        while True:
            if next_url:
                resp = requests.get(next_url, params={'access_token': access_token}, timeout=15).json()
            else:
                resp = requests.get(url, params=params, timeout=15).json()

            if 'error' in resp:
                err = resp['error']
                err_code = err.get('code')
                if err_code == 10:
                    raise UserError(
                        "❌ Insufficient privileges to fetch leads.\n\n"
                        "Fix in Meta Business Manager:\n"
                        "1. business.facebook.com/settings -> System Users\n"
                        "2. Add Assets -> Pages -> assign page + 'leads_retrieval'\n"
                        "3. Save -> return here -> Sync Lead Forms")
                if err_code == 190:
                    raise UserError(
                        "❌ Access token has expired.\n"
                        "Paste a new token, then click Verify Token.")
                raise UserError(f"❌ Facebook API Error: {err.get('message', err)}")

            all_leads.extend(resp.get('data', []))
            paging = resp.get('paging', {})
            next_url = paging.get('next')
            if not next_url:
                break

        if not all_leads:
            raise UserError("✅ No new leads found. All leads have already been fetched.")

        crm_lead_env = self.env['crm.lead']
        latest_time = None
        created_count = 0
        skipped_count = 0
        duplicate_phones = []

        existing_phone_ids = set()
        for rec in crm_lead_env.search([('phone', '!=', False)], limit=5000):
            normalized = self._normalize_phone(rec.phone)
            if normalized:
                existing_phone_ids.add(normalized)

        for fb_lead in all_leads:
            lead_id = fb_lead['id']
            existing_lead = crm_lead_env.search([('fb_lead_id', '=', lead_id)], limit=1)
            if existing_lead:
                skipped_count += 1
                continue

            phone = ''
            for field in fb_lead.get('field_data', []):
                if field['name'] == 'phone_number':
                    phone = field['values'][0] if field.get('values') else ''
                    break
            if phone:
                normalized = self._normalize_phone(phone)
                if normalized in existing_phone_ids:
                    duplicate_phones.append(phone)
                    skipped_count += 1
                    continue

            created_time_str = fb_lead.get('created_time')
            if created_time_str:
                created_time_str = created_time_str[:19].replace('T', ' ')
            if created_time_str and (latest_time is None or created_time_str > latest_time):
                latest_time = created_time_str

            lead_data = {
                'name': f"{self.name} - {lead_id}",
                'fb_lead_id': lead_id,
                'fb_form_id': self.id,
                'type': 'lead',
                'user_id': False,
                'description': f"Created Time: {fb_lead.get('created_time')}\n",
            }

            for field in fb_lead.get('field_data', []):
                name = field['name']
                val = field['values'][0] if field.get('values') else ''
                if name == 'full_name':
                    lead_data['contact_name'] = val
                elif name == 'email':
                    lead_data['email_from'] = val
                elif name == 'phone_number':
                    lead_data['phone'] = val
                lead_data['description'] += f"{name}: {val}\n"

            crm_lead_env.create(lead_data)
            created_count += 1

        actual_count = crm_lead_env.search_count([('fb_form_id', '=', self.id)])
        msg = (
            f"Facebook returned: {len(all_leads)} lead(s)\n"
            f"Created: {created_count}\n"
            f"Skipped (fb_lead_id exists): {skipped_count - len(duplicate_phones)}\n"
            f"Skipped (phone duplicate): {len(duplicate_phones)}\n"
            f"Total leads in Odoo: {actual_count}"
        )
        if duplicate_phones:
            msg += "\n\nDuplicate phones:\n" + "\n".join(duplicate_phones)

        self.sudo().write({
            'last_fetch_summary': msg,
            'leads_count': actual_count,
        })
        self.env.cr.commit()

        if created_count == 0:
            raise UserError(msg)

        return {
            'type': 'ir.actions.act_window',
            'name': 'CRM Leads',
            'res_model': 'crm.lead',
            'view_mode': 'kanban,list,form',
            'views': [(False, 'kanban'), (False, 'list'), (False, 'form')],
            'domain': [('fb_form_id', '=', self.id)],
            'context': {'default_type': 'lead', 'default_fb_form_id': self.id},
        }


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    fb_lead_id = fields.Char(string="Facebook Lead ID", index=True)
    fb_form_id = fields.Many2one('crm.facebook.leadform', string="Facebook Lead Form")
    fb_campaign_id = fields.Many2one(related='fb_form_id.campaign_id', string="Facebook Campaign", store=True)
    raw_data = fields.Text(string="Raw Data (Facebook)", help="Raw data from Facebook LeadGen webhook/API")
