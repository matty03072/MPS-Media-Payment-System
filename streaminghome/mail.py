import smtplib, ssl, time, secrets
from email.message import EmailMessage
from flask import current_app
from email.utils import formataddr
from pathlib import Path
from urllib.parse import urlsplit
from .core import db,setting,digest
from .models import OneTimeToken
from .emby import APIError

def build_message(to, sender, subject, body, *, heading=None, intro=None, action_url=None, action_label='Open StreamingHome', note=None, content_poster=None, content_title='', recommendations=None, ai_recommendations=False):
    if any('\n' in x or '\r' in x for x in (to,sender,subject)):
        raise APIError('Invalid email header.')
    if action_url and (urlsplit(action_url).scheme not in ('https','http') or urlsplit(action_url).netloc != urlsplit(current_app.config['PUBLIC_URL']).netloc):
        raise APIError('Invalid account email link.')
    recommendations=[dict(r) for r in (recommendations or [])[:3]]
    for index,rec in enumerate(recommendations):
        rec['poster_cid']=f'recommendation-poster-{index+1}' if rec.get('poster') else None
    msg=EmailMessage()
    msg['From']=formataddr((setting('site_name','Members Portal'),sender));msg['To']=to;msg['Subject']=subject
    msg.set_content(body)
    html=current_app.jinja_env.get_template('email/message.html').render(subject=subject,heading=heading or subject,
        paragraphs=(intro if intro is not None else body).split('\n\n'),action_url=action_url,
        action_label=action_label,note=note,home_url=current_app.config['PUBLIC_URL'], content_poster=bool(content_poster), content_title=content_title, recommendations=recommendations or [], ai_recommendations=ai_recommendations)
    msg.add_alternative(html,subtype='html')
    if content_poster:
        data,subtype=content_poster
        if subtype not in ('jpeg','png') or len(data)>2_000_000:raise APIError('Invalid content poster.')
        msg.get_payload()[-1].add_related(data,maintype='image',subtype=subtype,cid='<content-poster>',disposition='inline',filename='poster.'+subtype)
    for rec in recommendations:
        if rec.get('poster'):
            data,subtype=rec['poster']
            if subtype not in ('jpeg','png') or len(data)>2_000_000:raise APIError('Invalid recommendation poster.')
            msg.get_payload()[-1].add_related(data,maintype='image',subtype=subtype,cid='<'+rec['poster_cid']+'>',disposition='inline',filename=rec['poster_cid']+'.'+subtype)
    return msg

def send_mail(to,subject,body,**presentation):
    if current_app.config.get('MAIL_SENDER'):
        return current_app.config['MAIL_SENDER'](to,subject,body)
    host=setting('smtp_host');sender=setting('smtp_from')
    if not host or not sender:raise APIError('SMTP has not been configured by the administrator.')
    if any('\n' in x or '\r' in x for x in (to,sender,subject)):raise APIError('Invalid email header.')
    msg=build_message(to,sender,subject,body,**presentation)
    try:
        port=int(setting('smtp_port','587'))
        cls=smtplib.SMTP_SSL if setting('smtp_mode','starttls')=='ssl' else smtplib.SMTP
        options={'context':ssl.create_default_context()} if cls is smtplib.SMTP_SSL else {}
        with cls(host,port,timeout=10,**options) as smtp:
            if cls is smtplib.SMTP:smtp.ehlo();smtp.starttls(context=ssl.create_default_context());smtp.ehlo()
            if setting('smtp_username'):smtp.login(setting('smtp_username'),setting('smtp_password',secret=True))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise APIError('SMTP authentication failed. Check the SMTP username and password.') from None
    except ssl.SSLCertVerificationError:
        raise APIError('SMTP TLS certificate validation failed. Check the SMTP hostname and certificate chain.') from None
    except (TimeoutError,smtplib.SMTPServerDisconnected):
        raise APIError('SMTP connection timed out or disconnected. Check the port and encryption mode: SMTP2GO port 8025 requires STARTTLS, not SSL.') from None
    except (smtplib.SMTPSenderRefused,smtplib.SMTPRecipientsRefused,smtplib.SMTPDataError):
        raise APIError('SMTP rejected the sender, recipient or message. Check your provider delivery logs and verified sending domain.') from None
    except (OSError,smtplib.SMTPException,ValueError):
        raise APIError('Email delivery failed. Check SMTP configuration; no credentials have been logged.') from None

def token_email(member,purpose):
    token=secrets.token_urlsafe(32)
    db().add(OneTimeToken(digest=digest(token),member_id=member.id,purpose=purpose,expires=time.time()+1800));db().commit()
    link=current_app.config['PUBLIC_URL']+('/verify/' if purpose=='verify' else '/reset/')+token
    subject='Verify your members portal email' if purpose=='verify' else 'Reset your members portal password'
    send_mail(member.email,subject,f'{subject}\n\nOpen this link within 30 minutes:\n{link}\n\nIf you did not request this, ignore this email.\nStreamingHome', heading='Welcome home.' if purpose=='verify' else 'Reset your password.', intro='Confirm your email address to continue setting up your members portal account.' if purpose=='verify' else 'We received a request to reset your members portal password.', action_url=link, action_label='Verify email address' if purpose=='verify' else 'Reset password', note='This link expires in 30 minutes. If you did not request this email, you can ignore it.')
