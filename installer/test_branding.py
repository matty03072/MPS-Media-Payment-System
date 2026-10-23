import unittest
from installer.test_installation import InstallerTests
from mps.mail import build_message

class BrandingTests(unittest.TestCase):
    def setUp(self):
        InstallerTests.setUp(self)
    def tearDown(self):
        InstallerTests.tearDown(self)
    def test_login_identity_and_assets(self):
        page=self.client.get('/login')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'MPS',page.data)
        self.assertIn(b'Media Payment System',page.data)
        for asset in ['mps-logo.svg','mps-icon.png','favicon.svg','mps.css']:
            with self.client.get('/static/'+asset) as response:
                self.assertEqual(response.status_code,200)
    def test_worker_email_identity_mime_and_guards(self):
        with self.app.app_context():
            message=build_message('member@example.invalid','owner@example.invalid','MPS account','Plain text',intro='<test>',action_url='http://localhost:8099/reset/fixture')
            self.assertIn('MPS',message['From'])
            self.assertEqual(message.get_body(('plain',)).get_content().strip(),'Plain text')
            html=message.get_body(('html',)).get_content()
            self.assertIn('cid:mps-logo',html)
            self.assertIn('&lt;test&gt;',html)
            self.assertIn('http://localhost:8099/reset/fixture',html)
            self.assertTrue(any(part['Content-ID']=='<mps-logo>' and part.get_content_type()=='image/png' for part in message.walk()))
            from mps.emby import APIError
            with self.assertRaises(APIError):
                build_message('a@example.invalid','b@example.invalid','Hello\nInjected','x')
            with self.assertRaises(APIError):
                build_message('a@example.invalid','b@example.invalid','Hello','x',action_url='https://example.invalid/reset')
