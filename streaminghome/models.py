import time
import uuid
from sqlalchemy import String, Text, Integer, Float, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): pass

def ident(): return str(uuid.uuid4())

class Setting(Base):
    __tablename__='sh_settings'
    key: Mapped[str]=mapped_column(String(80),primary_key=True)
    value: Mapped[str]=mapped_column(Text,default='')

class Member(Base):
    __tablename__='sh_members'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=ident)
    email: Mapped[str]=mapped_column(String(254),unique=True)
    username: Mapped[str]=mapped_column(String(40))
    username_key: Mapped[str]=mapped_column(String(40),unique=True)
    password_hash: Mapped[str]=mapped_column(String(256))
    admin: Mapped[bool]=mapped_column(Boolean,default=False)
    disabled: Mapped[bool]=mapped_column(Boolean,default=False)
    verified: Mapped[bool]=mapped_column(Boolean,default=False)
    version: Mapped[int]=mapped_column(Integer,default=1)
    mfa_secret: Mapped[str]=mapped_column(Text,default='')
    mfa_pending: Mapped[str]=mapped_column(Text,default='')
    mfa_counter: Mapped[int]=mapped_column(Integer,default=-1)
    mfa_enabled: Mapped[bool]=mapped_column(Boolean,default=False)
    requires_payment: Mapped[bool]=mapped_column(Boolean,default=False)
    paid_through: Mapped[float]=mapped_column(Float,default=0)
    stripe_customer: Mapped[str]=mapped_column(String(120),default='',index=True)
    stripe_subscription: Mapped[str]=mapped_column(String(120),default='',index=True)
    checkout_id: Mapped[str]=mapped_column(String(120),default='')
    checkout_url: Mapped[str]=mapped_column(Text,default='')
    checkout_expires: Mapped[float]=mapped_column(Float,default=0)
    billing_status: Mapped[str]=mapped_column(String(40),default='free')
    cancel_pending: Mapped[bool]=mapped_column(Boolean,default=False)
    billing_cycle: Mapped[int]=mapped_column(Integer,default=0)
    operation_lock: Mapped[str]=mapped_column(String(36),default='')
    operation_started: Mapped[float]=mapped_column(Float,default=0)
    connect_email: Mapped[str]=mapped_column(String(254),default='')
    ombi_username: Mapped[str]=mapped_column(String(120),default='')
    ombi_user_id: Mapped[str]=mapped_column(String(120),default='')
    ombi_token: Mapped[str]=mapped_column(Text,default='')
    created: Mapped[float]=mapped_column(Float,default=time.time)

class RecoveryCode(Base):
    __tablename__='sh_recovery_codes'
    digest: Mapped[str]=mapped_column(String(64),primary_key=True)
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),index=True)

class OneTimeToken(Base):
    __tablename__='sh_tokens'
    digest: Mapped[str]=mapped_column(String(64),primary_key=True)
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'))
    purpose: Mapped[str]=mapped_column(String(20))
    expires: Mapped[float]=mapped_column(Float)

class Server(Base):
    __tablename__='sh_servers'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(80))
    url: Mapped[str]=mapped_column(String(350),unique=True)
    key: Mapped[str]=mapped_column(Text)
    libraries: Mapped[str]=mapped_column(Text,default='[]')

class Invite(Base):
    __tablename__='sh_invites'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=ident)
    digest: Mapped[str]=mapped_column(String(64),unique=True)
    encrypted_token: Mapped[str]=mapped_column(Text)
    label: Mapped[str]=mapped_column(String(120))
    recipient: Mapped[str]=mapped_column(String(254),default='')
    expires: Mapped[float]=mapped_column(Float)
    max_uses: Mapped[int]=mapped_column(Integer,default=1)
    uses: Mapped[int]=mapped_column(Integer,default=0)
    revoked: Mapped[bool]=mapped_column(Boolean,default=False)
    requires_payment: Mapped[bool]=mapped_column(Boolean,default=False)
    email_status: Mapped[str]=mapped_column(String(40),default='not_sent')
    created: Mapped[float]=mapped_column(Float,default=time.time)

