# Facebook Lead Integration - Setup & Troubleshooting Guide

## Error: (#10) User has insufficient privileges on the page

This error means the **Page Access Token** being used does not have the required
permissions to access Facebook Lead Gen APIs, or the user is not an Admin of the page.

---

## Prerequisites

- A [Facebook Developer Account](https://developers.facebook.com/)
- A Facebook App (type: **Business**)
- A Facebook Page where you have **Admin** access
- Required App Permissions / Scopes:
  - `leads_retrieval` — read leads from lead forms
  - `pages_show_list` — list pages the user manages
  - `pages_read_engagement` — read page engagement data
  - `pages_manage_metadata` — manage page metadata (needed for Page Access Token)

---

## Step-by-Step Fix

### Step 1: Add `leads_retrieval` Permission to Your App

1. Go to [Facebook Developer Portal](https://developers.facebook.com/)
2. Select your App from the dashboard
3. In the left sidebar, click **Permissions and Features**
4. Search for **leads_retrieval** and click **Request** or **Add**
5. If the permission requires App Review, submit your app for review and wait
   for approval before proceeding

> **Note:** Without `leads_retrieval` approved, lead form and lead fetch
> endpoints will always return Error #10.

### Step 2: Verify Page Admin Access

1. Open Facebook → your target Page
2. Go to **Settings → Page Access** (or **Page Roles**)
3. Confirm your user has the **Admin** role
4. If not, ask an existing Admin to grant you Admin access

### Step 3: Regenerate the Page Access Token in Odoo

1. Open Odoo → go to **CRM → Facebook Accounts**
2. Open your Facebook Account record
3. Paste a valid **User Access Token** in the `User Access Token` field
   - To get one: use the [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
     with your App, select all required permissions listed above, and generate a token
4. Click the **Fetch Pages** button
5. This will automatically find your page and store the **Page Access Token**

### Step 4: Test the Connection

1. On the same Facebook Account record, click **Test Connection**
2. You should see a success notification confirming the connection

### Step 5: Sync Lead Forms

1. Click **Sync Lead Forms** on the Facebook Account record
2. Your lead forms should now appear in the list view
3. Open any lead form and click **Fetch Leads** to pull leads into Odoo CRM

---

## Common Facebook API Errors

| Error Code | Meaning | Fix |
|------------|---------|-----|
| **10** | Insufficient privileges on the page | Ensure `leads_retrieval` permission is approved and you are a Page Admin. Regenerate the Page Access Token via **Fetch Pages**. |
| **100** | Invalid parameter (e.g., wrong endpoint or Page ID) | Verify your Page ID is correct (not a User ID). Ensure you are using a **Page Access Token**, not a User Access Token. |
| **190** | Access token has expired or is invalid | Generate a new token via Graph API Explorer or click **Fetch Pages** again with a fresh User Access Token. |
| **102** | API session expired | Token has expired. Re-authenticate and generate a new token. |
| **4** | Application-level rate limit reached | Wait before retrying. Reduce frequency of API calls. |
| **17** | Account-level rate limit reached | Wait before retrying. This usually resolves within a few minutes. |

---

## Token Types Explained

| Token Type | Where in Odoo | Purpose |
|------------|---------------|---------|
| **User Access Token** | `User Access Token` field | Temporary token used only to call `Fetch Pages` — which exchanges it for a Page Access Token. Obtained via Graph API Explorer. |
| **Page Access Token** | `Page Access Token` field (auto-filled by **Fetch Pages**) | Long-lived token used for all API calls (lead forms, leads). Stored in system parameters (`facebook_integration.access_token`). |

### How Tokens Flow

```
User Access Token  →  [Fetch Pages]  →  Page Access Token  →  [System Parameters]
                                         ↓
                                  facebook_integration.access_token
                                  facebook_integration.page_id
```

The **Fetch Pages** button calls `GET /me/accounts` with the User Access Token,
finds the configured Page, and stores its Page Access Token in both the
account record and the Odoo system parameters.

---

## Checklist Before Syncing

- [ ] Facebook App created with correct type (Business)
- [ ] `leads_retrieval` permission approved on the App
- [ ] `pages_show_list` and `pages_read_engagement` permissions added
- [ ] Your user is an **Admin** of the target Facebook Page
- [ ] Fresh **User Access Token** pasted in the Odoo Facebook Account record
- [ ] **Fetch Pages** clicked successfully
- [ ] **Test Connection** shows success
- [ ] **Sync Lead Forms** completes without errors
