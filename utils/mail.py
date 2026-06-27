"""
Helpers de envío de correo.
Soporta SMTP (Gmail), Brevo y Resend con fallback automático.
"""
import json
import smtplib
import traceback
from email.message import EmailMessage
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from flask import current_app, render_template, url_for

from utils.security import serializer, SALT_VERIFICACION, SALT_RESET


# ──────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────

def _mail_sender() -> str:
    return (
        current_app.config.get('MAIL_DEFAULT_SENDER')
        or current_app.config.get('MAIL_USERNAME', '')
    )


def _send_smtp_email(subject: str, recipient: str, html: str) -> None:
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From']    = _mail_sender()
    msg['To']      = recipient
    msg.set_content('Abre este correo en un cliente compatible con HTML.')
    msg.add_alternative(html, subtype='html')

    server   = current_app.config['MAIL_SERVER']
    port     = current_app.config['MAIL_PORT']
    use_tls  = current_app.config['MAIL_USE_TLS']
    use_ssl  = current_app.config['MAIL_USE_SSL']
    timeout  = current_app.config.get('MAIL_TIMEOUT', 10)
    attempts = [(server, port, use_tls, use_ssl)]

    if server == 'smtp.gmail.com' and port == 587 and use_tls and not use_ssl:
        attempts.append((server, 465, False, True))

    errors = []
    for smtp_server, smtp_port, smtp_tls, smtp_ssl in attempts:
        try:
            if smtp_ssl:
                host = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=timeout)
            else:
                host = smtplib.SMTP(smtp_server, smtp_port, timeout=timeout)

            with host:
                if smtp_tls and not smtp_ssl:
                    host.starttls()
                host.login(
                    current_app.config['MAIL_USERNAME'],
                    current_app.config['MAIL_PASSWORD'],
                )
                host.send_message(msg)
                return
        except Exception as e:
            errors.append(f"{smtp_server}:{smtp_port} tls={smtp_tls} ssl={smtp_ssl}: {type(e).__name__}: {e}")

    raise RuntimeError('No se pudo enviar correo por SMTP. Intentos: ' + ' | '.join(errors))


def _send_resend_email(subject: str, recipient: str, html: str) -> None:
    payload = json.dumps({
        'from':    current_app.config['RESEND_FROM'],
        'to':      [recipient],
        'subject': subject,
        'html':    html,
    }).encode('utf-8')
    req = urlrequest.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f"Bearer {current_app.config['RESEND_API_KEY']}",
            'Content-Type':  'application/json',
            'User-Agent':    'TicketIA/1.0',
        },
        method='POST',
    )
    try:
        with urlrequest.urlopen(req, timeout=15) as response:
            if response.status >= 400:
                raise RuntimeError(f'Resend error HTTP {response.status}')
    except HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Resend error HTTP {e.code}: {detail}') from e
    except URLError as e:
        raise RuntimeError(f'Resend connection error: {e.reason}') from e


def _send_brevo_email(subject: str, recipient: str, html: str) -> None:
    from_email = current_app.config.get('BREVO_FROM_EMAIL')
    if not from_email:
        raise RuntimeError('Falta BREVO_FROM_EMAIL o MAIL_USERNAME para enviar con Brevo')

    payload = json.dumps({
        'sender': {
            'name':  current_app.config.get('BREVO_FROM_NAME', 'TicketIA Bellavista'),
            'email': from_email,
        },
        'to':          [{'email': recipient}],
        'subject':     subject,
        'htmlContent': html,
    }).encode('utf-8')
    req = urlrequest.Request(
        'https://api.brevo.com/v3/smtp/email',
        data=payload,
        headers={
            'api-key':      current_app.config['BREVO_API_KEY'],
            'Accept':       'application/json',
            'Content-Type': 'application/json',
            'User-Agent':   'TicketIA/1.0',
        },
        method='POST',
    )
    try:
        with urlrequest.urlopen(req, timeout=15) as response:
            if response.status >= 400:
                raise RuntimeError(f'Brevo error HTTP {response.status}')
    except HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Brevo error HTTP {e.code}: {detail}') from e
    except URLError as e:
        raise RuntimeError(f'Brevo connection error: {e.reason}') from e


def send_email(subject: str, recipient: str, html: str) -> None:
    """
    Envía un correo HTML con fallback automático entre proveedores:
    SMTP → Brevo → Resend según lo configurado en MAIL_PROVIDER y variables de entorno.
    """
    import os
    provider = current_app.config.get('MAIL_PROVIDER', '').strip().lower()

    if provider == 'brevo':
        _send_brevo_email(subject, recipient, html)
        return
    if provider == 'resend':
        _send_resend_email(subject, recipient, html)
        return

    on_render = bool(os.environ.get('RENDER'))

    if on_render and current_app.config.get('BREVO_API_KEY'):
        _send_brevo_email(subject, recipient, html)
        return
    if on_render and current_app.config.get('RESEND_API_KEY'):
        _send_resend_email(subject, recipient, html)
        return

    if current_app.config.get('MAIL_USERNAME') and current_app.config.get('MAIL_PASSWORD'):
        try:
            _send_smtp_email(subject, recipient, html)
            return
        except Exception as e:
            if current_app.config.get('BREVO_API_KEY'):
                current_app.logger.warning('SMTP falló, intentando Brevo: %s', e)
                _send_brevo_email(subject, recipient, html)
                return
            if current_app.config.get('RESEND_API_KEY'):
                current_app.logger.warning('SMTP falló, intentando Resend: %s', e)
                _send_resend_email(subject, recipient, html)
                return
            raise

    if current_app.config.get('BREVO_API_KEY'):
        _send_brevo_email(subject, recipient, html)
        return
    if current_app.config.get('RESEND_API_KEY'):
        _send_resend_email(subject, recipient, html)
        return

    raise RuntimeError('No hay configuración de correo disponible')


# ──────────────────────────────────────────────────────────────
# Emails de aplicación
# ──────────────────────────────────────────────────────────────

def send_verification_email(usuario) -> None:
    token = serializer().dumps(usuario.email, salt=SALT_VERIFICACION)
    link  = url_for('auth.verificar_email', token=token, _external=True)
    html  = render_template('emails/verificar_cuenta.html',
                            nombre=usuario.nombre, link=link)
    send_email('Confirma tu cuenta — TicketIA Bellavista', usuario.email, html)


def send_reset_email(usuario) -> None:
    token = serializer().dumps(usuario.email, salt=SALT_RESET)
    link  = url_for('auth.reset_password', token=token, _external=True)
    html  = render_template('emails/reset_password.html',
                            nombre=usuario.nombre, link=link)
    send_email('Recupera tu contraseña — TicketIA Bellavista', usuario.email, html)
