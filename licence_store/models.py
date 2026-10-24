import time
import uuid
from sqlalchemy import String, Text, Integer, Float, Boolean, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = 'mps_licence_orders'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[str | None] = mapped_column(ForeignKey('mps_licence_customers.id'), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(254))
    installation_id: Mapped[str] = mapped_column(String(36))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    price_id: Mapped[str] = mapped_column(String(120))
    live: Mapped[bool] = mapped_column(Boolean)
    checkout_id: Mapped[str] = mapped_column(String(120), default='', index=True)
    checkout_cycle: Mapped[int] = mapped_column(Integer, default=0)
    subscription_id: Mapped[str] = mapped_column(String(120), default='', index=True)
    activation_digest: Mapped[str] = mapped_column(String(64), default='')
    paid_through: Mapped[float] = mapped_column(Float, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(20), default='pending')
    encrypted_code: Mapped[str] = mapped_column(Text, default='')
    email_state: Mapped[str] = mapped_column(String(20), default='waiting')
    email_attempts: Mapped[int] = mapped_column(Integer, default=0)
    email_next: Mapped[float] = mapped_column(Float, default=0)
    email_claim: Mapped[str] = mapped_column(String(36), default='')
    email_claimed: Mapped[float] = mapped_column(Float, default=0)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    paid_at: Mapped[float] = mapped_column(Float, default=0)


class PaymentEvent(Base):
    __tablename__ = 'mps_licence_events'
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80))
    live: Mapped[bool] = mapped_column(Boolean)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str] = mapped_column(String(120), default='')


class Customer(Base):
    __tablename__ = 'mps_licence_customers'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    mfa_secret: Mapped[str] = mapped_column(Text, default='')
    mfa_pending: Mapped[str] = mapped_column(Text, default='')
    mfa_counter: Mapped[int] = mapped_column(Integer, default=-1)
    created: Mapped[float] = mapped_column(Float, default=time.time)


class CustomerToken(Base):
    __tablename__ = 'mps_licence_customer_tokens'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('mps_licence_customers.id'), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    expires: Mapped[float] = mapped_column(Float)


class RecoveryCode(Base):
    __tablename__ = 'mps_licence_recovery_codes'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('mps_licence_customers.id'), index=True)


class Activation(Base):
    __tablename__ = 'mps_licence_activations'
    application_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey('mps_licence_orders.id'), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_ip: Mapped[str] = mapped_column(String(45))
    last_ip: Mapped[str] = mapped_column(String(45))
    registered: Mapped[float] = mapped_column(Float, default=time.time)
    last_seen: Mapped[float] = mapped_column(Float, default=time.time)
    removed: Mapped[float] = mapped_column(Float, default=0)


class ActivationLog(Base):
    __tablename__ = 'mps_licence_activation_log'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id: Mapped[str] = mapped_column(String(36), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey('mps_licence_orders.id'), index=True)
    action: Mapped[str] = mapped_column(String(30))
    ip: Mapped[str] = mapped_column(String(45))
    created: Mapped[float] = mapped_column(Float, default=time.time)
