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
            self.assertIn('Plain text',message.get_body(('plain',)).get_content())
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

    def owner(self):
        from mps.core import db
        from mps.models import Member
        from sqlalchemy import select
        InstallerTests.form(self)
        self.client.post('/setup', data=InstallerTests.form(self))
        with self.app.app_context():
            member=db().scalar(select(Member)); member.mfa_enabled=True; db().commit()
            uid, version=member.id, member.version
        with self.client.session_transaction() as session:
            session.update(uid=uid, version=version, mfa_ok=True, csrf="branding-test")
            return session['csrf']

    def test_custom_brand_persists_and_worker_mail(self):
        import io
        from PIL import Image
        from mps import create_app
        csrf=self.owner()
        logo=io.BytesIO();Image.new('RGB',(30,15),'blue').save(logo,'PNG');raw=logo.getvalue();logo.seek(0)
        response=self.client.post('/admin/settings/branding',data={'csrf':csrf,'section':'branding','site_name':'My <Cinema>','logo':(logo,'logo.png')})
        self.assertEqual(response.status_code,302)
        page=self.client.get('/admin/settings/branding')
        self.assertIn(b'My &lt;Cinema&gt;',page.data)
        self.assertIn(b'Powered by',page.data)
        self.assertIn(b'https://github.com/matty03072/MPS-Media-Payment-System',page.data)
        self.assertEqual(self.client.get('/branding/logo').mimetype,'image/png')
        restarted=create_app(dict(self.config,TESTING=True))
        with restarted.app_context():
            message=build_message('member@example.invalid','owner@example.invalid','Your MPS account','Open http://localhost:8099/reset/MPS-token',intro='Your MPS account')
            self.assertIn('My <Cinema>',message['From'])
            self.assertIn('My <Cinema>',message['Subject'])
            self.assertIn('My &lt;Cinema&gt;',message.get_body(('html',)).get_content())
            self.assertIn('/reset/MPS-token',message.get_body(('plain',)).get_content())
            self.assertTrue(any(part['Content-ID']=='<mps-logo>' for part in message.walk()))
        restarted.extensions['engine'].dispose()
        response=self.client.post('/admin/settings/branding',data={'csrf':csrf,'section':'branding','site_name':'New Name','remove_logo':'yes'})
        self.assertEqual(response.status_code,302)
        self.assertIn(b'/static/mps-logo.svg',self.client.get('/login').data)

    def test_invalid_upload_name_and_permissions(self):
        import io
        from mps.core import db
        from mps.models import Member
        from sqlalchemy import select
        self.assertEqual(self.client.get('/admin/settings/branding').status_code,302)
        csrf=self.owner()
        for name, content in [('Valid',b'<svg onload="alert(1)"/>'),('Bad\nName',b'bad')]:
            response=self.client.post('/admin/settings/branding',data={'csrf':csrf,'section':'branding','site_name':name,'logo':(io.BytesIO(content),'logo.png')})
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.post('/admin/settings/branding',data={'section':'branding','site_name':'No CSRF'}).status_code,400)
        with self.app.app_context():
            member=db().scalar(select(Member));member.admin=False;db().commit()
        self.assertEqual(self.client.post('/admin/settings/branding',data={'csrf':csrf,'section':'branding','site_name':'Forbidden'}).status_code,403)