class InviteServer(Base):
    __tablename__='sh_invite_servers'
    invite_id: Mapped[str]=mapped_column(ForeignKey('sh_invites.id'),primary_key=True)
    server_id: Mapped[int]=mapped_column(ForeignKey('sh_servers.id'),primary_key=True)
    folders: Mapped[str]=mapped_column(Text)

class Account(Base):
    __tablename__='sh_accounts'
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),primary_key=True)
    server_id: Mapped[int]=mapped_column(ForeignKey('sh_servers.id'),primary_key=True)
    invite_id: Mapped[str]=mapped_column(ForeignKey('sh_invites.id'))
    folders: Mapped[str]=mapped_column(Text)
    remote_id: Mapped[str]=mapped_column(String(120),default='')
    remote_name: Mapped[str]=mapped_column(String(40),default='')
    state: Mapped[str]=mapped_column(String(40),default='not_created')
    link_pending: Mapped[bool]=mapped_column(Boolean,default=False)
    access_enabled: Mapped[bool]=mapped_column(Boolean,default=False)
    sync_error: Mapped[str]=mapped_column(Text,default='')

class Plan(Base):
    __tablename__='sh_plans'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=ident)
    name: Mapped[str]=mapped_column(String(80))
    amount: Mapped[int]=mapped_column(Integer)
    currency: Mapped[str]=mapped_column(String(3),default='gbp')
    stripe_price: Mapped[str]=mapped_column(String(120),default='')
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    created: Mapped[float]=mapped_column(Float,default=time.time)

class Event(Base):
    __tablename__='sh_stripe_events'
    id: Mapped[str]=mapped_column(String(120),primary_key=True)
    event_type: Mapped[str]=mapped_column(String(80))
    object_id: Mapped[str]=mapped_column(String(120))
    live: Mapped[bool]=mapped_column(Boolean)
    state: Mapped[str]=mapped_column(String(20),default='pending')
    claimed: Mapped[float]=mapped_column(Float,default=0)
    error: Mapped[str]=mapped_column(String(250),default='')
    created: Mapped[float]=mapped_column(Float,default=time.time)

class Audit(Base):
    __tablename__='sh_audit'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    message: Mapped[str]=mapped_column(String(400))
    created: Mapped[float]=mapped_column(Float,default=time.time)

class Limit(Base):
    __tablename__='sh_limits'
    key: Mapped[str]=mapped_column(String(64),primary_key=True)
    count: Mapped[int]=mapped_column(Integer,default=0)
    expires: Mapped[float]=mapped_column(Float)

class ImportedProfile(Base):
    __tablename__='sh_imported_profiles'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    name: Mapped[str]=mapped_column(String(120))
    connect_name: Mapped[str]=mapped_column(String(254),default='')
    match_basis: Mapped[str]=mapped_column(String(40))
    member_id: Mapped[str|None]=mapped_column(ForeignKey('sh_members.id'),nullable=True)
    created: Mapped[float]=mapped_column(Float,default=time.time)

class ImportedIdentity(Base):
    __tablename__='sh_imported_identities'
    server_id: Mapped[int]=mapped_column(ForeignKey('sh_servers.id'),primary_key=True)
    remote_id: Mapped[str]=mapped_column(String(120),primary_key=True)
    profile_id: Mapped[str]=mapped_column(ForeignKey('sh_imported_profiles.id'),index=True)
    name: Mapped[str]=mapped_column(String(120))
    connect_name: Mapped[str]=mapped_column(String(254),default='')
    connect_id: Mapped[str]=mapped_column(String(120),default='')
    link_type: Mapped[str]=mapped_column(String(40),default='')
    remote_admin: Mapped[bool]=mapped_column(Boolean,default=False)
    remote_disabled: Mapped[bool]=mapped_column(Boolean,default=False)
    all_folders: Mapped[bool]=mapped_column(Boolean,default=False)
    folders: Mapped[str]=mapped_column(Text,default='[]')
    present: Mapped[bool]=mapped_column(Boolean,default=True)
    issue: Mapped[str]=mapped_column(String(250),default='')
    last_seen: Mapped[float]=mapped_column(Float,default=time.time)

