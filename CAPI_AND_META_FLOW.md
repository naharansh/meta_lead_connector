# Meta Lead Connector — Detailed Flow Guide (Meta Lead Ads + Meta CAPI)

This document describes, step by step, how the two Odoo 19 addons in `extra-addons/`
work together:

| Module | Path | Direction | Responsibility |
|---|---|---|---|
| **meta_lead** | `extra-addons/meta_lead` | **IN (pull)** | Pull leads from Facebook/Meta Lead Ad forms into Odoo `crm.lead` |
| **crm_facebook_capi** | `extra-addons/crm_facebook_capi` | **OUT (push)** | Push CRM stage progression back to Meta via the Conversions API (Lead Quality) |

`crm_facebook_capi` depends on `meta_lead`, so a lead captured by the first module
can be matched 1:1 when the second module reports its quality back to Meta.

- Graph API base: `https://graph.facebook.com/v25.0` (constant `FB_API` / `GRAPH_V25`)
- Data models use a plain `requests` HTTP client (no SDK).

---

## 0. High-level picture

```
                        ┌───────────────────────────────────────────────┐
                        │                    META / FACEBOOK            │
                        │  Pages · Lead Forms · Leads · Dataset (Pixel) │
                        └───────▲───────────────────────────┬───────────┘
                                │                           │
              GET pulls (leads) │                           │ POST events (CAPI)
                                │                           │
   ┌────────────────────────────┴───────────┐   ┌───────────▼────────────────────────┐
   │            meta_lead  (IN)             │   │   crm_facebook_capi  (OUT)         │
   │  facebook.instance → facebook.page     │   │  stage change → mapping →          │
   │  → crm.facebook.campaign/leadform      │   │  payload → POST /{dataset}/events  │
   │  → crm.lead (fb_lead_id, fb_form_id)   │   │  → facebook.capi.log               │
   └────────────────────────────────────────┘   └────────────────────────────────────┘
                                │                           ▲
                                └──────────► crm.lead ◄──────┘
                                   (stage_id changes trigger CAPI)
```

---

# PART A — Meta Lead Ads ingestion (`meta_lead`)

## A.1 Data model

| Model | Description | Key fields |
|---|---|---|
| `facebook.instance` | A Meta/Business connection | `access_token`, `active_scheduler`, `keep_logs`, `log_scheduler`, `capi_dataset_id`, `capi_access_token` |
| `facebook.page` | A Facebook Page under an instance | `page_id`, `access_token`, `instance_id`, `active_scheduler` (related) |
| `crm.facebook.campaign` | Ad campaign (built from a lead form) | `name`, `fb_form_id`, `page_id`, `status` |
| `crm.facebook.leadform` | A Lead Ad form | `form_id`, `page_id`, `campaign_id`, `pagination_size`, `last_sync_date`, `active_scheduler`, `leads_count`, `last_fetch_summary`, `mapping_line_ids`, `lead_ids` |
| `crm.facebook.leadform.mapping.line` | Facebook question → Odoo field map | `fb_field_name`, `fb_field_label`, `fb_field_type`, `odoo_field_id`, `odoo_field_name`, `description` |
| `facebook.logger` | Audit trail | `name`, `log_type` (`info`/`warning`/`error`), `message`, `execution_time` |
| `facebook.mapper` | Optional global field map | `crm_field_id`, `facebook_field` |
| `crm.lead` (inherited) | Odoo CRM lead | adds `fb_lead_id`, `fb_form_id`, `fb_campaign_id` (related, stored), `raw_data` |

### Relationships

```
facebook.instance (1) ──> (many) facebook.page
                                │
                                v
              crm.facebook.campaign (1) ──> (many) crm.facebook.leadform
                                                        │
                                                        ├── (many) crm.facebook.leadform.mapping.line
                                                        └── (many) crm.lead  (fb_form_id)
```

---

## A.2 Step 1 — Create a Facebook Instance

**Menu:** `Facebook Integration → Configuration → All Facebook Instances → Create`

