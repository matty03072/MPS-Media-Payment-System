import time
import stripe
from flask import Blueprint,abort,current_app,flash,redirect,request,url_for
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError
from .core import *
from .models import Member,Plan,Event
from .integrations import StripeAPI,stripe_key_mode
from .emby import APIError
bp=Blueprint('billing',__name__)

def api():return current_app.config.get('STRIPE_FACTORY',StripeAPI)()
def ident(value):return value.get('id','') if isinstance(value,dict) else value or ''
def subscription_id(invoice):
    return ident(invoice.get('subscription')) or ident(invoice.get('parent',{}).get('subscription_details',{}).get('subscription'))

def verified_subscription(member,sid):
    sub=api().get('/subscriptions/'+sid)
    if ident(sub.get('customer'))!=member.stripe_customer or sub.get('metadata',{}).get('member_id')!=member.id:
        raise APIError('Subscription ownership did not match this member.')
    return sub

@bp.post('/billing/checkout')
@login_required
def checkout():
    member=current_member()
    if not member.verified or not member.requires_payment:abort(403,'This membership is not eligible for checkout.')
    if setting('billing_enabled','0')!='1':abort(400,'Subscriptions are not enabled yet.')
    plan=db().get(Plan,request.form.get('plan_id',''))
    from .plans import eligible
    if not eligible(member,plan) or not plan.stripe_price:abort(400,'Choose an available monthly plan.')
    with member_lock(member):
        from .paypal import current_subscription,verified,TERMINAL
        pp=current_subscription(member)
        if pp:
            if not pp.remote_id or verified(pp).get('status') not in TERMINAL:abort(409,'Cancel your PayPal subscription before starting Stripe checkout.')
            pp.status='CANCELLED';db().commit()
        if member.stripe_subscription:
            sub=verified_subscription(member,member.stripe_subscription)
            if sub.get('status') not in ('canceled','incomplete_expired'):abort(409,'You already have a subscription. Manage it from your membership page.')
            member.stripe_subscription='';member.billing_cycle+=1;db().commit()
        if member.checkout_id:
            previous=api().get('/checkout/sessions/'+member.checkout_id)
            if previous.get('status')=='open':return redirect(previous['url'],303)
            if previous.get('status')=='complete' and ident(previous.get('subscription')):
                sid=ident(previous['subscription']);sub=verified_subscription(member,sid)
                if sub.get('status') not in ('canceled','incomplete_expired'):
                    member.stripe_subscription=sid;db().commit();abort(409,'Your checkout completed. Payment confirmation is being processed; do not create another subscription.')
            member.checkout_id='';member.billing_cycle+=1;db().commit()
        if not member.stripe_customer:
            customer=api().post('/customers',{'email':member.email,'metadata[member_id]':member.id},'customer-'+member.id)
            member.stripe_customer=customer['id'];db().commit()
        result=api().post('/checkout/sessions',{
          'mode':'subscription','customer':member.stripe_customer,'line_items[0][price]':plan.stripe_price,'line_items[0][quantity]':'1',
          'client_reference_id':member.id,'subscription_data[metadata][member_id]':member.id,
          'success_url':current_app.config['PUBLIC_URL']+'/member?checkout=complete',
          'cancel_url':current_app.config['PUBLIC_URL']+'/member?checkout=cancelled'},f'checkout-{member.id}-{member.billing_cycle}')
        member.checkout_id=result['id'];member.checkout_url=result['url'];member.checkout_expires=result.get('expires_at',0);db().commit()
        return redirect(result['url'],303)

@bp.post('/billing/cancel')
@login_required
def cancel():
    member=current_member()
    with member_lock(member):
        if not member.stripe_subscription:abort(400,'No subscription is linked to this membership.')
        sub=verified_subscription(member,member.stripe_subscription)
        if sub.get('status')!='canceled':
            api().post('/subscriptions/'+member.stripe_subscription,{'cancel_at_period_end':'true'},'cancel-'+member.stripe_subscription)
        member.cancel_pending=True;db().commit();audit('Member requested end-of-paid-period cancellation');db().commit()
    flash('Renewal cancelled. Access remains available through your paid-through date.','success')
    return redirect(url_for('portal.home'))

