"""Fail closed before publishing a client without its real licence authority."""
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

root=Path(__file__).resolve().parents[1]
configuration=json.loads((root/'mps'/'licensing_public.json').read_text())
url=urlsplit(configuration['url'])
if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
    raise SystemExit('A production HTTPS licence service URL is required.')
if url.path not in ('','/'):
    raise SystemExit('The issuer URL must be the origin, without a route path.')
key=configuration['public_key']
if len(base64.b64decode(key+'='*(-len(key)%4),altchars=b'-_',validate=True))!=32:
    raise SystemExit('A valid Ed25519 public key is required.')
if (root/'LICENSE').read_bytes()!=(root/'licence_store'/'TERMS.txt').read_bytes():
    raise SystemExit('Client and purchase-site terms do not match.')
print('Public licence configuration and terms are consistent.')