1. Open **Facebook Integration → All Facebook Instances** and click **Create**.
2. Paste a **User Access Token** into `Access Token` (from
   [Graph API Explorer](https://developers.facebook.com/tools/explorer/)).
3. Required Meta permissions: `leads_retrieval`, `pages_show_list`,
   `pages_read_engagement`, `pages_manage_metadata`.
4. Enable **Active Scheduler** if you want the automatic cron to fetch leads
   (the cron only picks forms whose page's instance also has this flag on).
5. Save.

> The instance object is also where the **CAPI Dataset ID** and **CAPI Access Token**
> live (see Part B). Keeping credentials on the instance means both directions share
> one Facebook configuration.

---

## A.3 Step 2 — Sync Facebook Pages

**Button:** `Sync Facebook Page` on the instance form → `action_sync_facebook_pages()`

1. Odoo calls `GET /me/accounts?fields=id,name,access_token`.
2. For each returned page it **creates or updates** a `facebook.page` record:
   `name`, `page_id`, `access_token`, `instance_id`.
3. The instance is marked `is_connected = True`.
4. A `facebook.logger` entry (`Page Sync`) is written.
5. Errors (e.g. bad token) set `is_connected = False` and raise a `UserError`
   with the Meta error message.

---

## A.4 Step 3 — Sync Lead Forms (+ campaigns)

**Buttons:**
- `Sync Campaigns` on a campaign form / list → `crm.facebook.campaign.action_fetch_leadforms()`
- `Sync Facebook Forms` on a page → `facebook.page.action_sync_facebook_forms()`

1. Odoo calls `GET /{page_id}/leadgen_forms?fields=id,name,status,created_time,leads_count`.
2. It follows `paging.next` until all pages are consumed.
3. For each form:
   - a `crm.facebook.campaign` is created (if new) keyed by `fb_form_id`;
   - a `crm.facebook.leadform` is created/updated with `form_id`, `campaign_id`,
     `page_id`, `leads_count`, `created_time`.
4. Facebook datetimes are normalized to naive UTC by `_parse_fb_datetime`.
5. A `facebook.logger` entry (`Campaign/Form Sync`) records the count and any errors.

---

## A.5 Step 4 — Import Fields (build the mapping)

**Button:** `Import Fields` on a lead form → `action_import_fields()`

1. Odoo calls `GET /{form_id}?fields=questions`.
2. Existing `mapping_line_ids` are deleted and rebuilt.
3. Each question becomes a mapping line. Auto-mapping (`auto_map`):

   | Facebook key | Odoo `crm.lead` field |
   |---|---|
   | `phone_number`, `phone` | `phone` |
   | `email` | `email_from` |
   | `full_name`, `name` | `contact_name` |
   | `website` | `website` |
   | `company` | `partner_name` |
   | CUSTOM / unmapped | `description` |

4. Unmapped questions are written into the `description` text at fetch time.
5. A `facebook.logger` entry (`Import Fields`) is written.
6. Mappings can be adjusted manually afterwards.

> The `phone` mapping is special: `action_fetch_leads()` uses it to locate the phone
> value for deduplication, then falls back to `phone_number` / `phone` keys.

---

## A.6 Step 5 — Fetch Leads

**Buttons:**
- `Fetch Lead` on a lead form (manual) → `action_fetch_leads(silent=False)`
- Cron `Facebook Leads: Auto Fetch` (every 30 min) → `cron_fetch_scheduled_leads()`

### A.6.1 Request

```
GET /{form_id}/leads
  ?fields=id,created_time,field_data
  &limit={pagination_size or 100}
  &filtering=[{"field":"time_created","operator":"GREATER_THAN_OR_EQUAL","value":<last_sync_date unix>}]
```

- The `filtering` clause is added **only** when `last_sync_date` is set → incremental fetch.
- Pagination follows `paging.next` (each page keeps the `filtering` clause).

### A.6.2 Error handling

| Meta error code | Result |
|---|---|
| `10` | "Insufficient privileges to fetch leads" — check `leads_retrieval` + Page Admin |
| `190` | "Access token has expired" — re-sync pages/tokens |
| other | Generic `UserError` with Meta message |

### A.6.3 Deduplication (two layers)

1. **By Facebook lead id** — skips when a `crm.lead` with the same `fb_lead_id` exists.
2. **By normalized phone** — `_normalize_phone()` strips whitespace, `-`, `(`, `)`
   and `+`; matches against existing lead phones (scanned up to 5000 leads).

### A.6.4 Lead creation

For every new Facebook lead:

1. Build base values: `name = "{form name} - {lead_id}"`, `fb_lead_id`, `fb_form_id`,
   `type = lead`, `user_id = False`, `source_id = utm.source "Facebook"` (auto-created).
2. If mapping lines exist → apply them (`odoo_field_name` → value). Every value is also
   appended to `description` as `"<label>: <value>"`.
3. If no mapping lines exist → hardcoded default (`full_name`→`contact_name`,
   `email`→`email_from`, `phone_number`→`phone`).
4. `crm.lead` is created.

### A.6.5 Bookkeeping

- `last_fetch_summary` = counts (returned / created / skipped by id / skipped by phone / total in Odoo)
- `leads_count` = actual leads in Odoo for this form
- `last_sync_date` = latest Facebook `created_time` seen (drives the next incremental fetch)
- `cr` is committed (`self.env.cr.commit()`)
- `facebook.logger` entry (`Fetch Leads`, `info` if created > 0 else `warning`)
- Manual fetch shows a success notification; **silent** fetch (cron) returns the summary string.

### A.6.6 Cron behaviour

`cron_fetch_scheduled_leads()`:

1. Selects forms where `active_scheduler = True` **and** the page's instance
   `active_scheduler = True`.
2. Calls `action_fetch_leads(silent=True)` per form inside a `try/except`.
3. Any exception is written to `facebook.logger` (`Scheduled Fetch Failed`, `error`)
   so one bad form does not stop the others.

---

## A.7 Step 6 — View leads

- **`Facebook Integration → Facebook Leads`** — filtered list of leads with
  `fb_lead_id != False` (`domain=[('fb_lead_id','!=',False)]`).
- **`CRM → Leads`** — all leads; a Facebook lead exposes the **Facebook Lead Info** tab
  with `fb_lead_id`, `fb_form_id`, `fb_campaign_id`, `raw_data`.
- The assignee column is hidden in the CRM lead list for this module's list view.

---

## A.8 Meta Lead — full sequence

```
Admin/User                Odoo (meta_lead)                     Facebook Graph API
    │                          │                                     │
    │ Create Instance + token  │                                     │
    ├─────────────────────────►│                                     │
    │ Sync Facebook Page       │  GET /me/accounts ─────────────────►│
    │                          │◄──────────── pages + page tokens ───│
    │ Sync Facebook Forms      │  GET /{page}/leadgen_forms ────────►│
    │                          │◄──────────── forms (+ paging) ──────│
    │ Import Fields            │  GET /{form}?fields=questions ─────►│
    │                          │◄──────────── questions ─────────────│
    │ Fetch Lead (or cron 30m) │  GET /{form}/leads (+filtering) ───►│
    │                          │◄──────────── leads + field_data ────│
    │                          │  dedupe → create crm.lead           │
    │                          │  write summary/log/counts           │
    │◄── notification / list ──│                                     │
```

---

# PART B — Meta Conversions API push (`crm_facebook_capi`)

## B.1 Data model

| Model | Description | Key fields |
|---|---|---|
| `facebook.capi.service` | `AbstractModel` — the sender | `_hash_pii`, `_prepare_payload`, `_credentials_for`, `_send_event`, `_on_stage_change` |
| `facebook.capi.account` | Per-team dataset routing | `team_id`, `page_id`, `instance_id` (related), `pixel_id`, `access_token`, `is_default`, `is_connected`, `last_test_date` |
| `facebook.capi.stage.mapping` | CRM stage → Meta event | `stage_id`, `stage_sequence`, `event_id`, `event_name`, `value_mode`, `fixed_value`, `action_source`, `active` |
| `facebook.capi.event` | Available Meta events | `name`, `event_type`, `sequence`, `seed_standard_events()`, `fetch_events_from_meta()` |
| `facebook.capi.log` | Every send attempt | `lead_id`, `account_id`, `event_name`, `payload`, `response_code`, `response_body`, `status` |
| `crm.lead` (inherited) | Tracking fields | `x_facebook_last_event`, `x_facebook_event_status`, `x_facebook_sent_date` |
| `res.config.settings` (inherited) | Config params | `facebook_capi.test_event_code`, `facebook_capi.verify_token` |

### CRM stage mapping is exclusive

- `unique(stage_id)` — one CAPI mapping per CRM stage.
- `unique(name)` on `facebook.capi.event` — event names are unique.

### Standard events seeded by `seed_standard_events()`

`QualifiedLead`, `ConvertedLead`, `Lead`, `Contact`, `Schedule`, `SubmitApplication`,
`Purchase`, `CompleteRegistration`, `InitiateCheckout`, `ViewContent`.

> For Meta **Lead Quality** optimization, the two events that matter most are
> `QualifiedLead` (qualified stage) and `ConvertedLead` (won/closed stage).

---

## B.2 Step 1 — Configure CAPI credentials

Credentials can live in **two places**, and `_credentials_for()` resolves them in priority order.

### Option 1 (preferred for Lead Ads) — on the Facebook Instance

**Menu:** `Facebook Integration → Configuration → All Facebook Instances`

On the instance that already owns the pages/forms:

- `CAPI Dataset ID` — Meta Pixel / Dataset ID
- `CAPI Access Token` — System User or Dataset token

Because Lead Ads `leadgen_id` is dataset-scoped, the dataset that receives the
`lead_id` **must** be the same dataset configured on the instance that ingests the lead.

### Option 2 (fallback / manual override) — a CAPI Account

**Menu:** `CRM → Configuration → Meta CAPI Accounts → Create`

- `Sales Team` — routes leads of that team to this dataset (`unique(team_id)`)
- `Facebook Page` — auto-fills `Dataset / Pixel ID` and `CAPI Access Token` from the page's instance
- `Is Default Account` — fallback for leads with no team / no page
- On create, `facebook.capi.event.fetch_events_from_meta()` runs automatically to
  discover the dataset's active events.

### Credential resolution priority (`_credentials_for`)

```
1. lead.fb_form_id → page_id → instance_id
      if instance.capi_dataset_id and instance.capi_access_token  → use them
2. facebook.capi.account where team_id == lead.team_id
3. facebook.capi.account where is_default == True
4. none → status "skipped" + log explaining what to configure
```

---

## B.3 Step 2 — Test the connection

**Buttons:**
- `Test CAPI Connection` on the **Facebook Instance** → `action_test_capi_connection()`
- `Test CAPI Connection` on a **CAPI Account** → `action_test_capi_connection()`

Two-step validation:

1. **Metadata read** — `GET /{dataset_id}?fields=id,name` (needs `ads_management`).
2. **Fallback** — if the read is restricted, POST a probe event to
   `/{dataset_id}/events` with `test_event_code` (default `TEST00000`):

   ```json
   { "data": [ { "event_name": "Lead", "event_time": <now>,
                 "action_source": "website",
                 "user_data": { "em": ["<sha256 of test email>"] } } ],
     "test_event_code": "TEST00000", "access_token": "<token>" }
   ```

3. Success → `is_connected = True`, `last_test_date` set, a `facebook.logger`
   entry and a `facebook.capi.log` entry (`Test Connection`, `sent`) are written.
4. Failure → raises `UserError` with the Meta error; logs a `failed` row.

> Set the real **Test Event Code** in `CRM → Configuration → Settings → Meta CAPI Test Event Code`
> while debugging; leave it blank in production so events go to the live dataset.

---

## B.4 Step 3 — Register events and map CRM stages

### Events

- Seeded automatically (standard list above) and/or
- **`Fetch Meta Events`** (on a CAPI account or stage mapping) →
  `GET /{dataset_id}/stats?aggregation=event`; discovered event names are created as
  `event_type = meta_fetched`.

### Stage mappings

**Menu:** `CRM → Configuration → Meta CAPI Stage Mappings`

For each CRM stage you want to report:

| Field | Meaning |
|---|---|
| `CRM Stage` | The Odoo `crm.stage` that triggers the event |
| `Meta Event` / `Meta Event Name` | e.g. `QualifiedLead`, `Purchase`, `ConvertedLead` |
| `Value Calculation` | `none` / `expected_revenue` / `fixed` |
| `Fixed Value Amount` | used when `value_mode = fixed` |
| `Action Source` | `system_generated` (recommended), `website`, `phone_call`, `business_messaging`, `other` |
| `Active` | only active mappings are used |

> Data file `data/stage_mapping_data.xml` ships two defaults: New → `Lead`,
> Won → `Purchase` (with `expected_revenue`).

---

## B.5 Step 4 — Triggering a send

Sending is triggered from `crm.lead`:

1. **`write()`** watches for:
   - `stage_id` changing → `service._on_stage_change(lead)`
   - `type` changing from `lead` to `opportunity` → `service._on_stage_change(lead)`
2. **Manual** button `action_send_meta_capi()` loops the selected leads and sends each.
   (The button is currently commented out in `views/crm_lead_views.xml`; the method
   still exists for programmatic/back-end use.)
3. **Cron** `Meta CAPI: Purge logs > 30 days` runs `_gc_logs(30)` daily (housekeeping only).

### `_on_stage_change(lead)`

```
find facebook.capi.stage.mapping where stage_id = lead.stage_id and active = True
   ├── none / no event_name → lead.x_facebook_event_status = "skipped"
   │                          + facebook.capi.log (stage has no mapping)
   └── found                → _send_event(lead, mapping)
```

---

## B.6 Step 5 — Build the payload

`_send_event()` → `_hash_pii()` + `_prepare_payload()`.

### User data (`_hash_pii`) — matching keys

| Key | Source | Treatment |
|---|---|---|
| `lead_id` | `lead.fb_lead_id` | **cleartext** integer (deterministic Lead Ads match) |
| `em` | `email_normalized` or `email_from` | lowercased + SHA-256 |
| `ph` | `phone_sanitized` or `phone` | digits only → SHA-256 |
| `fn`, `ln` | `contact_name` (first / last token) | SHA-256 |

Keys with empty values are dropped. If **none** of `lead_id` / `em` / `ph` is present,
the lead is **skipped** (not enough parameters for Meta to match).

### Event body

```json
{
  "data": [
    {
      "event_name": "QualifiedLead",
      "event_time": 1730000000,
      "action_source": "system_generated",
      "event_id": "odoo-crm-<lead_id>-<event_name>-<event_time>",
      "user_data": { "lead_id": 1636039311369996, "em": ["..."], "ph": ["..."], "fn": ["..."], "ln": ["..."] },
      "custom_data": {
        "lead_event_source": "odoo_crm",
        "event_source": "crm",
        "currency": "INR",
        "value": 25000.0
      }
    }
  ],
  "test_event_code": "<optional, only when configured>"
}
```

Notes:

- `action_source = business_messaging` is **coerced to `system_generated`**, because
  automated CRM stage updates are not user-initiated messaging and Meta rejects it otherwise.
- `value` / `currency` are only added when `value_mode` is `expected_revenue`
  (`lead.expected_revenue`) or `fixed` (`mapping.fixed_value`).
- `currency` comes from `lead.company_currency` (default `INR`).
- `event_id` is deterministic per lead + event + timestamp, which lets Meta deduplicate.

---

## B.7 Step 6 — Send, retry, and log

### Endpoint

```
POST https://graph.facebook.com/v25.0/{pixel_id}/events?access_token={token}
```

### Retry policy

| Constant | Value |
|---|---|
| `MAX_RETRIES` | 2 (so max 3 attempts total) |
| `RETRY_DELAY` | 2 seconds |

Retryable when:

- Exception: `requests.ConnectionError`, `requests.Timeout`
- HTTP: `429`, `500`, `502`, `503`, `504`
- Meta error codes: `4`, `17`, `32`, `613`, or `code 190 / subcode 463`

Not retried: HTTP `400`, `401`, `403`, and other Meta errors.

### Success test

`ok = resp.status_code == 200 and "error" not in resp.json()`

### Outcome handling

| Case | `crm.lead` update | `facebook.capi.log` |
|---|---|---|
| No credentials | `x_facebook_event_status = skipped` | `skipped` + configuration hint |
| No match params | `skipped` | `skipped` + "lacks sufficient customer parameters" |
| HTTP/connection failure after retries | `failed` | `failed` + `str(exc)` |
| Response received | `x_facebook_last_event`, `x_facebook_event_status` = `sent`/`failed`, `x_facebook_sent_date = now` | `payload`, `response_code`, `response_body`, `status` |

Result is also shown in the lead's **Meta CAPI Tracking** tab (visible only when
`x_facebook_event_status` is set, to `meta_lead` manager / system groups).

---

## B.8 Meta CAPI — full sequence

```
CRM User / Automation            Odoo (crm_facebook_capi)                Meta Graph API
        │                              │                                       │
        │ move lead to a stage         │                                       │
        ├─────────────────────────────►│ crm.lead.write(stage_id)              │
        │                              │  → _on_stage_change(lead)             │
        │                              │  → find active stage mapping          │
        │                              │  → _credentials_for(lead)             │
        │                              │  → _hash_pii(lead)                    │
        │                              │  → _prepare_payload(lead, mapping)    │
        │                              │  POST /{dataset}/events ─────────────►│
        │                              │◄──── 200 {events_received:1} or error─│
        │                              │  (retry on 429/5xx/4/17/32/613)       │
        │                              │  write lead status + capi.log         │
        │◄── Meta CAPI Tracking tab ───│                                       │
```

---

# PART C — Combined end-to-end flow

A single lead's journey through both modules:

1. **Capture (meta_lead → IN)**
   A user submits a Facebook Lead Ad form → `action_fetch_leads()` (or the 30-min cron)
   pulls it → dedup by `fb_lead_id` / phone → `crm.lead` created with
   `fb_lead_id`, `fb_form_id`, `source = Facebook`.

2. **Credential inheritance**
   The instance that ingested the lead already carries `CAPI Dataset ID` +
   `CAPI Access Token`, so `_credentials_for()` resolves the **same dataset** for
   the outbound event.

3. **Qualification (CRMs stage change)**
   A salesperson moves the lead to the *Qualified* stage (mapped to `QualifiedLead`).

4. **Match (crm_facebook_capi → OUT)**
   The event is enriched with `lead_id` (cleartext, from `fb_lead_id`) plus hashed
   `em` / `ph` / `fn` / `ln` → Meta matches the conversion back to the original ad click.

5. **Send + audit**
   `POST /{dataset}/events` returns 200 → lead shows **Sent**, `facebook.capi.log`
   stores the hashed payload and Meta's response.

6. **Close (optional)**
   Lead moves to *Won* (mapped to `Purchase`/`ConvertedLead` with
   `expected_revenue`) → a second event with `value` + `currency` is sent.

---

## Menu reference

| Location | Item | Purpose |
|---|---|---|
| Facebook Integration | All Facebook Instances | Instance + token + CAPI credentials |
| Facebook Integration → Configuration | Odoo Facebook Pages | Synced pages |
| Facebook Integration | Facebook Forms | Lead forms; Import Fields / Fetch Lead |
| Facebook Integration | Facebook Leads | Leads with `fb_lead_id` |
| Facebook Integration | Facebook Logger | Audit trail (`facebook.logger`) |
| Facebook Integration | Odoo Facebook Mapper | Optional global field map |
| CRM → Configuration | Meta CAPI Accounts | Per-team dataset routing |
| CRM → Configuration | Meta CAPI Stage Mappings | Stage → event mapping |
| CRM → Configuration | Meta CAPI Event Logs | `facebook.capi.log` browser |
| CRM → Configuration → Settings | Meta CAPI Test Event Code / Webhook Verify Token | Config parameters |

---

## Configuration parameters

| System parameter | Set via | Purpose |
|---|---|---|
| `facebook_capi.test_event_code` | Settings | Routes events to Meta Test Events while debugging; blank in production |
| `facebook_capi.verify_token` | Settings | Secret for Meta Lead Ads webhook verification (optional; webhooks currently inactive) |

---

## Status lifecycle on a lead

```
        (write: stage_id / type change)
                     │
        ┌────────────┴─────────────┐
        │  no stage mapping        │  no credentials / no email·phone·lead_id
        ▼                          ▼
     skipped ◄────────────────  skipped
        │
   mapping found ─► send ─► 200 & no error ─► sent
                          └─ error / timeout ─► failed  (retried up to 3 attempts)
```

---

## Quick troubleshooting

| Symptom | Where to look |
|---|---|
| Lead never arrives from Meta | `facebook.logger` (`Fetch Leads`), error codes 10 / 190 in Part A.6.2 |
| Duplicate leads skipped | `last_fetch_summary` ("Skipped (phone duplicate)") |
| Lead stuck at `skipped` (CAPI) | `facebook.capi.log` `response_body` — usually missing dataset/token or missing `em`/`ph`/`lead_id` |
| Lead `failed` | `response_code` + `response_body` in `facebook.capi.log`; check token expiry / dataset permissions |
| Events not counted in Meta | Confirm `test_event_code` is **blank** in production; verify events appear under the right Dataset |
| Stage triggers nothing | A **active** `Meta CAPI Stage Mappings` row must exist for that exact `crm.stage` |
