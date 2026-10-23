"""PayPal monthly subscriptions. Approval alone never grants access."""
import calendar,json,time
from datetime import datetime,timezone
from decimal import Decimal
from urllib.parse import urlsplit,quote
from flask import Blueprint,current_app,request,abort,redirect,flash,url_for
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError
from .core import db,setting,put,current_member,login_required,member_lock,audit
from .models import Member,Plan,PayPalPlan,PayPalSubscription,PayPalEvent,PaymentReceipt
from .emby import APIError,request as remote_request
from .revenue import receipt
bp=Blueprint('paypal',__name__)
TERMINAL={'CANCELLED','EXPIRED'}
EVENTS={'PAYMENT.SALE.COMPLETED','PAYMENT.SALE.REFUNDED','PAYMENT.SALE.REVERSED','BILLING.SUBSCRIPTION.PAYMENT.FAILED','BILLING.SUBSCRIPTION.ACTIVATED','BILLING.SUBSCRIPTION.CANCELLED','BILLING.SUBSCRIPTION.SUSPENDED','BILLING.SUBSCRIPTION.EXPIRED'}

def live_mode():return setting('paypal_mode','sandbox')=='live'
def stamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
def pennies(value):
    n=Decimal(str(value))*100
    if not n.is_finite() or n!=n.to_integral_value() or n<0:raise APIError('Invalid PayPal payment amount.')
    return int(n)
def next_month(value):
    d=datetime.fromtimestamp(value,timezone.utc);year=d.year+(d.month==12);month=d.month%12+1
    return d.replace(year=year,month=month,day=min(d.day,calendar.monthrange(year,month)[1])).timestamp()

class PayPalAPI:
    def __init__(self):self.base='https://api-m.paypal.com' if live_mode() else 'https://api-m.sandbox.paypal.com';self.token=''
    def call(self,method,path,data=None,key=None):
        if not self.token:
            client=setting('paypal_client_id');secret=setting('paypal_secret',secret=True)
            if not client or not secret:raise APIError('PayPal is not configured.')
            result=remote_request('POST',self.base+'/v1/oauth2/token',auth=(client,secret),data={'grant_type':'client_credentials'})
            self.token=result['access_token']
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Prefer':'return=representation'}
        if key:headers['PayPal-Request-Id']=key
        return remote_request(method,self.base+path,headers=headers,json=data)
    def get(self,path):return self.call('GET',path)
    def post(self,path,data,key=None):return self.call('POST',path,data,key)
def api():return current_app.config.get('PAYPAL_FACTORY',PayPalAPI)()

def publish(plan):
    existing=db().get(PayPalPlan,(plan.id,live_mode()))
    if existing:return existing
    remote=api();product=remote.post('/v1/catalogs/products',{'name':'MPS membership','type':'SERVICE','category':'SOFTWARE'},'product-mps')
    result=remote.post('/v1/billing/plans',{'product_id':product['id'],'name':plan.name,'status':'ACTIVE',
      'billing_cycles':[{'frequency':{'interval_unit':'MONTH','interval_count':1},'tenure_type':'REGULAR','sequence':1,'total_cycles':0,'pricing_scheme':{'fixed_price':{'value':f'{plan.amount/100:.2f}','currency_code':plan.currency.upper()}}}],
      'payment_preferences':{'auto_bill_outstanding':False,'payment_failure_threshold':1,'setup_fee_failure_action':'CANCEL'}},plan.id)
    row=PayPalPlan(plan_id=plan.id,live=live_mode(),remote_id=result['id']);db().add(row);db().commit();return row

def current_subscription(member):
    return db().scalar(select(PayPalSubscription).where(PayPalSubscription.member_id==member.id,PayPalSubscription.status.notin_(TERMINAL)).order_by(PayPalSubscription.created.desc()))

def verified(row,remote=None):
    if row.live!=live_mode():raise APIError('PayPal environment mismatch; restore the original configuration.')
    sub=(remote or api()).get('/v1/billing/subscriptions/'+quote(row.remote_id,safe=''))
    plan=db().get(PayPalPlan,(row.plan_id,row.live))
    if not plan or sub.get('id')!=row.remote_id or sub.get('custom_id')!=row.member_id or sub.get('plan_id')!=plan.remote_id:
        raise APIError('PayPal subscription ownership or plan did not match.')
    return sub

