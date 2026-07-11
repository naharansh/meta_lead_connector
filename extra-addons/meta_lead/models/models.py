# from odoo import models, fields, api


# class meta_lead(models.Model):
#     _name = 'meta_lead.meta_lead'
#     _description = 'meta_lead.meta_lead'

#     name = fields.Char()
#     value = fields.Integer()
#     value2 = fields.Float(compute="_value_pc", store=True)
#     description = fields.Text()
#
#     @api.depends('value')
#     def _value_pc(self):
#         for record in self:
#             record.value2 = float(record.value) / 100

