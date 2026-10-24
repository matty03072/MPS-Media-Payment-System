"""Idempotent fulfilment and retryable delivery, independent of member billing."""
import time
import uuid
import secrets
import hashlib
from sqlalchemy import select, update, or_, func
from .models import Order, PaymentEvent, Activation, ActivationLog
from mps_licence_tokens import issue


class PaymentMismatch(ValueError):
    pass


def fulfil(session, remote, checkout_id, live, private_key, encrypt):
    checkout = remote.get('/checkout/sessions/' + checkout_id)
    order_id = checkout.get('client_reference_id', '')
    order = session.get(Order, order_id)
    if order is None:
        return None  # Other products in the same Stripe account are not licence orders.
    if (order.checkout_id != checkout_id or order.live != live
            or checkout.get('livemode') is not live
            or checkout.get('mode') != 'subscription'
            or checkout.get('metadata', {}).get('product') != 'mps-unlock'
            or checkout.get('metadata', {}).get('order_id') != order.id):
        raise PaymentMismatch('Payment does not match the licence order.')
    if checkout.get('status') != 'complete' or checkout.get('payment_status') != 'paid':
        return None
    if checkout.get('amount_total') != order.amount or checkout.get('currency') != order.currency:
        raise PaymentMismatch('Payment amount or currency does not match the order.')
    items = remote.get('/checkout/sessions/' + checkout_id + '/line_items?limit=100')
    lines = items.get('data', [])
    if (items.get('has_more') or len(lines) != 1 or lines[0].get('quantity') != 1
            or lines[0].get('price', {}).get('id') != order.price_id):
        raise PaymentMismatch('Payment price does not match the order.')
    # Lock this order until the entitlement transaction commits. A concurrent
    # duplicate waits, then sees the existing paid state and reuses its code.
    session.execute(update(Order).where(Order.id == order.id).values(state=Order.state))
    session.refresh(order)
    if order.state != 'pending':
        session.commit()
        return order.id if order.state == 'paid' else None
    subscription_id = checkout.get('subscription', '')
    if not isinstance(subscription_id, str) or not subscription_id.startswith('sub_'):
        raise PaymentMismatch('Missing subscription.')
    order.subscription_id = subscription_id
    reconcile(session, remote, order)
    if order.state != 'paid':
        raise PaymentMismatch('Subscription payment has not been confirmed.')
    code = 'MPSK1.' + order.id + '.' + secrets.token_urlsafe(32)
    order.activation_digest = hashlib.sha256(code.encode()).hexdigest()
    order.encrypted_code = encrypt(code)
    order.state = 'paid'
    order.paid_at = time.time()
    order.email_state = 'pending'
    session.commit()
    return order.id


def process_events(session, remote, private_key, encrypt):
    ids = list(session.scalars(select(PaymentEvent.id).where(
        PaymentEvent.processed == False, PaymentEvent.next_attempt <= time.time()).limit(50)))
    for event_id in ids:
        event = session.get(PaymentEvent, event_id)
        try:
            if event.event_type.startswith('checkout.session.'):
                fulfil(session, remote, event.object_id, event.live, private_key, encrypt)
            else:
                if event.event_type.startswith('customer.subscription.'):
                    sid = event.object_id
                else:
                    invoice = remote.get('/invoices/' + event.object_id)
                    sid = invoice.get('subscription', '') or invoice.get('parent', {}).get('subscription_details', {}).get('subscription', '')
                order = session.scalar(select(Order).where(Order.subscription_id == sid, Order.subscription_id != '', Order.live == event.live))
                if order:
                    reconcile(session, remote, order)
                    session.commit()
            event = session.get(PaymentEvent, event_id)
            event.processed = True
            event.error = ''
            session.commit()
        except Exception:
            session.rollback()
            event = session.get(PaymentEvent, event_id)
            event.attempts += 1
            event.next_attempt = time.time() + min(3600, 30 * 2 ** min(event.attempts, 7))
            event.error = 'Payment verification failed; check the order and Stripe.'
            session.commit()


def deliver(session, decrypt, send_mail):
    now = time.time()
    ids = list(session.scalars(select(Order.id).where(
        Order.state == 'paid', Order.email_state == 'pending', Order.email_next <= now,
        or_(Order.email_claim == '', Order.email_claimed < now - 300)).limit(25)))
    for order_id in ids:
        claim = str(uuid.uuid4())
        changed = session.execute(update(Order).where(
            Order.id == order_id, Order.state == 'paid', Order.email_state == 'pending',
            or_(Order.email_claim == '', Order.email_claimed < now - 300)
        ).values(email_claim=claim, email_claimed=now)).rowcount
        session.commit()
        if not changed:
            continue
        order = session.get(Order, order_id)
        session.refresh(order)
        try:
            code = decrypt(order.encrypted_code)
            send_mail(order.email, 'Your MPS unlock code',
                      'Your payment is confirmed.\n\nOpen MPS → Licence and paste this code:\n\n'
                      + code + '\n\nThis subscription supports up to three registered applications.'
                      + '\nOrder: ' + order.id + '\n\nKeep this email for your records.')
            values = dict(email_state='accepted', email_claim='', email_claimed=0,
                          email_attempts=Order.email_attempts + 1)
        except Exception:
            values = dict(email_claim='', email_claimed=0, email_attempts=Order.email_attempts + 1,
                          email_next=time.time() + min(3600, 30 * 2 ** min(order.email_attempts + 1, 7)))
        session.execute(update(Order).where(Order.id == order_id, Order.email_claim == claim).values(**values))
        session.commit()


