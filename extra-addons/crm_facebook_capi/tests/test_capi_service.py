import hashlib
from odoo.tests.common import TransactionCase

class TestCapiService(TransactionCase):

    def setUp(self):
        super().setUp()
        self.CapiService = self.env["facebook.capi.service"]
        self.Lead = self.env["crm.lead"]
        self.Account = self.env["facebook.capi.account"]
        self.StageMapping = self.env["facebook.capi.stage.mapping"]

        # Create test team
        self.team_media = self.env["crm.team"].create({"name": "Yuvmedia-OnBoard Test"})

        # Create dataset account
        self.account_media = self.Account.create({
            "name": "YuvMedia Dataset",
            "team_id": self.team_media.id,
            "pixel_id": "1234567890",
            "access_token": "TEST_TOKEN_123",
            "is_default": True,
        })

        # Create stage and mapping
        self.stage_qualified = self.env["crm.stage"].create({
            "name": "Qualified Test Stage",
            "sequence": 10,
        })

        self.mapping_qualified = self.StageMapping.create({
            "stage_id": self.stage_qualified.id,
            "event_name": "QualifiedLead",
            "value_mode": "expected_revenue",
            "action_source": "business_messaging",
            "active": True,
        })

    def test_pii_hashing_with_fb_lead_id(self):
        """Test deterministic lead_id (cleartext) + SHA256 hashed PII"""
        lead = self.Lead.create({
            "name": "FB Lead Test",
            "email_from": "Test.User@Example.com",
            "phone": "+91 98765-43210",
            "contact_name": "John Doe",
            "fb_lead_id": "1636039311369996",
        })

        user_data = self.CapiService._hash_pii(lead)

        # lead_id must be cleartext int or string
        self.assertEqual(user_data.get("lead_id"), 1636039311369996)

        # Email must be lowercased & SHA256 hashed
        expected_em = hashlib.sha256(b"test.user@example.com").hexdigest()
        self.assertEqual(user_data.get("em"), [expected_em])

        # Phone digits only (919876543210) & SHA256 hashed
        expected_ph = hashlib.sha256(b"919876543210").hexdigest()
        self.assertEqual(user_data.get("ph"), [expected_ph])

        # First and last name hashed
        expected_fn = hashlib.sha256(b"john").hexdigest()
        expected_ln = hashlib.sha256(b"doe").hexdigest()
        self.assertEqual(user_data.get("fn"), [expected_fn])
        self.assertEqual(user_data.get("ln"), [expected_ln])

    def test_pii_hashing_without_fb_lead_id(self):
        """Test non-FB lead fallback without lead_id key"""
        lead = self.Lead.create({
            "name": "Web Lead Test",
            "email_from": "Jane@Example.com",
            "phone": "9876543210",
            "contact_name": "Jane",
        })

        user_data = self.CapiService._hash_pii(lead)
        self.assertNotIn("lead_id", user_data)
        self.assertIn("em", user_data)
        self.assertIn("ph", user_data)

    def test_account_routing_by_team(self):
        """Test dataset account resolution based on team_id"""
        lead_media = self.Lead.create({
            "name": "Team Lead",
            "team_id": self.team_media.id,
        })
        acc = self.CapiService._account_for(lead_media)
        self.assertEqual(acc, self.account_media)

    def test_payload_preparation(self):
        """Test CAPI Graph API payload structure"""
        lead = self.Lead.create({
            "name": "Payload Lead",
            "fb_lead_id": "999888777",
            "expected_revenue": 25000.00,
        })

        payload = self.CapiService._prepare_payload(lead, self.mapping_qualified)
        self.assertIn("data", payload)
        self.assertEqual(len(payload["data"]), 1)

        event = payload["data"][0]
        self.assertEqual(event["event_name"], "QualifiedLead")
        self.assertEqual(event["action_source"], "business_messaging")
        self.assertIn("user_data", event)
        self.assertIn("custom_data", event)
        self.assertEqual(event["custom_data"]["value"], 25000.00)
        self.assertEqual(event["custom_data"]["event_source"], "crm")
