# Meta Lead Connector - App Flow

## Overview

An Odoo 19 module that connects **Facebook/Meta Lead Ads** with **Odoo CRM**. It pulls leads from Facebook Lead Ad Forms and creates them as CRM leads inside Odoo.

---

## Step-by-Step Flow

### Step 1: App Startup

1. Docker Compose starts the **Odoo 19** container (`meta_lead`).
2. Odoo connects to the **PostgreSQL** database (`facebook_odoo_integration`).
3. The `meta_lead` module loads automatically from `/mnt/extra-addons`.
4. Security groups, access rules, menus, and views are registered.

---

### Step 2: Create a Facebook Instance

1. Go to **Facebook Integration > All Facebook Instances**.
2. Click **Create** to add a new Facebook Instance.
3. Paste your **User Access Token** (obtained from [Graph API Explorer](https://developers.facebook.com/tools/explorer/)).
4. Required Facebook permissions: `leads_retrieval`, `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`.

---

### Step 3: Sync Facebook Pages

1. On the Facebook Instance form, click **"Sync Facebook Page"**.
2. The app calls the **Facebook Graph API**: `GET /me/accounts`
3. For each Facebook Page returned:
   - A `facebook.page` record is **created or updated** in Odoo.
   - Each page stores its **Page ID** and **Page Access Token**.
4. The instance is marked as **Connected** (`is_connected = True`).
5. A log entry is created in **Facebook Logger**.

---

### Step 4: Sync Lead Forms

1. Go to **Facebook Integration > Odoo Facebook Pages**.
2. Open a synced Facebook Page.
3. Click **"Sync Facebook Forms"**.
4. The app calls the **Facebook Graph API**: `GET /{page_id}/leadgen_forms`
5. For each lead form returned:
   - A `crm.facebook.campaign` record is **created** (the ad campaign).
   - A `crm.facebook.leadform` record is **created or updated** (the lead form).
   - Each lead form is linked to its campaign and page.
6. A log entry is created.

---

### Step 5: Import Fields (Optional but Recommended)

1. Go to **Facebook Integration > Odoo Facebook Forms**.
2. Open a specific Lead Form.
3. Click **"Import Fields"**.
4. The app calls the **Facebook Graph API**: `GET /{form_id}?fields=questions`
5. For each question/field in the form:
   - A mapping line (`crm.facebook.leadform.mapping.line`) is created.
   - Common fields are **auto-mapped** to Odoo CRM fields:

     | Facebook Field Key | Odoo CRM Field |
     |---|---|
     | `phone_number`, `phone` | `phone` |
     | `email` | `email_from` |
     | `full_name`, `name` | `contact_name` |
     | `website` | `website` |
     | `company` | `partner_name` |
     | (unmapped / CUSTOM) | `description` |

6. You can manually adjust the mappings if needed.
7. A log entry is created.

---

### Step 6: Fetch Leads

1. Open the same Lead Form.
2. Click **"Fetch Lead"**.
3. The app calls the **Facebook Graph API**: `GET /{form_id}/leads`
4. **Pagination**: The app follows `paging.next` URLs to fetch all pages of results.
5. **Deduplication**:
   - Skips leads where `fb_lead_id` already exists in Odoo CRM.
   - Skips leads where the **phone number** (normalized) already exists in Odoo CRM.
6. For each new lead:
   - A `crm.lead` record is **created** in Odoo.
   - Fields are populated based on the **mapping lines** from Step 5.
   - If no mapping exists, a **hardcoded default mapping** is used.
   - The `description` field stores all field values as plain text.
7. The form's `last_fetch_summary` and `leads_count` are updated.
8. Changes are committed to the database.
9. A log entry is created.
10. A success/error notification is shown.

---

### Step 7: View Leads in CRM

1. Go to **Facebook Integration > Facebook Leads** to see only Facebook-sourced leads.
2. Or go to **CRM > Leads** to see all leads (Facebook leads have a "Facebook Lead Info" tab).
3. Each Facebook lead shows:
   - `fb_lead_id` (Facebook's unique lead ID)
   - `fb_form_id` (the form it came from)
   - `fb_campaign_id` (the campaign it belongs to)
   - `raw_data` (raw JSON, if populated)

---

## Data Model Relationships

```
facebook.instance (1) ──> (many) facebook.page
                                │
                                v
                      crm.facebook.campaign (1) ──> (many) crm.facebook.leadform
                                                          │
                                                          v
                                              crm.facebook.leadform.mapping.line (many)
                                                          │
                                                          v
                                                    crm.lead (Odoo CRM)
```

---

## Menu Structure

```
Facebook Integration
  ├── All Facebook Instances     -- Manage Facebook accounts & access tokens
  ├── Odoo Facebook Pages        -- View synced Facebook Pages
  ├── Odoo Facebook Forms        -- View & manage Lead Forms, fetch leads
  ├── Facebook Logger            -- Audit trail of all operations
  └── Odoo Facebook Mapper       -- Global field mapping configuration
```

---

## Key Buttons / Actions

| Button | Where | What It Does |
|---|---|---|
| **Sync Facebook Page** | Facebook Instance form | Pulls all pages from the Facebook account |
| **Sync Facebook Forms** | Facebook Page form | Pulls all lead forms from a page |
| **Sync Campaigns** | Lead Forms list | Pulls lead forms from all connected pages |
| **Import Fields** | Lead Form form | Fetches form questions and creates field mappings |
| **Fetch Lead** | Lead Form form | Pulls leads from Facebook and creates CRM leads |

---

## Notes

- **Manual Pull Only**: Leads are fetched manually via buttons. There are no webhooks or automatic schedulers active.
- **Phone Deduplication**: Duplicate phone numbers are detected across the entire CRM lead database (normalized by stripping spaces, dashes, parentheses, and leading `+`).
- **Logging**: Every operation (sync, import, fetch) creates an audit log entry in the Facebook Logger.
