import unittest
import time
import uuid
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select, func
from werkzeug.exceptions import Forbidden
import test_mps as base
from mps.core import db, put, setting
from mps.models import Member
from mps.license_tokens import issue, encode
from mps.licensing import prepare, reserve_member, allowed, blocked, refresh


class ClientTests(unittest.TestCase):
    setUp=base.Tests.setUp
    tearDown=base.Tests.tearDown
    as_user=base.Tests.as_user
    post=base.Tests.post
    def setup_issuer(self):
        self.key=Ed25519PrivateKey.generate()
        self.public=encode(self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))
        prepare(); self.installation=setting('mps_installation_id')
        self.authority=patch('mps.licensing.issuer',return_value={'url':'https://licences.example.com','public_key':self.public})
        self.authority.start();self.addCleanup(self.authority.stop)
    def signed(self, limit, expires=None):
        return issue(self.key,licence_id=str(uuid.uuid4()),installation_id=self.installation,
                     member_limit=limit,expires=expires or int(time.time())+3600)
    def populate(self, count):
        offset = db().scalar(select(func.count()).select_from(Member))
        for index in range(offset, offset+count):
            db().add(Member(email=f'license{index}@example.com',username=f'license{index}',username_key=f'license{index}',password_hash='unused',created=time.time()+index))
        db().commit()
    def test_tenth_allowed_eleventh_blocked_without_deletion(self):
        with self.app.app_context():
            self.setup_issuer(); self.populate(8) # base fixture contains one non-admin
            reserve_member()
            self.populate(1)
            with self.assertRaises(Forbidden):reserve_member()
            db().rollback()
            self.assertEqual(db().scalar(select(func.count()).select_from(Member).where(Member.admin==False)),10)
    def test_subscription_fallback_keeps_all_accounts(self):
        with self.app.app_context():
            self.setup_issuer();self.populate(14)
            members=db().scalars(select(Member).where(Member.admin==False).order_by(Member.created,Member.id)).all()
            put('mps_licence_lease',self.signed(0));db().commit()
            self.assertTrue(all(allowed(member) for member in members))
            put('mps_licence_lease',self.signed(10));db().commit()
            self.assertEqual(sum(allowed(member) for member in members),10)
            self.assertEqual(db().scalar(select(func.count()).select_from(Member).where(Member.admin==False)),15)
    def test_removed_registration_stays_disabled_after_expiry(self):
        with self.app.app_context():
            self.setup_issuer();put('mps_licence_lease',self.signed(-1,int(time.time())-1));db().commit()
            self.assertTrue(blocked())
        self.as_user(self.admin)
        self.assertEqual(self.client.get('/admin').status_code,403)
        self.assertEqual(self.client.get('/admin/licence').status_code,200)
        self.assertEqual(self.client.get('/health').status_code,200)
    def test_fake_activation_cannot_clear_revocation(self):
        class Denied:
            status_code=403
        with self.app.app_context():
            self.setup_issuer();put('mps_licence_lease',self.signed(-1));db().commit()
            self.app.config['LICENCE_HTTP_POST']=lambda *args,**kwargs:Denied()
            refresh(force=True,activation_code='bad-code')
            self.assertTrue(blocked())
    def test_expired_paid_lease_reverts_to_free(self):
        with self.app.app_context():
            self.setup_issuer();self.populate(10)
            put('mps_licence_lease',self.signed(0,int(time.time())-1));db().commit()
            members=db().scalars(select(Member).where(Member.admin==False)).all()
            self.assertEqual(sum(allowed(member) for member in members),10)

    def test_concurrent_admission_cannot_exceed_ten(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with self.app.app_context():
            self.setup_issuer();self.populate(8)
        barrier=Barrier(2)
        def attempt(index):
            with self.app.app_context():
                barrier.wait()
                try:
                    reserve_member()
                    db().add(Member(email=f'race{index}@example.com',username=f'race{index}',username_key=f'race{index}',password_hash='unused'))
                    db().commit();return True
                except Forbidden:
                    db().rollback();return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(results),1)
        with self.app.app_context():
            self.assertEqual(db().scalar(select(func.count()).select_from(Member).where(Member.admin==False)),10)
