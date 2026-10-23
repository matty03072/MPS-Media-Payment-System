import time
from sqlalchemy import select,update
from .core import db,put,audit,member_lock
from .models import Event,Member
from .billing import process_event
from .provision import sync_access
from .emby import APIError

def tick():
    # Run exactly one scheduled worker instance; compare-and-update also prevents duplicate event claims.
    db().expire_all()
    db().execute(update(Event).where(Event.state=='running',Event.claimed<time.time()-600).values(state='retry'));db().commit()
    events=db().scalars(select(Event).where(Event.state.in_(['pending','retry'])).order_by(Event.created).limit(50)).all()
    for event in events:
        changed=db().execute(update(Event).where(Event.id==event.id,Event.state.in_(['pending','retry'])).values(state='running',claimed=time.time())).rowcount;db().commit()
        if not changed:continue
        try:process_event(event);event.state='done';event.error=''
        except Exception:
            db().rollback();event=db().get(Event,event.id);event.state='retry';event.error='Reconciliation failed; check integration availability and configuration.'
        db().commit()
    from .paypal import tick as paypal_tick
    paypal_tick()
    from .deletion import tick as deletion_tick,visible_members
    deletion_tick()
    from .plans import tick as plans_tick
    plans_tick()
    from .onboarding import tick as onboarding_tick,refresh_pending_links
    from .passwords import tick as password_tick
    password_tick()
    onboarding_tick()
    for member in db().scalars(select(Member).where(Member.admin==False,visible_members())).all():
        if member.operation_lock:continue
        try:
            with member_lock(member):
                sync_access(member);refresh_pending_links(member)
        except Exception:db().rollback();audit('Member access sync requires attention');db().commit()
    from .availability import tick as availability_tick
    availability_tick()
    put('worker_seen',str(time.time()));db().commit()
