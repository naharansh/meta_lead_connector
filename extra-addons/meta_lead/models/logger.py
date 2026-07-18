from odoo import models, fields

class FacebookLogger(models.Model):
    _name = 'facebook.logger'
    _description = 'Facebook Logger'
    _order = 'create_date desc'

    name = fields.Char(string="Name", required=True)
    log_type = fields.Selection([
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error')
    ], string="Log Type", default='info')
    message = fields.Text(string="Message")
    execution_time = fields.Datetime(string="Execution Time", default=fields.Datetime.now)