@bp.post('/billing/portal')
@login_required
def customer_portal():
    member=current_member()
    if not member.stripe_customer:abort(400,'No billing account exists yet.')
    result=api().post('/billing_portal/sessions',{'customer':member.stripe_customer,'return_url':current_app.config['PUBLIC_URL']+'/member'},'portal-'+member.id+'-'+str(int(time.time()//30)))
    return redirect(result['url'],303)

@bp.post('/stripe/webhook')
def webhook():
    secret=setting('stripe_webhook',secret=True)
    if not secret:abort(503,'Stripe webhook is not configured.')
    try:event=stripe.Webhook.construct_event(request.get_data(),request.headers.get('Stripe-Signature',''),secret,tolerance=300)
    except (ValueError,stripe.SignatureVerificationError):abort(400,'Invalid webhook signature.')
    supported={'invoice.paid','invoice.payment_failed','customer.subscription.updated','customer.subscription.deleted','checkout.session.completed','charge.refunded'}
    if event['type'] not in supported:return {'received':True}
    mode=stripe_key_mode(setting('stripe_secret',secret=True))
    if mode is None:abort(503,'Stripe API key is not configured.')
    live=mode=='live'
    if bool(event.get('livemode'))!=live:abort(400,'Stripe environment mismatch.')
    if db().get(Event,event['id']):return {'received':True}
    obj=event['data']['object']
    db().add(Event(id=event['id'],event_type=event['type'],object_id=obj['id'],live=live))
    try:db().commit()
    except IntegrityError:db().rollback()
    return {'received':True}

def process_event(event):
    remote=api();kind=event.event_type
    if kind.startswith('invoice.'):
        invoice=remote.get('/invoices/'+event.object_id)
        customer=ident(invoice.get('customer'));sid=subscription_id(invoice)
        member=db().scalar(select(Member).where(Member.stripe_customer==customer,Member.stripe_customer!=''))
        if not member or not sid:return
        sub=verified_subscription(member,sid)
        current_cycle=not member.stripe_subscription or member.stripe_subscription==sid
        if current_cycle:member.stripe_subscription=sid
        if kind=='invoice.paid' and invoice.get('status')=='paid':
            # Only full recurring subscription lines on our own monthly prices grant time.
            prices=set(db().scalars(select(Plan.stripe_price)).all())
            lines=remote.get('/invoices/'+event.object_id+'/lines?limit=100')
            if lines.get('has_more'):raise APIError('Invoice is too complex for automatic entitlement reconciliation.')
            ends=[]
            for line in lines.get('data',[]):
                price=ident(line.get('price')) or line.get('pricing',{}).get('price_details',{}).get('price','')
                if price in prices and not line.get('proration',False) and line.get('period',{}).get('end') and line.get('type','subscription')=='subscription':
                    ends.append(float(line['period']['end']))
            if ends:
                if current_cycle and (event.live or current_app.config.get('TESTING')):member.paid_through=max(member.paid_through,max(ends))
                from .revenue import receipt
                receipt('stripe',invoice['id'],member,int(invoice.get('amount_paid',0)),invoice.get('currency','gbp'),float(invoice.get('status_transitions',{}).get('paid_at') or invoice.get('created') or event.created),event.live)
            if not current_cycle:db().commit();return
            member.billing_status=sub.get('status','active');member.cancel_pending=bool(sub.get('cancel_at_period_end')) or sub.get('status')=='canceled'
        elif kind=='invoice.payment_failed' and invoice.get('status')=='open' and current_cycle:
            # Approved policy: stop automatic collection, no new paid access, no new final invoice.
            remote.post('/invoices/'+event.object_id,{'auto_advance':'false'},event.id+'-stop-collection')
            if sub.get('status')!='canceled':remote.delete('/subscriptions/'+sid,{'invoice_now':'false','prorate':'false'},event.id+'-cancel')
            member.billing_status='payment_failed';member.cancel_pending=True
        db().commit()
    elif kind.startswith('customer.subscription.'):
        sub=remote.get('/subscriptions/'+event.object_id)
        member=db().scalar(select(Member).where(Member.stripe_customer==ident(sub.get('customer')),Member.stripe_customer!=''))
        if not member or sub.get('metadata',{}).get('member_id')!=member.id:return
        if member.stripe_subscription and member.stripe_subscription!=sub['id']:return
        member.stripe_subscription=sub['id'];member.billing_status=sub['status'];member.cancel_pending=bool(sub.get('cancel_at_period_end')) or sub['status']=='canceled'
        # Subscription status alone never grants additional paid time.
        db().commit()
    elif kind=='checkout.session.completed':
        checkout=remote.get('/checkout/sessions/'+event.object_id)
        member=db().get(Member,checkout.get('client_reference_id',''))
        if not member or checkout.get('mode')!='subscription' or ident(checkout.get('customer'))!=member.stripe_customer:return
        sid=ident(checkout.get('subscription'))
        if sid and (not member.stripe_subscription or member.stripe_subscription==sid):
            sub=verified_subscription(member,sid);member.stripe_subscription=sid;member.billing_status=sub.get('status','pending');db().commit()

    elif kind=='charge.refunded':
        from .revenue import receipt
        charge=remote.get('/charges/'+event.object_id)
        member=db().scalar(select(Member).where(Member.stripe_customer==ident(charge.get('customer')),Member.stripe_customer!=''))
        if not member:return
        # Only refunds for subscriptions billed through this app enter the ledger.
        invoice_id=ident(charge.get('invoice'))
        if not invoice_id:return
        invoice=remote.get('/invoices/'+invoice_id)
        sid=subscription_id(invoice)
        if not sid:return
        verified_subscription(member,sid)
        cursor=''
        while True:
            page=remote.get('/refunds?charge='+event.object_id+'&limit=100'+('&starting_after='+cursor if cursor else ''))
            for refund in page.get('data',[]):
                if refund.get('status')=='succeeded':receipt('stripe',refund['id'],member,-int(refund['amount']),refund['currency'],float(refund['created']),event.live,'refund')
            if not page.get('has_more'):break
            if not page.get('data'):raise APIError('Incomplete refund pagination.')
            cursor=page['data'][-1]['id']
        db().commit()
