from odoo import models, fields

class FacebookMapper(models.Model):
    _name = 'facebook.mapper'
    _description = 'Odoo Facebook Mapper'

    name = fields.Char(string="Mapper Name", required=True)
    crm_field_id = fields.Many2one('ir.model.fields', string="CRM Lead Field", domain="[('model_id.model', '=', 'crm.lead')]")
    facebook_field = fields.Char(string="Facebook Field Name")
