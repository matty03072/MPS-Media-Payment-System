"""Immutable invite attribution and settled cash receipts; never inferred commissions."""
from datetime import datetime,timezone
from flask import Blueprint,render_template,request,abort
from sqlalchemy import select
from .core import db,admin_required
from .models import DeletionJob,Member,MemberReferral,PaymentReceipt,InviteDispatch,InviteAttribution,Invite
bp=Blueprint('revenue',__name__)

def receipt(provider,remote_id,member,amount,currency,occurred,live,kind='payment'):
    key=f'{provider}:{int(live)}:{kind}:{remote_id}'
    if db().get(PaymentReceipt,key):return
    referral=db().get(MemberReferral,member.id)
    db().add(PaymentReceipt(id=key,provider=provider,live=live,member_id=member.id,
      admin_id=referral.admin_id if referral else None,amount=amount,currency=currency.lower(),occurred=occurred,kind=kind))
    db().flush()

@bp.get('/admin/revenue')
@admin_required
def dashboard():
    month=request.args.get('month',datetime.now(timezone.utc).strftime('%Y-%m'))
    try:
        start=datetime.strptime(month,'%Y-%m').replace(tzinfo=timezone.utc)
        end=start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)
    except ValueError:abort(400,'Choose a month in YYYY-MM format.')
    live=request.args.get('mode','live')!='sandbox'
    people={m.id:m for m in db().scalars(select(Member)).all()}
    rows=db().scalars(select(PaymentReceipt).where(PaymentReceipt.occurred>=start.timestamp(),PaymentReceipt.occurred<end.timestamp(),PaymentReceipt.live==live).order_by(PaymentReceipt.occurred.desc())).all()
    totals={(m.id,'gbp'):{'admin':m,'currency':'GBP','payments':0,'refunds':0,'members':set()} for m in people.values() if m.admin}
    for row in rows:
        key=(row.admin_id,row.currency)
        total=totals.setdefault(key,{'admin':people.get(row.admin_id),'currency':row.currency.upper(),'payments':0,'refunds':0,'members':set()})
        if row.amount>=0:total['payments']+=row.amount
        else:total['refunds']-=row.amount
        total['members'].add(row.member_id)
    dispatches=db().execute(select(InviteDispatch,Invite).join(Invite,Invite.id==InviteDispatch.invite_id).where(InviteDispatch.created>=start.timestamp(),InviteDispatch.created<end.timestamp()).order_by(InviteDispatch.created.desc())).all()
    return render_template('revenue.html',deleted_ids=set(db().scalars(select(DeletionJob.member_id))),month=month,live=live,totals=list(totals.values()),receipts=rows,people=people,dispatches=dispatches,
      attribution={a.invite_id:a for a in db().scalars(select(InviteAttribution)).all()})