class ImportControl(Base):
    __tablename__='sh_import_control'
    id: Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=False)
    lock: Mapped[str]=mapped_column(String(36),default='')

class InviteAttribution(Base):
    __tablename__='sh_invite_attribution'
    invite_id: Mapped[str]=mapped_column(ForeignKey('sh_invites.id'),primary_key=True)
    admin_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'))
    created_by: Mapped[str]=mapped_column(ForeignKey('sh_members.id'))
    created: Mapped[float]=mapped_column(Float,default=time.time)

class InviteDispatch(Base):
    __tablename__='sh_invite_dispatches'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=ident)
    invite_id: Mapped[str]=mapped_column(ForeignKey('sh_invites.id'),index=True)
    actor_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'))
    recipient: Mapped[str]=mapped_column(String(254),default='')
    action: Mapped[str]=mapped_column(String(20))
    status: Mapped[str]=mapped_column(String(30))
    created: Mapped[float]=mapped_column(Float,default=time.time)

class MemberReferral(Base):
    __tablename__='sh_member_referrals'
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),primary_key=True)
    invite_id: Mapped[str]=mapped_column(ForeignKey('sh_invites.id'))
    admin_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'))
    created: Mapped[float]=mapped_column(Float,default=time.time)

class PaymentReceipt(Base):
    __tablename__='sh_payment_receipts'
    id: Mapped[str]=mapped_column(String(180),primary_key=True)
    provider: Mapped[str]=mapped_column(String(15))
    live: Mapped[bool]=mapped_column(Boolean)
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),index=True)
    admin_id: Mapped[str|None]=mapped_column(ForeignKey('sh_members.id'),nullable=True)
    amount: Mapped[int]=mapped_column(Integer)
    currency: Mapped[str]=mapped_column(String(3))
    kind: Mapped[str]=mapped_column(String(20))
    occurred: Mapped[float]=mapped_column(Float,index=True)
    recorded: Mapped[float]=mapped_column(Float,default=time.time)

class PayPalPlan(Base):
    __tablename__='sh_paypal_plans'
    plan_id: Mapped[str]=mapped_column(ForeignKey('sh_plans.id'),primary_key=True)
    live: Mapped[bool]=mapped_column(Boolean,primary_key=True)
    remote_id: Mapped[str]=mapped_column(String(120))

class PayPalSubscription(Base):
    __tablename__='sh_paypal_subscriptions'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=ident)
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),index=True)
    plan_id: Mapped[str]=mapped_column(ForeignKey('sh_plans.id'))
    live: Mapped[bool]=mapped_column(Boolean)
    remote_id: Mapped[str]=mapped_column(String(120),default='',index=True)
    status: Mapped[str]=mapped_column(String(40),default='CREATING')
    approval_url: Mapped[str]=mapped_column(Text,default='')
    created: Mapped[float]=mapped_column(Float,default=time.time)

class PayPalEvent(Base):
    __tablename__='sh_paypal_events'
    id: Mapped[str]=mapped_column(String(160),primary_key=True)
    event_type: Mapped[str]=mapped_column(String(80))
    payload: Mapped[str]=mapped_column(Text)
    live: Mapped[bool]=mapped_column(Boolean)
    state: Mapped[str]=mapped_column(String(20),default='pending')
    claimed: Mapped[float]=mapped_column(Float,default=0)
    error: Mapped[str]=mapped_column(String(250),default='')
    created: Mapped[float]=mapped_column(Float,default=time.time)

class InvitePlan(Base):
    __tablename__='sh_invite_plans'
    invite_id: Mapped[str]=mapped_column(ForeignKey('sh_invites.id'),primary_key=True)
    plan_id: Mapped[str]=mapped_column(ForeignKey('sh_plans.id'))

class MemberPlan(Base):
    __tablename__='sh_member_plans'
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),primary_key=True)
    plan_id: Mapped[str]=mapped_column(ForeignKey('sh_plans.id'))

