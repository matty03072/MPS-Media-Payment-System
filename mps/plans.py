"""Save pricing locally; publish through the worker so web requests never wait on providers."""
import time
from decimal import Decimal,InvalidOperation
from flask import Blueprint,request,abort,redirect,url_for,flash,render_template
from sqlalchemy import select,update
from .core import db,setting,admin_required,audit
from .models import Plan,PlanPublishJob,PayPalPlan,MemberPlan
from .emby import APIError
bp=Blueprint('plans',__name__)

def eligible(member,plan):
    assigned=db().get(MemberPlan,member.id)
    return bool(plan and (plan.active or (assigned and assigned.plan_id==plan.id)) and (not assigned or assigned.plan_id==plan.id))

def queue(plan,provider):
    if provider not in ('stripe','paypal'):abort(400,'Choose Stripe or PayPal.')
    key=plan.id+':'+provider;job=db().get(PlanPublishJob,key)
    if job and job.state=='running':return
    if not job:job=PlanPublishJob(id=key,plan_id=plan.id,provider=provider);db().add(job)
    job.state='pending';job.error='';db().commit()

def values():
    name=request.form.get('name','').strip()
    try:
        value=Decimal(request.form.get('amount','0'))
        if not value.is_finite() or value!=value.quantize(Decimal('.01')):raise ValueError()
        amount=int(value*100)
    except (InvalidOperation,ValueError,OverflowError):abort(400,'Enter a GBP amount with no more than two decimal places.')
    if not name or len(name)>80 or not 50<=amount<=100000:abort(400,'Enter a name and monthly price between £0.50 and £1000.')
    return name,amount

@bp.get('/admin/plans')
@admin_required
def page():
    from .paypal import live_mode
    return render_template('plans.html',plans=db().scalars(select(Plan).order_by(Plan.created.desc())).all(),
      paypal_plans={p.plan_id for p in db().scalars(select(PayPalPlan).where(PayPalPlan.live==live_mode())).all()},
      jobs={j.id:j for j in db().scalars(select(PlanPublishJob)).all()},worker_seen=float(setting('worker_seen','0')))

@bp.post('/admin/plans/<pid>/edit')
@admin_required
def edit(pid):
    old=db().get(Plan,pid)
    if not old:abort(404)
    name,amount=values()
    if amount!=old.amount:
        # Every price change is a new immutable price version, even for draft prices.
        old.active=False;new=Plan(name=name,amount=amount,currency=old.currency,active=True);db().add(new);db().commit()
        audit('Administrator created a new monthly price version');db().commit()
        flash('New monthly price saved. Existing invitations and members retain their original price; publish this version to your payment providers.','success')
    else:
        old.name=name;db().commit();flash('Plan name updated. Monthly amount is unchanged.','success')
    return redirect(url_for('plans.page'),303)

@bp.post('/admin/plans/<pid>/publish/<provider>')
@admin_required
def publish_route(pid,provider):
    plan=db().get(Plan,pid)
    if not plan:abort(404)
    queue(plan,provider);flash('Publication queued. Refresh this page for the result; you can continue using the site.','success')
    return redirect(url_for('plans.page'),303)

@bp.post('/admin/plans/<pid>/activate')
@admin_required
def activate(pid):
    plan=db().get(Plan,pid)
    if not plan:abort(404)
    plan.active=True;db().commit();flash('Plan available for new invitations. Provider publication is still required before checkout.','success')
    return redirect(url_for('plans.page'),303)

def tick():
    db().execute(update(PlanPublishJob).where(PlanPublishJob.state=='running',PlanPublishJob.claimed<time.time()-600).values(state='pending'));db().commit()
    for job in db().scalars(select(PlanPublishJob).where(PlanPublishJob.state=='pending').limit(10)).all():
        changed=db().execute(update(PlanPublishJob).where(PlanPublishJob.id==job.id,PlanPublishJob.state=='pending').values(state='running',claimed=time.time())).rowcount;db().commit()
        if not changed:continue
        try:
            plan=db().get(Plan,job.plan_id)
            if job.provider=='stripe':
                from .billing import api
                if not plan.stripe_price:
                    if not setting('stripe_secret',secret=True):raise APIError('Configure Stripe credentials before publishing this plan.')
                    result=api().post('/prices',{'unit_amount':str(plan.amount),'currency':plan.currency,'recurring[interval]':'month','product_data[name]':'MPS · '+plan.name,'metadata[plan_id]':plan.id},'plan-'+plan.id)
                    plan.stripe_price=result['id']
            elif job.provider=='paypal':
                from .paypal import publish
                if not setting('paypal_client_id') or not setting('paypal_secret',secret=True):raise APIError('Configure PayPal credentials before publishing this plan.')
                publish(plan)
            else:raise APIError('Unknown payment provider.')
            job.state='done';job.error='';db().commit()
        except Exception:
            db().rollback();job=db().get(PlanPublishJob,job.id);job.state='failed'
            job.error='Publication failed. Check provider credentials and connectivity, then retry. Your local plan is saved and unchanged.';db().commit()
