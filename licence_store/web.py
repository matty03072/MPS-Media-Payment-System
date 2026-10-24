"""Install only on the owner's Members server, using its existing auth/Stripe/SMTP."""
import importlib
import re
import time
import uuid
from pathlib import Path
import click
import stripe
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from mps_licence_tokens import encode
from .models import Base, Order, PaymentEvent, Activation
from .service import process_events, deliver, check_in, ActivationLimit, remove_activation, restore_activation


def install(app, host_package='streaminghome'):
    core = importlib.import_module(host_package + '.core')
    integration = importlib.import_module(host_package + '.integrations')
    mail = importlib.import_module(host_package + '.mail')
    bp = Blueprint('mps_store', __name__, template_folder='templates', static_folder='static', static_url_path='/mps-assets')
    from types import SimpleNamespace
    host_mail = mail
    def licence_mail(to, subject, body):
        if subject == 'Your MPS unlock code':
            body += ('\n\nSubscription contract — MPS, 24 October 2026\nSeller: '+core.setting('mps_seller_name')+'\n'+core.setting('mps_seller_address')+
                '\nContact: '+core.setting('mps_public_contact')+'\nGBP 5 per month; three applications; monthly renewal until cancelled. No delivery fee.\n'
                'Cancel in your licence account. Cancellation is effective at the current billing period end. Request within 14 days of the latest payment for a full refund of that payment. Licence access remains until the billing period end. Statutory rights are unaffected.\n'
                'Cancellation requests can also be sent to the seller contact with your account email and order reference.\nPrivacy notice: '+app.config['PUBLIC_URL']+'/mps/privacy\n\n'+Path(__file__).resolve().parent.joinpath('TERMS.txt').read_text())
        return host_mail.send_mail(to, subject, body, **({'brand':'MPS'} if host_package == 'streaminghome' else {}))
    mail = SimpleNamespace(send_mail=licence_mail)
    supported = {'checkout.session.completed', 'checkout.session.async_payment_succeeded',
                 'invoice.paid', 'invoice.payment_failed', 'customer.subscription.updated', 'customer.subscription.deleted'}

    def remote():
        return app.config.get('MPS_STORE_STRIPE_FACTORY', integration.StripeAPI)()

    def signing_key():
        try:return Ed25519PrivateKey.from_private_bytes(Path(app.config['DATA_DIR'], 'mps-signing.key').read_bytes())
        except (OSError, ValueError):abort(503, 'Licence signing is not configured.')

    def mode():
        value = integration.stripe_key_mode(core.setting('stripe_secret', secret=True))
        if value is None:
            abort(503, 'Payment service is not configured.')
        return value == 'live'

    from .accounts import install as install_accounts
    current_customer, customer_required = install_accounts(app, core, mail, remote)

    @bp.get('/mps')
    def shop():
        return render_template('mps_store/shop.html', installation=request.args.get('installation', ''),
                               enabled=core.setting('mps_sales_enabled') == '1')

    @bp.post('/mps/checkout')
    @customer_required
    def checkout():
        core.rate_limit('mps-checkout', 10)
        if core.setting('mps_sales_enabled') != '1':
            abort(503, 'Licence sales are not enabled yet.')
        customer = current_customer()
        email = customer.email
        if request.form.get('terms') != 'yes':
            abort(400, 'Accept the licence and monthly subscription terms.')
        installation = ''
        # Serialize checkout creation per customer, and retry uncertain API calls
        # with the same idempotency key rather than starting a second subscription.
        from sqlalchemy import update
        from .models import Customer
        core.db().execute(update(Customer).where(Customer.id == customer.id).values(version=Customer.version))
        existing = core.db().scalar(select(Order).where(Order.customer_id == customer.id).order_by(Order.created.desc()))
        if existing:
            if existing.revoked:abort(403, 'This licence has been revoked. Contact support.')
            if existing.subscription_id:
                sub = remote().get('/subscriptions/'+existing.subscription_id)
                if sub.get('status') not in ('canceled', 'incomplete_expired'):
                    core.db().commit()
                    return redirect(url_for('mps_accounts.dashboard'))
                # Preserve the prior cancellation and refund audit trail.
                # A new purchase receives a new order and unlock code.
                existing = None
            elif existing.checkout_id:
                previous=remote().get('/checkout/sessions/'+existing.checkout_id)
                if previous.get('status')=='open':
                    core.db().commit();return redirect(previous['url'],303)
                if previous.get('status')=='complete':
                    event_id='reconcile-'+existing.checkout_id
                    if not core.db().get(PaymentEvent,event_id):
                        core.db().add(PaymentEvent(id=event_id,event_type='checkout.session.completed',object_id=existing.checkout_id,live=existing.live))
                    core.db().commit();return redirect(url_for('mps_store.thanks'))
                existing.checkout_id='';existing.checkout_cycle+=1
        price_id = core.setting('mps_stripe_price')
        price = remote().get('/prices/' + price_id)
        recurring = price.get('recurring', {})
        live = mode()
        if (not price.get('active') or price.get('unit_amount') != 500 or price.get('currency') != 'gbp'
                or price.get('livemode') is not live or recurring.get('interval') != 'month'
                or recurring.get('interval_count') != 1 or recurring.get('usage_type', 'licensed') != 'licensed'):
            abort(503, 'The MPS £5 monthly price needs administrator attention.')
        signing_key()  # Do not accept payment before an issuer key exists.
        if existing and (existing.live != live or existing.price_id != price_id):
            abort(409, 'Existing order payment settings changed. Administrator review is required.')
        order = existing or Order(customer_id=customer.id, email=email, installation_id=installation, amount=500, currency='gbp', price_id=price_id, live=live)
        core.db().add(order); core.db().commit()
        result = remote().post('/checkout/sessions', {
            'mode':'subscription', 'customer_email':email,
            'line_items[0][price]':price_id, 'line_items[0][quantity]':'1',
            'client_reference_id':order.id, 'metadata[product]':'mps-unlock', 'metadata[order_id]':order.id,
            'subscription_data[metadata][mps_order_id]':order.id,
            'success_url':app.config['PUBLIC_URL']+'/mps/thanks',
            'cancel_url':app.config['PUBLIC_URL']+'/mps',
            'allow_promotion_codes':'false'}, 'mps-licence-checkout-'+order.id+'-'+str(order.checkout_cycle))
        order.checkout_id = result['id']; core.db().commit()
        return redirect(result['url'], 303)

    @bp.get('/mps/thanks')
    def thanks():
        return render_template('mps_store/thanks.html')

    @bp.get('/mps/terms')
    def terms():
        from flask import Response
        seller = 'MPS subscription service\nSeller: '+core.setting('mps_seller_name')+'\n'+core.setting('mps_seller_address')+'\nContact: '+core.setting('mps_public_contact')+'\nCancellation and refunds: '+app.config['PUBLIC_URL']+'/mps/cancellation\nPrivacy: '+app.config['PUBLIC_URL']+'/mps/privacy\n\n'
        return Response(seller+Path(__file__).resolve().parent.joinpath('TERMS.txt').read_text(), mimetype='text/plain')

    @bp.get('/mps/privacy')
    def privacy():
        return render_template('mps_store/policy.html',kind='privacy',seller=core.setting('mps_seller_name'),address=core.setting('mps_seller_address'),contact=core.setting('mps_public_contact'))

    @bp.get('/mps/cancellation')
    def cancellation_policy():
        return render_template('mps_store/policy.html',kind='cancellation',seller=core.setting('mps_seller_name'),address=core.setting('mps_seller_address'),contact=core.setting('mps_public_contact'))

    @bp.post('/mps/stripe/webhook')
    def webhook():
        secret = core.setting('mps_stripe_webhook', secret=True)
        if not secret:
            abort(503, 'Licence webhook is not configured.')
        try:
            event = stripe.Webhook.construct_event(request.get_data(), request.headers.get('Stripe-Signature', ''), secret)
        except (ValueError, stripe.SignatureVerificationError):
            abort(400, 'Invalid webhook signature.')
        if event['type'] not in supported:
            return {'received':True}
        live = mode()
        if bool(event.get('livemode')) != live:
            abort(400, 'Stripe environment mismatch.')
        if not core.db().get(PaymentEvent, event['id']):
            core.db().add(PaymentEvent(id=event['id'], event_type=event['type'],
                                      object_id=event['data']['object']['id'], live=live))
            try: core.db().commit()
            except IntegrityError: core.db().rollback()
        return {'received':True}

    @bp.post('/mps/api/check-in')
    def checkin():
        core.rate_limit('mps-check-in', 100)
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):abort(400, 'Expected a JSON object.')
        try:
            lease = check_in(core.db(), remote(), body.get('code', ''), body.get('installation_id', ''), signing_key(), request.remote_addr or '')
        except ActivationLimit as error:
            return {'error':str(error)}, 409
        except ValueError:
            abort(403, 'Invalid activation code or installation.')
        return {'lease':lease, 'check_in_after':900}

    @bp.route('/admin/mps-licences', methods=['GET','POST'])
    @core.admin_required
    def administration():
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'settings':
                price_id = request.form.get('price_id', '').strip()
                if not re.fullmatch(r'price_[A-Za-z0-9]+', price_id):
                    abort(400, 'Enter a Stripe Price ID.')
                core.put('mps_stripe_price', price_id)
                webhook_secret = request.form.get('webhook_secret', '').strip()
                if webhook_secret:
                    if not webhook_secret.startswith('whsec_'):abort(400, 'Enter the licence endpoint signing secret.')
                    core.put('mps_stripe_webhook', webhook_secret, secret=True)
                core.put('mps_sales_enabled', '1' if request.form.get('enabled') == 'yes' else '0')
                core.audit('MPS licence sales configuration updated')
            else:
                order = core.db().get(Order, request.form.get('order_id', ''))
                if not order:abort(404)
                if action == 'resend' and order.encrypted_code:
                    order.email_state='pending';order.email_next=0
                elif action in ('revoke', 'restore'):
                    order.revoked = action == 'revoke'
                elif action in ('remove_application', 'restore_application'):
                    operation = remove_activation if action == 'remove_application' else restore_activation
                    try:operation(core.db(), order.id, request.form.get('application_id',''), request.remote_addr or '')
                    except ValueError as error:abort(400,str(error))
                else:abort(400, 'Unknown licence action.')
                core.audit('MPS licence order '+order.id+': '+action)
            core.db().commit();flash('Licence administration updated.', 'success')
            return redirect(url_for('mps_store.administration'))
        orders = core.db().scalars(select(Order).order_by(Order.created.desc()).limit(200)).all()
        registrations={order.id:core.db().scalars(select(Activation).where(Activation.order_id==order.id)).all() for order in orders}
        return render_template('mps_store/admin.html', orders=orders, registrations=registrations)

    app.register_blueprint(bp)
    # Exempt only these machine-authenticated endpoints from the host's form CSRF
    # middleware. Checkout and every admin mutation keep its original CSRF checks.
    originals = app.before_request_funcs.get(None, [])
    found = False
    for index, fn in enumerate(originals):
        if fn.__name__ == 'csrf':
            def guarded_csrf(original=fn):
                if request.endpoint in ('mps_store.webhook', 'mps_store.checkin'):
                    return None
                return original()
            originals[index] = guarded_csrf
            found = True
            break
    if not found:
        raise RuntimeError('Expected host CSRF middleware was not found.')

    @app.cli.command('mps-store-init')
    def initialize():
        import os
        Base.metadata.create_all(app.extensions['engine'])
        path = Path(app.config['DATA_DIR'], 'mps-signing.key')
        if not path.exists():
            private = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
            with os.fdopen(os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'wb') as handle:
                handle.write(private)
        click.echo('Licence tables ready. Public verification key: '+encode(signing_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)))

    @app.cli.command('mps-store-worker')
    @click.option('--once', is_flag=True)
    def worker(once):
        while True:
            process_events(core.db(), remote(), signing_key(), core.seal)
            deliver(core.db(), core.unseal, mail.send_mail)
            from .lifecycle import process_cancellations, deliver_support
            process_cancellations(core.db(), remote())
            deliver_support(core.db(), core.unseal, mail.send_mail, core.setting('mps_support_email'))
            if once:break
            time.sleep(30)
