{
    "name": "CRM → Meta Conversions API (Lead Quality)",
    "version": "19.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Send CRM stage progression to Meta Conversions API for Conversion Leads optimization",
    "description": """
Meta Conversions API (CAPI) integration for Odoo CRM.
Track CRM lead stage transitions (QualifiedLead, Purchase, etc.) back to Meta.
Supports 1:1 deterministic lead_id matching for FB Lead Ads and hashed PII matching for web leads.
Supports per-team multi-dataset routing (e.g. YuvMedia vs YuvTrainings).
    """,
    "author": "YuvMedia",
    "website": "https://www.yuvmedia.com",
    "license": "LGPL-3",
    "depends": [
        "base",
        "crm",
        "meta_lead",
    ],
    "external_dependencies": {
        "python": ["requests"]
    },
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/stage_mapping_views.xml",
        "views/capi_account_views.xml",
        "views/capi_log_views.xml",
        "views/capi_event_views.xml",
        "views/crm_lead_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