def reconcile(session, remote, order):
    """Use current Stripe state, not event delivery order, to determine entitlement."""
    sub = remote.get('/subscriptions/' + order.subscription_id)
    items = sub.get('items', {}).get('data', [])
    if (sub.get('livemode') is not order.live
            or sub.get('metadata', {}).get('mps_order_id') != order.id
            or len(items) != 1 or items[0].get('quantity') != 1
            or items[0].get('price', {}).get('id') != order.price_id):
        raise PaymentMismatch('Subscription does not match the order.')
    order.state = 'inactive'
    order.paid_through = 0
    if order.revoked or sub.get('status') != 'active' or sub.get('pause_collection'):
        return
    invoice_id = sub.get('latest_invoice', '')
    if isinstance(invoice_id, dict):
        invoice_id = invoice_id.get('id', '')
    if not invoice_id:
        return
    invoice = remote.get('/invoices/' + invoice_id)
    sid = invoice.get('subscription', '') or invoice.get('parent', {}).get('subscription_details', {}).get('subscription', '')
    if (invoice.get('status') != 'paid' or invoice.get('amount_paid') != order.amount
            or invoice.get('currency') != order.currency or sid != order.subscription_id
            or invoice.get('livemode') is not order.live):
        return
    period_end = sub.get('current_period_end') or items[0].get('current_period_end', 0)
    if period_end > time.time():
        order.paid_through = period_end
        order.state = 'paid'


def check_in(session, remote, code, installation_id, private_key, ip=''):
    """Fresh Stripe reconciliation followed by a short, installation-bound lease."""
    import hmac
    try:
        prefix, order_id, secret = code.split('.')
        if prefix != 'MPSK1' or len(secret) < 32 or len(code) > 200:
            raise ValueError()
        order_id = str(uuid.UUID(order_id))
        installation_id = str(uuid.UUID(installation_id))
        order = session.get(Order, order_id)
        if (not order
                or not hmac.compare_digest(order.activation_digest, hashlib.sha256(code.encode()).hexdigest())):
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('Invalid activation code or installation.') from None
    # Serialize registrations/removals on their parent order, including concurrent
    # requests for the last activation slot.
    session.execute(update(Order).where(Order.id == order.id).values(state=Order.state))
    session.refresh(order)
    activation = session.get(Activation, installation_id)
    if activation and activation.order_id != order.id:
        session.rollback()
        raise ValueError('Application ID belongs to another licence.')
    revoked = order.revoked or bool(activation and not activation.active)
    if not revoked:
        reconcile(session, remote, order)
    unlimited = order.state == 'paid' and not revoked
    if not activation and unlimited:
        count = session.scalar(select(func.count()).select_from(Activation).where(Activation.order_id == order.id, Activation.active == True))
        if count >= 3:
            session.rollback()
            raise ActivationLimit('Three applications are already registered. Remove one in your licence account first.')
        activation = Activation(application_id=installation_id, order_id=order.id, first_ip=ip[:45], last_ip=ip[:45])
        session.add(activation)
        session.add(ActivationLog(application_id=installation_id, order_id=order.id, action='registered', ip=ip[:45]))
    if activation:
        if activation.last_ip and activation.last_ip != ip[:45]:
            session.add(ActivationLog(application_id=installation_id, order_id=order.id, action='ip_changed', ip=ip[:45]))
        activation.last_ip = ip[:45]
        activation.last_seen = time.time()
    session.commit()
    expires = int(min(time.time() + 3600, order.paid_through)) if unlimited else int(time.time() + 3600)
    return issue(private_key, licence_id=order.id, installation_id=installation_id,
                 expires=expires, live=order.live, member_limit=-1 if revoked else 0 if unlimited else 10)


class ActivationLimit(ValueError):
    pass


def remove_activation(session, order_id, application_id, ip=''):
    session.execute(update(Order).where(Order.id == order_id).values(state=Order.state))
    activation = session.get(Activation, application_id)
    if not activation or activation.order_id != order_id:
        session.rollback()
        raise ValueError('Registration was not found.')
    activation.active = False
    activation.removed = time.time()
    session.add(ActivationLog(application_id=application_id, order_id=order_id, action='removed', ip=ip[:45]))
    session.commit()


def restore_activation(session, order_id, application_id, ip=''):
    session.execute(update(Order).where(Order.id == order_id).values(state=Order.state))
    activation = session.get(Activation, application_id)
    if not activation or activation.order_id != order_id:
        session.rollback();raise ValueError('Registration was not found.')
    count = session.scalar(select(func.count()).select_from(Activation).where(Activation.order_id == order_id, Activation.active == True))
    if not activation.active and count >= 3:
        session.rollback();raise ActivationLimit('Remove an active registration before restoring this one.')
    activation.active = True
    activation.removed = 0
    session.add(ActivationLog(application_id=application_id, order_id=order_id, action='restored_by_admin', ip=ip[:45]))
    session.commit()