@bp.post('/billing/paypal/checkout')
@login_required
def checkout():
    from .billing import verified_subscription,api as stripe_api,ident
    member=current_member()
    if not member.verified or not member.requires_payment:abort(403,'This membership is not eligible for checkout.')
    if setting('paypal_enabled','0')!='1':abort(400,'PayPal subscriptions are not enabled.')
    plan=db().get(Plan,request.form.get('plan_id',''))
    mapping=db().get(PayPalPlan,(plan.id,live_mode())) if plan else None
    from .plans import eligible
    if not eligible(member,plan) or not mapping:abort(400,'Choose a plan published to PayPal.')
    with member_lock(member):
        if member.stripe_subscription and verified_subscription(member,member.stripe_subscription).get('status') not in ('canceled','incomplete_expired'):abort(409,'Cancel your existing Stripe subscription before changing payment provider.')
        if member.checkout_id:
            previous=stripe_api().get('/checkout/sessions/'+member.checkout_id)
            if previous.get('status')=='open':abort(409,'Finish or let your existing Stripe checkout expire before changing provider.')
            sid=ident(previous.get('subscription'))
            if sid and verified_subscription(member,sid).get('status') not in ('canceled','incomplete_expired'):abort(409,'An existing Stripe subscription is still active.')
        row=current_subscription(member);remote=api()
        if row and row.remote_id:
            sub=verified(row,remote);row.status=sub['status'];db().commit()
            if row.status in TERMINAL:row=None
            elif row.status=='APPROVAL_PENDING' and row.approval_url:return redirect(row.approval_url,303)
            else:abort(409,'You already have a PayPal subscription; use its cancellation control.')
        if not row:
            # Start the first charge after existing paid access, if switching after cancellation.
            row=PayPalSubscription(member_id=member.id,plan_id=plan.id,live=live_mode());db().add(row);db().commit()
        if row.plan_id!=plan.id or row.live!=live_mode():abort(409,'A different checkout is already pending.')
        if time.time()-row.created>48*3600:abort(409,'This checkout needs administrator reconciliation before another can be created.')
        body={'plan_id':mapping.remote_id,'custom_id':member.id,'application_context':{'brand_name':'MPS','user_action':'SUBSCRIBE_NOW','shipping_preference':'NO_SHIPPING','return_url':current_app.config['PUBLIC_URL']+'/member?paypal=approved','cancel_url':current_app.config['PUBLIC_URL']+'/member?paypal=cancelled'}}
        if member.paid_through>time.time()+60:body['start_time']=datetime.fromtimestamp(member.paid_through,timezone.utc).isoformat().replace('+00:00','Z')
        result=remote.post('/v1/billing/subscriptions',body,row.id)
        link=next((x['href'] for x in result.get('links',[]) if x.get('rel')=='approve'),'')
        parsed=urlsplit(link)
        if parsed.scheme!='https' or parsed.hostname not in ('www.paypal.com','www.sandbox.paypal.com'):raise APIError('PayPal did not provide a valid approval link.')
        row.remote_id=result['id'];row.status=result['status'];row.approval_url=link;db().commit()
        return redirect(link,303)

@bp.post('/billing/paypal/cancel')
@login_required
def cancel():
    member=current_member()
    with member_lock(member):
        row=current_subscription(member)
        if not row or not row.remote_id:abort(400,'No PayPal subscription is available to cancel.')
        remote=api();sub=verified(row,remote)
        if sub['status'] not in TERMINAL:remote.post('/v1/billing/subscriptions/'+row.remote_id+'/cancel',{'reason':'Member cancelled renewal'},'cancel-'+row.id)
        row.status='CANCELLED';member.cancel_pending=True;db().commit();audit('Member cancelled PayPal renewal');db().commit()
    flash('PayPal renewal cancelled. Existing paid access remains until the paid-through date.','success')
    return redirect(url_for('portal.home'))

@bp.post('/paypal/webhook')
def webhook():
    webhook_id=setting('paypal_webhook_id')
    if not webhook_id:abort(503,'PayPal webhook is not configured.')
    event=request.get_json(silent=True)
    if not isinstance(event,dict) or not event.get('id'):abort(400,'Invalid PayPal event.')
    fields={'auth_algo':'PAYPAL-AUTH-ALGO','cert_url':'PAYPAL-CERT-URL','transmission_id':'PAYPAL-TRANSMISSION-ID','transmission_sig':'PAYPAL-TRANSMISSION-SIG','transmission_time':'PAYPAL-TRANSMISSION-TIME'}
    payload={k:request.headers.get(v,'') for k,v in fields.items()}
    if not all(payload.values()):abort(400,'Missing PayPal signature headers.')
    payload.update(webhook_id=webhook_id,webhook_event=event)
    if api().post('/v1/notifications/verify-webhook-signature',payload).get('verification_status')!='SUCCESS':abort(400,'Invalid PayPal webhook signature.')
    if event.get('event_type') not in EVENTS:return {'received':True}
    key=f'{int(live_mode())}:{event["id"]}'
    if len(key)>160:abort(400)
    if not db().get(PayPalEvent,key):
        db().add(PayPalEvent(id=key,event_type=event['event_type'],payload=json.dumps(event),live=live_mode()))
        try:db().commit()
        except IntegrityError:db().rollback()
    return {'received':True}

