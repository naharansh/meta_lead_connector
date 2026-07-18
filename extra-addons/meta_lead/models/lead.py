from odoo import models, fields, api
from odoo.exceptions import UserError
import json
import re
import requests
from datetime import timezone as _tz

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

    def action_fetch_leadforms(self, page=None):
        """Fetch lead forms for the configured page(s)"""
        if page:
            pages = page
        else:
            pages = self.env['facebook.page'].search([('page_id', '!=', False), ('access_token', '!=', False)])

        if not pages:
            raise UserError("⚠️ No Facebook pages configured or synced.")

        leadform_model = self.env['crm.facebook.leadform']
        synced_count = 0
        errors = []

        for p in pages:
            page_id = p.page_id
            access_token = p.access_token

            url = f"{FB_API}/{page_id}/leadgen_forms"
            try:
                resp = requests.get(url, params={
                    'access_token': access_token,
                    'fields': 'id,name,status,leads_count'
                }, timeout=15).json()
            except Exception as e:
                errors.append(f"Page {p.name}: request failed: {e}")
                continue

            if 'error' in resp:
                err = resp['error']
                errors.append(f"Page {p.name}: {err.get('message')}")
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
                    'page_id': p.id,
                    'leads_count': form.get('leads_count', 0),
                }
                if leadform:
                    leadform.write(vals)
                else:
                    leadform_model.create(vals)

            synced_count += 1

        msg = f"Synced {synced_count} page(s)."
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors)

        # Log action
        self.env['facebook.logger'].create({
            'name': 'Campaign/Form Sync',
            'log_type': 'info' if synced_count > 0 else 'error',
            'message': msg
        })

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

    name = fields.Char(required=True, string="Form Name")
    form_id = fields.Char(string="Form ID", required=True)
    campaign_id = fields.Many2one('crm.facebook.campaign', string="Campaign")
    page_id = fields.Many2one('facebook.page', string="Facebook Page")
    instance_id = fields.Many2one('facebook.instance', related='page_id.instance_id', string="Facebook Instance", store=True)
    
    pagination_size = fields.Integer(string="Pagination Size", default=2)
    last_sync_date = fields.Datetime(string="Last Sync Date")
    active_scheduler = fields.Boolean(string="Active Scheduler", default=False)
    
    leads_count = fields.Integer(string="Leads Count", default=0)
    last_fetched = fields.Datetime(string="Last Fetched Time", default=False)
    last_fetch_summary = fields.Text(string="Last Fetch Summary")
    lead_ids = fields.One2many('crm.lead', 'fb_form_id', string="Fetched Leads")
    
    mapping_line_ids = fields.One2many(
        'crm.facebook.leadform.mapping.line',
        'leadform_id',
        string="Fields Mapping"
    )

    @staticmethod
    def _normalize_phone(phone):
        """Strip spaces, dashes, parentheses and leading + for comparison"""
        if not phone:
            return ''
        return re.sub(r'[\s\-\(\)\+]', '', phone)

    def action_import_fields(self):
        self.ensure_one()
        page = self.page_id
        if not page or not page.access_token:
            raise UserError("⚠️ No Facebook page or page access token found for this lead form.")

        url = f"{FB_API}/{self.form_id}"
        params = {
            'access_token': page.access_token,
            'fields': 'questions'
        }
        try:
            resp = requests.get(url, params=params, timeout=15).json()
        except Exception as e:
            raise UserError(f"Request failed: {e}")

        if 'error' in resp:
            raise UserError(f"Facebook API Error: {resp['error'].get('message')}")

        questions = resp.get('questions', [])
        MappingLine = self.env['crm.facebook.leadform.mapping.line']

        # Clear existing mapping lines
        self.mapping_line_ids.unlink()

        IrModelFields = self.env['ir.model.fields']
        
        # A dictionary to auto-map common facebook keys to CRM field technical names
        auto_map = {
            'phone_number': 'phone',
            'phone': 'phone',
            'email': 'email_from',
            'full_name': 'contact_name',
            'name': 'contact_name',
            'website': 'website',
            'company': 'partner_name',
        }

        description_field = IrModelFields.search([
            ('model_id.model', '=', 'crm.lead'),
            ('name', '=', 'description')
        ], limit=1)

        for q in questions:
            fb_key = q.get('key')
            fb_label = q.get('label', '')
            fb_type = q.get('type', 'CUSTOM')

            crm_field_name = auto_map.get(fb_key)
            if not crm_field_name and fb_type == 'CUSTOM':
                crm_field_name = 'description'

            crm_field = False
            if crm_field_name:
                crm_field = IrModelFields.search([
                    ('model_id.model', '=', 'crm.lead'),
                    ('name', '=', crm_field_name)
                ], limit=1)

            MappingLine.create({
                'leadform_id': self.id,
                'fb_field_label': fb_label,
                'fb_field_name': fb_key,
                'fb_field_type': fb_type,
                'odoo_field_id': crm_field.id if crm_field else False,
                'odoo_field_label': crm_field.field_description if crm_field else fb_label,
            })

        # Log action
        self.env['facebook.logger'].create({
            'name': 'Import Fields',
            'log_type': 'info',
            'message': f"Imported {len(questions)} fields for form {self.name}."
        })

        return True

    def action_fetch_leads(self):
        """Fetch leads for this form and create crm.lead records"""
        self.ensure_one()
        page = self.page_id
        if not page or not page.access_token:
            raise UserError("⚠️ No Facebook page or page access token found for this lead form.")

        access_token = page.access_token
        url = f"{FB_API}/{self.form_id}/leads"

        limit = self.pagination_size or 100

        # Build base params — apply filtering from last_sync_date to only fetch that day's leads
        params = {
            'access_token': access_token,
            'fields': 'id,created_time,field_data',
            'limit': limit,
        }
        filtering = []
        if self.last_sync_date:
            import datetime as dt
            day_start = self.last_sync_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_tz.utc)
            day_end = day_start + dt.timedelta(days=1)
            filtering = [
                {"field": "time_created", "operator": "GREATER_THAN_OR_EQUAL", "value": int(day_start.timestamp())},
                {"field": "time_created", "operator": "LESS_THAN", "value": int(day_end.timestamp())},
            ]
            params['filtering'] = json.dumps(filtering)

        all_leads = []
        next_url = None
        while True:
            try:
                if next_url:
                    page_params = {'access_token': access_token}
                    if filtering:
                        page_params['filtering'] = json.dumps(filtering)
                    resp = requests.get(next_url, params=page_params, timeout=15).json()
                else:
                    resp = requests.get(url, params=params, timeout=15).json()
            except Exception as e:
                raise UserError(f"Request failed: {e}")

            if 'error' in resp:
                err = resp['error']
                err_code = err.get('code')
                if err_code == 10:
                    raise UserError(
                        "❌ Insufficient privileges to fetch leads.\n\n"
                        "Please verify the system user has leads retrieval permission for the page.")
                if err_code == 190:
                    raise UserError("❌ Access token has expired. Please sync pages/tokens again.")
                raise UserError(f"❌ Facebook API Error: {err.get('message', err)}")

            all_leads.extend(resp.get('data', []))
            paging = resp.get('paging', {})
            next_url = paging.get('next')
            if not next_url:
                break

        sync_filter_msg = ""
        if filtering:
            sync_filter_msg = f" (filtering time_created >= {filtering[0]['value']} AND time_created < {filtering[1]['value']})"

        if not all_leads:
            raise UserError(f"✅ No new leads found on Facebook for this form{sync_filter_msg}.")

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
            lead_id = str(fb_lead['id'])
            existing_lead = crm_lead_env.search([('fb_lead_id', '=', lead_id)], limit=1)
            if existing_lead:
                skipped_count += 1
                continue

            # Determine phone value dynamically or fallback
            phone = ''
            if self.mapping_line_ids:
                phone_mapping = self.mapping_line_ids.filtered(lambda l: l.odoo_field_name == 'phone')
                if phone_mapping:
                    for field in fb_lead.get('field_data', []):
                        if field['name'] == phone_mapping[0].fb_field_name:
                            phone = field['values'][0] if field.get('values') else ''
                            break
            if not phone:
                for field in fb_lead.get('field_data', []):
                    if field['name'] in ('phone_number', 'phone'):
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
            }
            lead_description = f"Created Time: {fb_lead.get('created_time')}\n"

            # Apply mapping logic
            if self.mapping_line_ids:
                field_vals = {}
                for field in fb_lead.get('field_data', []):
                    name = field['name']
                    val = field['values'][0] if field.get('values') else ''
                    field_vals[name] = val

                for line in self.mapping_line_ids:
                    fb_key = line.fb_field_name
                    val = field_vals.get(fb_key, '')
                    
                    if line.odoo_field_name:
                        if line.odoo_field_name == 'description':
                            lead_description += f"{line.fb_field_label or fb_key}: {val}\n"
                        else:
                            lead_data[line.odoo_field_name] = val
                    
                    if line.odoo_field_name != 'description':
                        lead_description += f"{line.fb_field_label or fb_key}: {val}\n"
            else:
                for field in fb_lead.get('field_data', []):
                    name = field['name']
                    val = field['values'][0] if field.get('values') else ''
                    if name == 'full_name':
                        lead_data['contact_name'] = val
                    elif name == 'email':
                        lead_data['email_from'] = val
                    elif name == 'phone_number':
                        lead_data['phone'] = val
                    lead_description += f"{name}: {val}\n"

            lead_data['description'] = lead_description
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

        # Log action
        self.env['facebook.logger'].create({
            'name': 'Fetch Leads',
            'log_type': 'info' if created_count > 0 else 'warning',
            'message': f"Form: {self.name} (ID: {self.form_id})\n{msg}"
        })

        self.env.cr.commit()

        if created_count == 0:
            raise UserError(msg)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': msg,
                'type': 'success',
                'sticky': True,
            },
        }


