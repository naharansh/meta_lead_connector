import hashlib
import json
import logging
import re
import time
import requests
from odoo import api, fields, models

_logger = logging.getLogger(__name__)
GRAPH_V25 = "https://graph.facebook.com/v25.0"


class FacebookCapiService(models.AbstractModel):
    _name = "facebook.capi.service"
    _description = "Meta Conversions API Sender Service for CRM Lead Quality Events"

    def _cfg(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param("facebook_capi.%s" % key, default)

    def _hash(self, value):
        if not value:
            return None
        return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()

    def _hash_pii(self, lead):
        """Return Meta user_data dictionary.
        lead_id is sent in CLEARTEXT (integer) for Facebook Lead Ads matching.
        em, ph, fn, ln are lowercased and SHA256 hashed.
        """
        ud = {}

        # Deterministic match key for FB Lead Ads leads (cleartext — NOT hashed)
        if hasattr(lead, "fb_lead_id") and lead.fb_lead_id:
            try:
                ud["lead_id"] = int(lead.fb_lead_id)
            except (ValueError, TypeError):
                ud["lead_id"] = lead.fb_lead_id

        # Email — pre-normalized field (pre-lowercased/trimmed by Odoo)
        em = self._hash(lead.email_normalized or lead.email_from)
        if em:
            ud["em"] = [em]

        # Phone — sanitized E.164 or raw, strip all non-digits before hashing
        phone = lead.phone_sanitized or lead.phone
        if phone:
            digits = re.sub(r"\D", "", phone)
            if digits:
                ud["ph"] = [hashlib.sha256(digits.encode("utf-8")).hexdigest()]

        # Contact Name split (fn / ln)
        name = (lead.contact_name or "").strip()
        if name:
            parts = name.split()
            ud["fn"] = [self._hash(parts[0])]
            if len(parts) > 1:
                ud["ln"] = [self._hash(parts[-1])]

        return {k: v for k, v in ud.items() if v}

    def _prepare_payload(self, lead, mapping, user_data=None):
        action_source = mapping.action_source or "system_generated"
        # Meta CAPI requires custom_data.messaging_channel for business_messaging.
        # CRM automated stage updates must use system_generated to prevent Meta 400 error.
        if action_source == "business_messaging":
            action_source = "system_generated"

        event_time = int(time.time())
        event = {
            "event_name": mapping.event_name,
            "event_time": event_time,
            "action_source": action_source,
            "event_id": "odoo-crm-%s-%s-%s" % (lead.id, mapping.event_name, event_time),
            "user_data": user_data if user_data is not None else self._hash_pii(lead),
        }

        custom_data = {
            "lead_event_source": "odoo_crm",
            "event_source": "crm",
        }

        value = None
        if mapping.value_mode == "expected_revenue":
            value = lead.expected_revenue or 0.0
        elif mapping.value_mode == "fixed":
            value = mapping.fixed_value or 0.0

        if value is not None:
            currency = lead.company_currency.name if lead.company_currency else "INR"
            custom_data["currency"] = currency
            custom_data["value"] = round(value, 2)

        event["custom_data"] = custom_data

        body = {"data": [event]}
        test_code = self._cfg("test_event_code")
        if test_code:
            body["test_event_code"] = test_code.strip()
        return body

    def _credentials_for(self, lead):
        """Resolve CAPI credentials for this lead.

        Priority:
        1. lead.fb_form_id → page_id → instance_id (capi_dataset_id + capi_access_token)
           — deterministic for FB Lead Ads; leadgen_id is dataset-scoped so this MUST match.
        2. facebook.capi.account by team_id → pixel_id + access_token
           — manual override for non-FB leads or when Page/Instance lacks CAPI creds.
        """
        # 1. First attempt: resolve from the existing Facebook page/form ingestion chain
        lead_sudo = lead.sudo()
        if hasattr(lead_sudo, "fb_form_id") and lead_sudo.fb_form_id:
            inst = lead_sudo.fb_form_id.page_id.instance_id
            if inst and inst.capi_dataset_id and inst.capi_access_token:
                # Find matching account record if exists, just for logging linkage
                acc = self.env["facebook.capi.account"].sudo().search([
                    ("pixel_id", "=", inst.capi_dataset_id.strip())
                ], limit=1)
                return {
                    "pixel_id": inst.capi_dataset_id.strip(),
                    "access_token": inst.capi_access_token.strip(),
                    "account_id": acc.id if acc else False,
                }

        # 2. Second attempt: resolve from team-routed facebook.capi.account
        acc = None
        if lead.team_id:
            acc = self.env["facebook.capi.account"].sudo().search([
                ("team_id", "=", lead.team_id.id),
            ], limit=1)

        # Fallback to default CAPI account
        if not acc:
            acc = self.env["facebook.capi.account"].sudo().search([
                ("is_default", "=", True)
            ], limit=1)

        if acc and acc.pixel_id and acc.access_token:
            return {
                "pixel_id": acc.pixel_id.strip(),
                "access_token": acc.access_token.strip(),
                "account_id": acc.id,
            }

        return None

    MAX_RETRIES = 2
    RETRY_DELAY = 2

    _RETRYABLE_FB_CODES = {4, 17, 32, 613}

    def _is_retryable(self, resp=None, exc=None):
        if exc is not None:
            return isinstance(exc, (requests.ConnectionError, requests.Timeout))
        if resp is None:
            return False
        if resp.status_code in (429, 500, 502, 503, 504):
            return True
        if resp.status_code in (400, 401, 403):
            return False
        try:
            data = resp.json()
            err = data.get("error", {})
            code = err.get("code", 0)
            if code in self._RETRYABLE_FB_CODES:
                return True
            subcode = err.get("error_subcode", 0)
            if code == 190 and subcode == 463:
                return True
        except Exception:
            pass
        return False

    def _send_event(self, lead, mapping):
        Log = self.env["facebook.capi.log"].sudo()
        creds = self._credentials_for(lead)

        if not creds:
            lead.write({"x_facebook_event_status": "skipped"})
            team_name = lead.team_id.name if lead.team_id else "No Team"
            Log.create({
                "lead_id": lead.id,
                "event_name": mapping.event_name,
                "status": "skipped",
                "response_body": (
                    "No CAPI credentials found for team '%s'. "
                    "Set CAPI Dataset ID + Token on the Facebook Instance "
                    "(Facebook Integration → Instances), or add a Meta CAPI Account." % team_name
                ),
            })
            return False

        user_data = self._hash_pii(lead)
        if not user_data.get("lead_id") and not user_data.get("em") and not user_data.get("ph"):
            lead.write({"x_facebook_event_status": "skipped"})
            Log.create({
                "lead_id": lead.id,
                "account_id": creds.get("account_id", False),
                "event_name": mapping.event_name,
                "status": "skipped",
                "response_body": (
                    "Skipped: Lead lacks sufficient customer parameters for Meta CAPI "
                    "(requires fb_lead_id, email, or phone number to match with Meta)."
                ),
            })
            return False

        body = self._prepare_payload(lead, mapping, user_data=user_data)
        url = "%s/%s/events?access_token=%s" % (
            GRAPH_V25, creds["pixel_id"], creds["access_token"]
        )

        last_resp = None
        last_exc = None

        for attempt in range(1, self.MAX_RETRIES + 2):
            try:
                resp = requests.post(url, json=body, timeout=15)
                resp_data = resp.json() if resp.text else {}
                ok = resp.status_code == 200 and "error" not in resp_data

                if ok:
                    last_resp = resp
                    last_exc = None
                    break

                if self._is_retryable(resp=resp) and attempt <= self.MAX_RETRIES:
                    _logger.warning(
                        "Meta CAPI retryable error (attempt %d/%d) for lead ID %s: "
                        "HTTP %s - %s",
                        attempt, self.MAX_RETRIES + 1, lead.id,
                        resp.status_code, (resp.text or "")[:300],
                    )
                    last_resp = resp
                    last_exc = None
                    time.sleep(self.RETRY_DELAY)
                    continue

                last_resp = resp
                last_exc = None
                break

            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt <= self.MAX_RETRIES:
                    _logger.warning(
                        "Meta CAPI retryable exception (attempt %d/%d) for lead ID %s: %s",
                        attempt, self.MAX_RETRIES + 1, lead.id, exc,
                    )
                    last_resp = None
                    last_exc = exc
                    time.sleep(self.RETRY_DELAY)
                    continue
                last_resp = None
                last_exc = exc
                break

        if last_exc is not None:
            lead.write({"x_facebook_event_status": "failed"})
            Log.create({
                "lead_id": lead.id,
                "account_id": creds.get("account_id", False),
                "event_name": mapping.event_name,
                "status": "failed",
                "response_body": str(last_exc)[:2000],
            })
            _logger.exception("Meta CAPI send failed for lead ID %s after %d attempts", lead.id, self.MAX_RETRIES + 1)
            return False

        resp = last_resp
        resp_data = resp.json() if resp.text else {}
        ok = resp.status_code == 200 and "error" not in resp_data
        lead.write({
            "x_facebook_last_event": mapping.event_name,
            "x_facebook_event_status": "sent" if ok else "failed",
            "x_facebook_sent_date": fields.Datetime.now(),
        })
        Log.create({
            "lead_id": lead.id,
            "account_id": creds.get("account_id", False),
            "event_name": mapping.event_name,
            "payload": json.dumps(body),
            "response_code": resp.status_code,
            "response_body": (resp.text or "")[:2000],
            "status": "sent" if ok else "failed",
        })
        return ok

    def _on_stage_change(self, lead):
        lead = lead.sudo()
        mapping = self.env["facebook.capi.stage.mapping"].sudo().search(
            [("stage_id", "=", lead.stage_id.id), ("active", "=", True)],
            limit=1,
        )
        if not mapping or not mapping.event_name:
            lead.write({"x_facebook_event_status": "skipped"})
            stage_name = lead.stage_id.name or "No Stage"
            self.env["facebook.capi.log"].sudo().create({
                "lead_id": lead.id,
                "event_name": f"Stage: {stage_name}",
                "status": "skipped",
                "response_body": (
                    f"Skipped: Stage '{stage_name}' has no active Meta CAPI mapping configured. "
                    "Configure in CRM → Configuration → Meta CAPI Stage Mappings."
                ),
            })
            return
        self._send_event(lead, mapping)
