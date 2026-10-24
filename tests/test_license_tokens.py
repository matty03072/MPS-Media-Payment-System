import json
import time
import unittest
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from mps.license_tokens import issue, verify, encode, decode


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        self.installation = str(uuid.uuid4())

    def token(self, **options):
        return issue(self.key, licence_id=str(uuid.uuid4()), installation_id=self.installation, **options)

    def test_valid_bound_unlimited_licence(self):
        self.assertEqual(verify(self.token(), self.public, self.installation)['member_limit'], 0)

    def test_foreign_installation_rejected(self):
        with self.assertRaises(ValueError):
            verify(self.token(), self.public, str(uuid.uuid4()))

    def test_changed_payload_rejected(self):
        prefix, body, signature = self.token().split('.')
        payload = json.loads(decode(body)); payload['expires'] = 9999999999
        tampered = '.'.join((prefix, encode(json.dumps(payload).encode()), signature))
        with self.assertRaises(ValueError):
            verify(tampered, self.public, self.installation)

    def test_expired_and_test_keys_rejected(self):
        for token in (self.token(expires=int(time.time()) - 1), self.token(live=False)):
            with self.assertRaises(ValueError):
                verify(token, self.public, self.installation)
        self.assertFalse(verify(self.token(live=False), self.public, self.installation, allow_test=True)['live'])

    def test_wrong_issuer_rejected(self):
        other = Ed25519PrivateKey.generate()
        public = encode(other.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        with self.assertRaises(ValueError):
            verify(self.token(), public, self.installation)

    def test_malformed_codes_rejected(self):
        for token in ('', 'MPS1.bad.bad', 'x' * 5000, 'MPS2.a.b'):
            with self.assertRaises(ValueError):
                verify(token, self.public, self.installation)