def process_event(event):
    if event.live!=live_mode():raise APIError('PayPal event environment mismatch.')
    resource=json.loads(event.payload)['resource'];kind=event.event_type;remote=api()
    if kind.startswith('PAYMENT.SALE.'):
        if kind=='PAYMENT.SALE.REFUNDED':
            refund=remote.get('/v1/payments/refund/'+quote(resource['id'],safe=''))
            if refund.get('state')!='completed':raise APIError('Refund confirmation is pending; retry reconciliation.')
            sale=remote.get('/v1/payments/sale/'+quote(refund['sale_id'],safe=''))
        else:sale=remote.get('/v1/payments/sale/'+quote(resource['id'],safe=''))
        sid=sale.get('billing_agreement_id','')
    else:sid=resource.get('id','')
    row=db().scalar(select(PayPalSubscription).where(PayPalSubscription.remote_id==sid,PayPalSubscription.remote_id!='',PayPalSubscription.live==event.live))
    if not row:raise APIError('PayPal subscription is not yet linked; reconciliation will retry.')
    member=db().get(Member,row.member_id)
    with member_lock(member):
        sub=verified(row,remote);row.status=sub['status'];plan=db().get(Plan,row.plan_id)
        if kind=='PAYMENT.SALE.COMPLETED':
            if sale.get('state') not in ('completed','partially_refunded','refunded'):raise APIError('Payment confirmation is pending; retry reconciliation.')
            amount=pennies(sale['amount']['total']);currency=sale['amount']['currency'].lower();paid=stamp(sale['create_time'])
            if amount!=plan.amount or currency!=plan.currency:raise APIError('PayPal payment does not match the monthly plan price.')
            # Only a verified full monthly payment grants a period; ACTIVATED and browser redirects do not.
            # No trials, setup fees, quantities or variable pricing are created by this application.
            end=next_month(paid)
            last=sub.get('billing_info',{}).get('last_payment',{})
            next_date=sub.get('billing_info',{}).get('next_billing_time')
            if next_date and last.get('time') and abs(stamp(last['time'])-paid)<86400:
                candidate=stamp(next_date)
                if paid<candidate<=end+86400:end=candidate
            receipt('paypal',sale['id'],member,amount,currency,paid,event.live)
            if event.live or current_app.config.get('TESTING'):member.paid_through=max(member.paid_through,end)
            member.billing_status='active' if row.status=='ACTIVE' else row.status.lower()
            member.cancel_pending=row.status in TERMINAL
        elif kind=='PAYMENT.SALE.REFUNDED':
            receipt('paypal',refund['id'],member,-pennies(refund['amount']['total']),refund['amount']['currency'],stamp(refund['create_time']),event.live,'refund')
        elif kind=='PAYMENT.SALE.REVERSED':
            if sale.get('state')!='reversed':return
            receipt('paypal',sale['id'],member,-pennies(sale['amount']['total']),sale['amount']['currency'],stamp(resource.get('update_time') or resource['create_time']),event.live,'reversal')
        elif kind=='BILLING.SUBSCRIPTION.PAYMENT.FAILED':
            # Ignore a late failure event if PayPal shows a later successful collection.
            failed=stamp(resource.get('status_update_time') or json.loads(event.payload)['create_time'])
            last=sub.get('billing_info',{}).get('last_payment',{}).get('time')
            if last and stamp(last)>failed:return
            if row.status not in TERMINAL:remote.post('/v1/billing/subscriptions/'+row.remote_id+'/cancel',{'reason':'Failed renewal; stop further automatic collection'},'failed-'+event.id[-30:])
            row.status='CANCELLED';member.billing_status='payment_failed';member.cancel_pending=True
        else:
            member.billing_status=row.status.lower();member.cancel_pending=row.status in TERMINAL
        db().commit()

def tick():
    db().execute(update(PayPalEvent).where(PayPalEvent.state=='running',PayPalEvent.claimed<time.time()-600).values(state='retry'));db().commit()
    for event in db().scalars(select(PayPalEvent).where(PayPalEvent.state.in_(['pending','retry'])).order_by(PayPalEvent.created).limit(50)).all():
        changed=db().execute(update(PayPalEvent).where(PayPalEvent.id==event.id,PayPalEvent.state.in_(['pending','retry'])).values(state='running',claimed=time.time())).rowcount;db().commit()
        if not changed:continue
        try:process_event(event);event.state='done';event.error=''
        except Exception:
            db().rollback();event=db().get(PayPalEvent,event.id);event.state='retry';event.error='PayPal reconciliation needs attention. Check configuration and provider availability.'
        db().commit()
