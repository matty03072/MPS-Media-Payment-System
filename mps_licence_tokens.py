"""Public-key verification shared by MPS and its licence issuer.

The private signing key must never be shipped in an MPS installation/image.
Tokens contain no member records, passwords or payment credentials.
"""
import base64
import json
import time
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PREFIX = 'MPS1'
DOMAIN = b'MPS-license-v1\x00'


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def decode(value):
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def issue(private_key, *, licence_id, installation_id, expires=0, live=True, member_limit=0):
    """Issuer-only entry point; callers must establish paid entitlement first."""
    payload = {'v': 1, 'product': 'mps', 'licence_id': str(uuid.UUID(licence_id)),
               'installation_id': str(uuid.UUID(installation_id)), 'member_limit': member_limit,
               'issued': int(time.time()), 'expires': int(expires), 'live': bool(live)}
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return '.'.join((PREFIX, encode(raw), encode(private_key.sign(DOMAIN + raw))))


def verify(token, public_key, installation_id, *, allow_test=False, now=None, allow_expired=False):
    """Return authenticated entitlement or raise ValueError; never trust unsigned data."""
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError('Invalid licence code.')
        prefix, body, signature = token.strip().split('.')
        if prefix != PREFIX:
            raise ValueError('Unknown licence format.')
        raw = decode(body)
        Ed25519PublicKey.from_public_bytes(decode(public_key)).verify(decode(signature), DOMAIN + raw)
        payload = json.loads(raw)
        if payload.get('v') != 1 or payload.get('product') != 'mps':
            raise ValueError('This licence is not for MPS.')
        if payload.get('installation_id') != str(uuid.UUID(installation_id)):
            raise ValueError('This licence belongs to another installation.')
        uuid.UUID(payload['licence_id'])
        if type(payload.get('member_limit')) is not int or payload['member_limit'] not in (-1, 0, 10):
            raise ValueError('Unsupported licence entitlement.')
        if type(payload.get('live')) is not bool or (not payload['live'] and not allow_test):
            raise ValueError('Test licences cannot unlock a production installation.')
        current = time.time() if now is None else now
        if type(payload.get('issued')) is not int or payload['issued'] > current + 300:
            raise ValueError('Invalid licence issue date.')
        expiry = payload.get('expires')
        if type(expiry) is not int or expiry < 0 or (expiry and expiry <= current and not allow_expired):
            raise ValueError('This licence has expired.')
        return payload
    except (InvalidSignature, TypeError, KeyError, UnicodeError, ValueError) as error:
        raise ValueError('Licence code is invalid, expired, or not valid for this installation.') from error
