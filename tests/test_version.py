import tempfile
import unittest
from mps import create_app
from mps.version import VERSION
from mps.models import Base

class VersionTests(unittest.TestCase):
    def test_health_reports_release_version(self):
        with tempfile.TemporaryDirectory() as state:
            app = create_app({'TESTING': True, 'DATA_DIR': state, 'DATABASE_URL': 'sqlite:///' + state + '/test.db'})
            Base.metadata.create_all(app.extensions['engine'])
            try:
                result = app.test_client().get('/health')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json['version'], VERSION)
                self.assertEqual(result.json['installer_version'], VERSION)
                self.assertEqual(VERSION, '1.0.0')
            finally:
                app.extensions['engine'].dispose()