class FacebookLeadFormMappingLine(models.Model):
    _name = 'crm.facebook.leadform.mapping.line'
    _description = 'Facebook Lead Form Mapping Line'

    leadform_id = fields.Many2one('crm.facebook.leadform', string="Lead Form", ondelete='cascade')
    odoo_field_id = fields.Many2one('ir.model.fields', string="Odoo Field", domain="[('model_id.model', '=', 'crm.lead')]")
    odoo_field_name = fields.Char(related='odoo_field_id.name', string="Odoo Fields", store=True)
    odoo_field_type = fields.Char(compute='_compute_odoo_field_type', string="Odoo Fields Type", store=True)
    odoo_field_label = fields.Char(string="Odoo Fields Label")
    
    fb_field_label = fields.Char(string="Facebook Fields Label")
    fb_field_name = fields.Char(string="Facebook Fields")
    fb_field_type = fields.Char(string="Facebook Fields Type")
    description = fields.Char(string="Description")

    @api.depends('odoo_field_id')
    def _compute_odoo_field_type(self):
        for line in self:
            if not line.odoo_field_id:
                line.odoo_field_type = ''
                continue
            
            ttype = line.odoo_field_id.ttype
            # Map Odoo types to PostgreSQL-like database types as shown in screenshot
            if ttype in ('char', 'text', 'selection', 'many2one'):
                line.odoo_field_type = 'character varying'
            elif ttype == 'boolean':
                line.odoo_field_type = 'boolean'
            elif ttype == 'integer':
                line.odoo_field_type = 'integer'
            elif ttype in ('datetime', 'date'):
                line.odoo_field_type = 'timestamp without time zone'
            else:
                line.odoo_field_type = ttype

    @api.onchange('odoo_field_id')
    def _onchange_odoo_field_id(self):
        if self.odoo_field_id:
            self.odoo_field_label = self.odoo_field_id.field_description


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    fb_lead_id = fields.Char(string="Facebook Lead ID", index=True)
    fb_form_id = fields.Many2one('crm.facebook.leadform', string="Facebook Lead Form")
    fb_campaign_id = fields.Many2one(related='fb_form_id.campaign_id', string="Facebook Campaign", store=True)
    raw_data = fields.Text(string="Raw Data (Facebook)", help="Raw data from Facebook LeadGen webhook/API")
