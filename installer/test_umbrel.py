"""Protect bootstrap even when reached outside app_proxy."""
import base64
import os
import unittest
from unittest.mock import patch
from installer.test_installation import InstallerTests
from mps.core import db, put


class UmbrelTests(unittest.TestCase):
    def setUp(self):
        InstallerTests.setUp(self)
        self.bootstrap = 'fixture-bootstrap-password-not-for-production'
        self.env = patch.dict(os.environ, UMBREL_BOOTSTRAP_PASSWORD=self.bootstrap)
        self.env.start()
        self.addCleanup(self.env.stop)

    def tearDown(self):
        InstallerTests.tearDown(self)

    def credentials(self, password=None):
        value = 'owner:' + (password or self.bootstrap)
        return {'Authorization': 'Basic ' + base64.b64encode(value.encode()).decode()}

    def test_umbrel_owner_setup(self):
        self.assertEqual(self.client.get('/setup').status_code, 401)
        self.assertEqual(self.client.get('/setup', headers=self.credentials('wrong')).status_code, 401)
        page = self.client.get('/setup', headers=self.credentials())
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'name="token"', page.data)
        self.assertNotIn(self.bootstrap.encode(), page.data)
        with self.app.app_context():
            put('setup_expires', '1'); db().commit()
        with self.client.session_transaction() as session:
            csrf = session['csrf']
        form = dict(csrf=csrf, site_name='Fixture', email='owner@example.invalid', username='owner', password='Fixture-password-2026')
        self.assertEqual(self.client.post('/setup', data=form).status_code, 401)
        self.assertEqual(self.client.post('/setup', data=dict(form, csrf='wrong'), headers=self.credentials()).status_code, 400)
        self.assertEqual(self.client.post('/setup', data=form, headers=self.credentials()).status_code, 302)
        self.assertEqual(self.client.get('/setup').location, '/login')
        with self.client.session_transaction() as session:
            form['csrf'] = session['csrf']
        self.assertEqual(self.client.post('/setup', data=form, headers=self.credentials()).location, '/login')
        self.client.get('/login')
        with self.client.session_transaction() as session:
            csrf = session['csrf']
        self.assertEqual(self.client.post('/login', data=dict(csrf=csrf, email=form['email'], password=self.bootstrap)).status_code, 200)
        self.assertEqual(self.client.post('/login', data=dict(csrf=csrf, email=form['email'], password=form['password'])).location, '/security')
