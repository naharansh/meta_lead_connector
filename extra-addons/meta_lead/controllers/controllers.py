# from odoo import http


# class MetaLead(http.Controller):
#     @http.route('/meta_lead/meta_lead', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/meta_lead/meta_lead/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('meta_lead.listing', {
#             'root': '/meta_lead/meta_lead',
#             'objects': http.request.env['meta_lead.meta_lead'].search([]),
#         })

#     @http.route('/meta_lead/meta_lead/objects/<model("meta_lead.meta_lead"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('meta_lead.object', {
#             'object': obj
#         })