class PlanPublishJob(Base):
    __tablename__='sh_plan_publish_jobs'
    id: Mapped[str]=mapped_column(String(60),primary_key=True)
    plan_id: Mapped[str]=mapped_column(ForeignKey('sh_plans.id'))
    provider: Mapped[str]=mapped_column(String(15))
    state: Mapped[str]=mapped_column(String(20),default='pending')
    error: Mapped[str]=mapped_column(String(250),default='')
    claimed: Mapped[float]=mapped_column(Float,default=0)
    created: Mapped[float]=mapped_column(Float,default=time.time)

class OnboardingJob(Base):
    __tablename__='sh_onboarding_jobs'
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),primary_key=True)
    credential: Mapped[str]=mapped_column(Text,default='')
    expires: Mapped[float]=mapped_column(Float,default=0)
    state: Mapped[str]=mapped_column(String(30),default='pending')
    connect_state: Mapped[str]=mapped_column(String(30),default='pending')
    ombi_state: Mapped[str]=mapped_column(String(30),default='pending')
    error: Mapped[str]=mapped_column(String(500),default='')
    next_attempt: Mapped[float]=mapped_column(Float,default=0)
    attempts: Mapped[int]=mapped_column(Integer,default=0)


class InvitePermission(Base):
    __tablename__='sh_invite_permissions'
    invite_id: Mapped[str]=mapped_column(ForeignKey('sh_invites.id'),primary_key=True)
    server_id: Mapped[int]=mapped_column(ForeignKey('sh_servers.id'),primary_key=True)
    live_tv: Mapped[bool]=mapped_column(Boolean,default=False)

class AccountPermission(Base):
    __tablename__='sh_account_permissions'
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),primary_key=True)
    server_id: Mapped[int]=mapped_column(ForeignKey('sh_servers.id'),primary_key=True)
    enabled: Mapped[bool]=mapped_column(Boolean,default=True)
    live_tv: Mapped[bool]=mapped_column(Boolean,default=False)
    pending: Mapped[bool]=mapped_column(Boolean,default=True)

class DeletionJob(Base):
    __tablename__='sh_deletion_jobs'
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),primary_key=True)
    state: Mapped[str]=mapped_column(String(30),default='pending')
    email: Mapped[str]=mapped_column(Text,default='')
    targets: Mapped[str]=mapped_column(Text,default='')
    ombi_id: Mapped[str]=mapped_column(String(120),default='')
    ombi_name: Mapped[str]=mapped_column(String(120),default='')
    stripe_done: Mapped[bool]=mapped_column(Boolean,default=False)
    paypal_done: Mapped[bool]=mapped_column(Boolean,default=False)
    emby_done: Mapped[bool]=mapped_column(Boolean,default=False)
    ombi_done: Mapped[bool]=mapped_column(Boolean,default=False)
    erased: Mapped[bool]=mapped_column(Boolean,default=False)
    email_sent: Mapped[bool]=mapped_column(Boolean,default=False)
    error: Mapped[str]=mapped_column(String(500),default='')
    attempts: Mapped[int]=mapped_column(Integer,default=0)
    next_attempt: Mapped[float]=mapped_column(Float,default=0)
    created: Mapped[float]=mapped_column(Float,default=time.time)

class InviteDeletion(Base):
    __tablename__='sh_invite_deletions'
    invite_id: Mapped[str]=mapped_column(ForeignKey('sh_invites.id'),primary_key=True)
    created: Mapped[float]=mapped_column(Float,default=time.time)

class PasswordJob(Base):
    __tablename__='sh_password_jobs'
    member_id: Mapped[str]=mapped_column(ForeignKey('sh_members.id'),primary_key=True)
    credential: Mapped[str]=mapped_column(Text,default='')
    expires: Mapped[float]=mapped_column(Float,default=0)
    targets: Mapped[str]=mapped_column(Text,default='[]')
    ombi_id: Mapped[str]=mapped_column(String(120),default='')
    ombi_done: Mapped[bool]=mapped_column(Boolean,default=False)
    state: Mapped[str]=mapped_column(String(30),default='pending')
    error: Mapped[str]=mapped_column(String(500),default='')
    next_attempt: Mapped[float]=mapped_column(Float,default=0)
    attempts: Mapped[int]=mapped_column(Integer,default=0)
