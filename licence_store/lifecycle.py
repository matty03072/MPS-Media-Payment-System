"""Customer cancellation, idempotent refunds and a private support outbox."""
import time, uuid
from sqlalchemy import select, update, or_
from .models import Order, Cancellation, SupportTicket
from .service import PaymentMismatch


def subscription(remote, order):
    sub = remote.get('/subscriptions/' + order.subscription_id)
    items = sub.get('items', {}).get('data', [])
    if (sub.get('livemode') is not order.live or sub.get('metadata', {}).get('mps_order_id') != order.id
            or len(items) != 1 or items[0].get('price', {}).get('id') != order.price_id
            or items[0].get('quantity') != 1):
        raise PaymentMismatch('Subscription ownership needs review.')
    return sub


def request_cancellation(session, remote, order):
    requested = time.time()
    sub = subscription(remote, order)
    session.execute(update(Order).where(Order.id == order.id).values(state=Order.state))
    existing = session.get(Cancellation, order.id)
    if existing:
        session.commit()
        return existing
    invoice = sub.get('latest_invoice') or ''
    if isinstance(invoice, dict):invoice = invoice['id']
    ends = sub.get('current_period_end') or sub['items']['data'][0].get('current_period_end', 0)
    if not invoice or not ends:
        session.rollback()
        raise PaymentMismatch('Billing period needs review before cancellation.')
    latest = remote.get('/invoices/'+invoice)
    if latest.get('status') != 'paid':
        paid_invoices = remote.get('/invoices?subscription='+order.subscription_id+'&status=paid&limit=1').get('data',[])
        if paid_invoices:invoice = paid_invoices[0]['id']
    row = Cancellation(order_id=order.id, invoice_id=invoice, ends=ends, requested=requested)
    session.add(row);session.commit()
    return row


def process_cancellations(session, remote):
    ids = list(session.scalars(select(Cancellation.order_id).where(
        Cancellation.next_attempt <= time.time(),
        or_(Cancellation.cancel_status != 'scheduled', Cancellation.refund_status.in_(['checking', 'pending']))).limit(25)))
    for oid in ids:
        claim = str(uuid.uuid4())
        changed = session.execute(update(Cancellation).where(Cancellation.order_id == oid,
            or_(Cancellation.claim == '', Cancellation.claimed < time.time()-300)).values(claim=claim, claimed=time.time())).rowcount
        session.commit()
        if not changed:continue
        try:
            row = session.get(Cancellation, oid);session.refresh(row)
            order = session.get(Order, oid)
            sub = subscription(remote, order)
            if row.cancel_status != 'scheduled':
                if sub.get('status') != 'canceled' and not sub.get('cancel_at_period_end'):
                    sub = remote.post('/subscriptions/'+order.subscription_id,
                        {'cancel_at_period_end':'true'}, 'mps-cancel-'+oid)
                if sub.get('status') != 'canceled' and not sub.get('cancel_at_period_end'):
                    raise ValueError('Cancellation was not confirmed.')
                row.cancel_status = 'scheduled';session.commit()
            if row.refund_status in ('checking', 'pending'):
                inv = remote.get('/invoices/'+row.invoice_id)
                sid = inv.get('subscription') or inv.get('parent', {}).get('subscription_details', {}).get('subscription')
                if (sid != order.subscription_id or inv.get('livemode') is not order.live
                        or inv.get('currency') != order.currency or inv.get('amount_paid') != order.amount):
                    raise PaymentMismatch('Invoice does not match this subscription.')
                paid = inv.get('status_transitions', {}).get('paid_at', 0)
                if inv.get('status') != 'paid' or not paid:
                    raise ValueError('A confirmed payment date is required.')
                if not 0 <= row.requested-paid <= 14*86400:
                    row.refund_status = 'not_eligible'
                else:
                    pi = inv.get('payment_intent')
                    if isinstance(pi, dict):pi = pi['id']
                    if not pi:raise ValueError('Payment reference needs review.')
                    # Recover uncertain API results even after Stripe's idempotency window.
                    refunds = remote.get('/refunds?payment_intent='+pi+'&limit=100')
                    if refunds.get('has_more'):raise ValueError('Refund history needs review.')
                    own = next((x for x in refunds.get('data', []) if x.get('metadata', {}).get('mps_cancellation') == oid), None)
                    if own:
                        refund = own
                    else:
                        existing = [x for x in refunds.get('data', []) if x.get('status') not in ('failed','canceled')]
                        if sum(x.get('amount',0) for x in existing) >= order.amount:
                            row.refund_status = 'succeeded' if all(x.get('status')=='succeeded' for x in existing) else 'pending'
                            session.commit();continue
                        if existing:raise ValueError('Partial refund needs administrator review.')
                        refund = remote.post('/refunds', {'payment_intent':pi,'amount':str(order.amount),
                            'reason':'requested_by_customer','metadata[mps_cancellation]':oid}, 'mps-refund-'+oid+'-'+row.invoice_id)
                    row.refund_id = refund['id']
                    row.refund_status = refund.get('status','pending') if refund.get('status') in ('succeeded','pending','failed','canceled') else 'needs_attention'
            row.error = '';row.next_attempt = time.time()+60;session.commit()
        except Exception:
            session.rollback();row = session.get(Cancellation, oid)
            row.attempts += 1;row.next_attempt = time.time()+min(3600,30*2**min(row.attempts,7))
            row.error = 'Processing delayed. We will retry; contact support if this persists.'
            session.commit()
        finally:
            session.execute(update(Cancellation).where(Cancellation.order_id==oid,Cancellation.claim==claim).values(claim='',claimed=0));session.commit()


def deliver_support(session, decrypt, send_mail, recipient):
    if not recipient:return
    ids = list(session.scalars(select(SupportTicket.id).where(SupportTicket.state=='pending',SupportTicket.next_attempt<=time.time()).limit(20)))
    for tid in ids:
        claim=str(uuid.uuid4())
        changed=session.execute(update(SupportTicket).where(SupportTicket.id==tid,SupportTicket.state=='pending',
            or_(SupportTicket.claim=='',SupportTicket.claimed<time.time()-300)).values(claim=claim,claimed=time.time())).rowcount
        session.commit()
        if not changed:continue
        row=session.get(SupportTicket,tid)
        try:
            send_mail(recipient,'MPS support request '+tid,decrypt(row.encrypted_message))
            row.state='accepted'
        except Exception:
            row.attempts+=1;row.next_attempt=time.time()+min(3600,30*2**min(row.attempts,7))
        row.claim='';row.claimed=0;session.commit()
