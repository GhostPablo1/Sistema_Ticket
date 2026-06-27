import os
import sys
import sqlite3
import json
import smtplib
import traceback
from email.message import EmailMessage
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from database import db, Usuario, Ticket, MensajeIA, MensajeTicket, Notificacion
from ai_assistant import get_ai_response
from datetime import datetime
from sqlalchemy import inspect, text, func
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    import secrets as _secrets
    _secret_key = _secrets.token_hex(32)
    print("⚠️  SECRET_KEY no configurada en .env — usando clave efímera (las sesiones no sobrevivirán reinicios).", file=sys.stderr)
app.config['SECRET_KEY'] = _secret_key

# ============================================================
# CONFIGURACIÓN DE BASE DE DATOS
# ============================================================
def get_valid_database_path():
    candidate_paths = [
        r"C:\tickets.db",
        os.path.join(os.environ.get('USERPROFILE', r'C:\Users\Pablo'), 'tickets.db'),
        os.path.abspath('tickets.db'),
    ]
    for path in candidate_paths:
        try:
            test_conn = sqlite3.connect(path)
            test_conn.close()
            print(f"✅ Base de datos válida en: {path}", file=sys.stderr)
            return path
        except Exception as e:
            print(f"❌ No se pudo usar {path}: {e}", file=sys.stderr)
            continue
    print("ERROR CRÍTICO: No se pudo encontrar una ubicación para la base de datos.", file=sys.stderr)
    sys.exit(1)


def _normalize_database_uri(database_url):
    if database_url.startswith('postgres://'):
        return 'postgresql+psycopg://' + database_url[len('postgres://'):]
    if database_url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + database_url[len('postgresql://'):]
    return database_url


def _safe_db_uri_for_log(uri):
    if '://' not in uri or '@' not in uri:
        return uri

    scheme, rest = uri.split('://', 1)
    host_part = rest.split('@', 1)[1]
    return f'{scheme}://***:***@{host_part}'


database_url = os.environ.get('DATABASE_URL')
if database_url:
    db_uri = _normalize_database_uri(database_url)
else:
    db_path = get_valid_database_path()
    if os.environ.get('RENDER') or not db_path.startswith(('C:', 'D:')):
        db_uri = 'sqlite:///test.db'
    else:
        db_uri = 'sqlite:///' + db_path.replace('\\', '/')

app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
print(f"📁 URI final: {_safe_db_uri_for_log(db_uri)}", file=sys.stderr)

db.init_app(app)


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')

# ============================================================
# CONFIGURACIÓN DE CORREO
# ============================================================
app.config['MAIL_SERVER']         = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT']           = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS']        = _env_bool('MAIL_USE_TLS', True)
app.config['MAIL_USE_SSL']        = _env_bool('MAIL_USE_SSL', False)
app.config['MAIL_USERNAME']       = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD']       = os.environ.get('MAIL_PASSWORD', '').replace(' ', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER') or app.config['MAIL_USERNAME']
app.config['MAIL_TIMEOUT']        = int(os.environ.get('MAIL_TIMEOUT', 10))
app.config['MAIL_PROVIDER']       = os.environ.get('MAIL_PROVIDER', '').strip().lower()
app.config['RESEND_API_KEY']      = os.environ.get('RESEND_API_KEY')
app.config['RESEND_FROM']         = os.environ.get('RESEND_FROM', 'TicketIA Bellavista <onboarding@resend.dev>')
app.config['BREVO_API_KEY']       = os.environ.get('BREVO_API_KEY')
app.config['BREVO_FROM_EMAIL']    = os.environ.get('BREVO_FROM_EMAIL') or app.config['MAIL_USERNAME']
app.config['BREVO_FROM_NAME']     = os.environ.get('BREVO_FROM_NAME', 'TicketIA Bellavista')

mail = Mail(app)
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

SALT_VERIFICACION = 'email-verify-2026'
SALT_RESET        = 'password-reset-2026'
ADMIN_ROLE = 'admin'
TECH_ROLE = 'tecnico'
USER_ROLE = 'usuario'
STAFF_ROLES = {ADMIN_ROLE, TECH_ROLE}

# ==================== LOGIN MANAGER ====================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Debes iniciar sesión para acceder.'
login_manager.login_message_category = 'warning'

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# ==================== INIT DB ====================
with app.app_context():
    db.create_all()
    inspector = inspect(db.engine)
    usuario_columns = {column['name'] for column in inspector.get_columns('usuarios')}
    ticket_columns = {column['name'] for column in inspector.get_columns('tickets')}
    default_true = '1' if db.engine.dialect.name == 'sqlite' else 'TRUE'
    default_false = '0' if db.engine.dialect.name == 'sqlite' else 'FALSE'
    added_email_verificado = False

    # Migración: columna activo
    if 'activo' not in usuario_columns:
        with db.engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE usuarios ADD COLUMN activo BOOLEAN DEFAULT {default_true}"))
            conn.commit()

    # Migración: columna email_verificado
    if 'email_verificado' not in usuario_columns:
        with db.engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE usuarios ADD COLUMN email_verificado BOOLEAN DEFAULT {default_false}"))
            conn.commit()
        added_email_verificado = True

    if 'informe_cierre' not in ticket_columns:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE tickets ADD COLUMN informe_cierre TEXT"))
            conn.commit()

    if 'informe_fecha' not in ticket_columns:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE tickets ADD COLUMN informe_fecha TIMESTAMP"))
            conn.commit()

    if added_email_verificado:
        # Solo las cuentas antiguas, creadas antes de existir la verificacion, quedan verificadas.
        Usuario.query.filter(
            (Usuario.email_verificado.is_(None)) | (Usuario.email_verificado == False)
        ).update({Usuario.email_verificado: True}, synchronize_session=False)
        db.session.commit()

    admin_demo = Usuario.query.filter_by(email='tecnico@bellavista.gob.pe').first()
    if admin_demo:
        admin_demo.rol = ADMIN_ROLE
        admin_demo.area = admin_demo.area or 'Informática'
        admin_demo.email_verificado = True
        # Migrar a hash si aún tiene contraseña en texto plano
        if not (admin_demo.password.startswith('pbkdf2:') or admin_demo.password.startswith('scrypt:')):
            admin_demo.set_password(admin_demo.password)
    else:
        admin_demo = Usuario(
            nombre='Administrador TI',
            email='tecnico@bellavista.gob.pe',
            rol=ADMIN_ROLE,
            area='Informática',
            activo=True,
            email_verificado=True,
        )
        admin_demo.set_password('tecnico0123')
        db.session.add(admin_demo)

    usuario_demo = Usuario.query.filter_by(email='usuario@bellavista.gob.pe').first()
    if not usuario_demo:
        usuario_demo = Usuario(
            nombre='Usuario Demo',
            email='usuario@bellavista.gob.pe',
            rol=USER_ROLE,
            area='Administración',
            activo=True,
            email_verificado=True,
        )
        usuario_demo.set_password('usuario123')
        db.session.add(usuario_demo)
    elif not (usuario_demo.password.startswith('pbkdf2:') or usuario_demo.password.startswith('scrypt:')):
        usuario_demo.set_password(usuario_demo.password)

    db.session.commit()
    print("✅ Base de datos lista.", file=sys.stderr)


# ============================================================
# HELPERS DE CORREO
# ============================================================
def _mail_sender():
    return app.config['MAIL_DEFAULT_SENDER'] or app.config['MAIL_USERNAME']


def _send_smtp_email(subject, recipient, html):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = _mail_sender()
    msg['To'] = recipient
    msg.set_content('Abre este correo en un cliente compatible con HTML.')
    msg.add_alternative(html, subtype='html')

    server = app.config['MAIL_SERVER']
    port = app.config['MAIL_PORT']
    use_tls = app.config['MAIL_USE_TLS']
    use_ssl = app.config['MAIL_USE_SSL']
    attempts = [(server, port, use_tls, use_ssl)]

    if server == 'smtp.gmail.com' and port == 587 and use_tls and not use_ssl:
        attempts.append((server, 465, False, True))

    errors = []
    timeout = app.config['MAIL_TIMEOUT']
    for smtp_server, smtp_port, smtp_tls, smtp_ssl in attempts:
        try:
            if smtp_ssl:
                host = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=timeout)
            else:
                host = smtplib.SMTP(smtp_server, smtp_port, timeout=timeout)

            with host:
                if smtp_tls and not smtp_ssl:
                    host.starttls()
                host.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
                host.send_message(msg)
                return
        except Exception as e:
            errors.append(f"{smtp_server}:{smtp_port} tls={smtp_tls} ssl={smtp_ssl}: {type(e).__name__}: {e}")

    raise RuntimeError('No se pudo enviar correo por SMTP. Intentos: ' + ' | '.join(errors))


def _log_mail_error(action, error):
    print(f"Error {action}: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)


def _send_resend_email(subject, recipient, html):
    payload = json.dumps({
        'from': app.config['RESEND_FROM'],
        'to': [recipient],
        'subject': subject,
        'html': html,
    }).encode('utf-8')
    req = urlrequest.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f"Bearer {app.config['RESEND_API_KEY']}",
            'Content-Type': 'application/json',
            'User-Agent': 'TicketIA/1.0',
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


def _send_brevo_email(subject, recipient, html):
    if not app.config['BREVO_FROM_EMAIL']:
        raise RuntimeError('Falta BREVO_FROM_EMAIL o MAIL_USERNAME para enviar con Brevo')

    payload = json.dumps({
        'sender': {
            'name': app.config['BREVO_FROM_NAME'],
            'email': app.config['BREVO_FROM_EMAIL'],
        },
        'to': [{'email': recipient}],
        'subject': subject,
        'htmlContent': html,
    }).encode('utf-8')
    req = urlrequest.Request(
        'https://api.brevo.com/v3/smtp/email',
        data=payload,
        headers={
            'api-key': app.config['BREVO_API_KEY'],
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'TicketIA/1.0',
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


def _send_email(subject, recipient, html):
    provider = app.config['MAIL_PROVIDER']

    if provider == 'brevo':
        _send_brevo_email(subject, recipient, html)
        return
    if provider == 'resend':
        _send_resend_email(subject, recipient, html)
        return

    if os.environ.get('RENDER') and app.config['BREVO_API_KEY']:
        _send_brevo_email(subject, recipient, html)
        return
    if os.environ.get('RENDER') and app.config['RESEND_API_KEY']:
        _send_resend_email(subject, recipient, html)
        return

    if app.config['MAIL_USERNAME'] and app.config['MAIL_PASSWORD']:
        try:
            _send_smtp_email(subject, recipient, html)
            return
        except Exception as e:
            if app.config['BREVO_API_KEY']:
                _log_mail_error('SMTP, intentando Brevo', e)
                _send_brevo_email(subject, recipient, html)
                return
            if app.config['RESEND_API_KEY']:
                _log_mail_error('SMTP, intentando Resend', e)
                _send_resend_email(subject, recipient, html)
                return
            raise

    if app.config['BREVO_API_KEY']:
        _send_brevo_email(subject, recipient, html)
        return

    if app.config['RESEND_API_KEY']:
        _send_resend_email(subject, recipient, html)
        return

    raise RuntimeError('No hay configuracion de correo disponible')


def _send_verification_email(usuario):
    token = serializer.dumps(usuario.email, salt=SALT_VERIFICACION)
    link  = url_for('verificar_email', token=token, _external=True)
    html = render_template('emails/verificar_cuenta.html',
                           nombre=usuario.nombre, link=link)
    _send_email('Confirma tu cuenta — TicketIA Bellavista', usuario.email, html)


def _send_reset_email(usuario):
    token = serializer.dumps(usuario.email, salt=SALT_RESET)
    link  = url_for('reset_password', token=token, _external=True)
    html = render_template('emails/reset_password.html',
                           nombre=usuario.nombre, link=link)
    _send_email('Recupera tu contraseña — TicketIA Bellavista', usuario.email, html)


def _is_admin(user=None):
    user = user or current_user
    return user.is_authenticated and user.rol == ADMIN_ROLE


def _is_staff(user=None):
    user = user or current_user
    return user.is_authenticated and user.rol in STAFF_ROLES


def _is_assigned_tech(ticket, user=None):
    user = user or current_user
    return user.is_authenticated and user.rol == TECH_ROLE and ticket.tecnico_id == user.id


def _can_view_ticket(ticket):
    return (
        _is_admin()
        or ticket.usuario_id == current_user.id
        or _is_assigned_tech(ticket)
    )


def _can_manage_ticket(ticket):
    return _is_admin() or _is_assigned_tech(ticket)


def _ticket_query_for_current_user():
    if _is_admin():
        return Ticket.query
    if current_user.rol == TECH_ROLE:
        return Ticket.query.filter_by(tecnico_id=current_user.id)
    return Ticket.query.filter_by(usuario_id=current_user.id)


def _tecnicos_activos():
    return Usuario.query.filter_by(rol=TECH_ROLE, activo=True).order_by(Usuario.nombre.asc()).all()


def _admin_users():
    return Usuario.query.filter_by(rol=ADMIN_ROLE, activo=True).all()


def _notify_user(usuario_id, titulo, mensaje, ticket_id=None):
    if not usuario_id:
        return
    db.session.add(Notificacion(
        usuario_id=usuario_id,
        ticket_id=ticket_id,
        titulo=titulo,
        mensaje=mensaje,
        leida=False,
    ))


def _notify_admins(titulo, mensaje, ticket_id=None, exclude_user_id=None):
    for admin in _admin_users():
        if admin.id != exclude_user_id:
            _notify_user(admin.id, titulo, mensaje, ticket_id)


def _notification_json(notificacion):
    return {
        'id': notificacion.id,
        'titulo': notificacion.titulo,
        'mensaje': notificacion.mensaje,
        'leida': notificacion.leida,
        'fecha': notificacion.fecha.strftime('%d/%m/%Y %H:%M') if notificacion.fecha else '',
        'url': url_for('ver_ticket', ticket_id=notificacion.ticket_id) if notificacion.ticket_id else '',
    }


def _mensaje_ticket_json(mensaje):
    autor = mensaje.usuario
    return {
        'id': mensaje.id,
        'mensaje': mensaje.mensaje,
        'autor': autor.nombre if autor else 'Usuario',
        'autor_rol': autor.rol if autor else '',
        'propio': mensaje.usuario_id == current_user.id,
        'fecha': mensaje.fecha.strftime('%d/%m/%Y %H:%M') if mensaje.fecha else '',
    }


def _build_ticket_report(ticket):
    mensajes = MensajeTicket.query.filter_by(ticket_id=ticket.id).order_by(MensajeTicket.fecha.asc()).all()
    historial = []
    for mensaje in mensajes:
        autor = mensaje.usuario.nombre if mensaje.usuario else 'Usuario'
        fecha = mensaje.fecha.strftime('%d/%m/%Y %H:%M') if mensaje.fecha else ''
        historial.append(f"- {fecha} | {autor}: {mensaje.mensaje}")

    historial_texto = '\n'.join(historial) if historial else 'No hubo mensajes entre usuario y soporte.'
    tecnico = ticket.tecnico.nombre if ticket.tecnico else 'Sin tecnico asignado'
    fecha_creacion = ticket.fecha_creacion.strftime('%d/%m/%Y %H:%M') if ticket.fecha_creacion else 'Sin fecha'
    fecha_cierre = ticket.fecha_resolucion.strftime('%d/%m/%Y %H:%M') if ticket.fecha_resolucion else datetime.utcnow().strftime('%d/%m/%Y %H:%M')

    return (
        f"INFORME DE CIERRE - TICKET #{ticket.id}\n\n"
        f"Solicitante: {ticket.creador.nombre if ticket.creador else 'No disponible'}\n"
        f"Area solicitante: {ticket.creador.area if ticket.creador and ticket.creador.area else 'No registrada'}\n"
        f"Tecnico responsable: {tecnico}\n"
        f"Categoria: {ticket.categoria or 'No registrada'}\n"
        f"Prioridad: {ticket.prioridad or 'No registrada'}\n"
        f"Estado final: {ticket.estado}\n"
        f"Fecha de creacion: {fecha_creacion}\n"
        f"Fecha de cierre: {fecha_cierre}\n\n"
        f"Problema reportado:\n{ticket.descripcion or 'Sin descripcion'}\n\n"
        f"Historial de comunicacion:\n{historial_texto}\n\n"
        f"Conclusion:\nEl ticket fue marcado como {ticket.estado}. Este informe fue generado automaticamente por TicketIA."
    )


def _ensure_ticket_report(ticket, force=False):
    if ticket.estado not in ['Resuelto', 'Cerrado']:
        return
    if ticket.informe_cierre and not force:
        return
    ticket.informe_cierre = _build_ticket_report(ticket)
    ticket.informe_fecha = datetime.utcnow()


def _set_ticket_status(ticket, nuevo_estado):
    estado_anterior = ticket.estado
    ticket.estado = nuevo_estado
    if nuevo_estado in ['Resuelto', 'Cerrado']:
        ticket.fecha_resolucion = ticket.fecha_resolucion or datetime.utcnow()
        _ensure_ticket_report(ticket, force=(nuevo_estado == 'Cerrado'))
    elif estado_anterior in ['Resuelto', 'Cerrado']:
        ticket.fecha_resolucion = None

    if estado_anterior != nuevo_estado:
        _notify_user(
            ticket.usuario_id,
            'Estado actualizado',
            f'Tu ticket #{ticket.id} cambio a {nuevo_estado}.',
            ticket.id,
        )
        if ticket.tecnico_id and ticket.tecnico_id != current_user.id:
            _notify_user(
                ticket.tecnico_id,
                'Estado actualizado',
                f'El ticket #{ticket.id} cambio a {nuevo_estado}.',
                ticket.id,
            )
        if not _is_admin():
            _notify_admins(
                'Estado actualizado',
                f'El ticket #{ticket.id} cambio a {nuevo_estado}.',
                ticket.id,
                exclude_user_id=current_user.id,
            )


# ==================== RUTAS PÚBLICAS ====================
def _ticket_json(ticket):
    creador = ticket.creador
    tecnico = ticket.tecnico
    creador_nombre = creador.nombre if creador else ''
    tecnico_nombre = tecnico.nombre if tecnico else ''

    return {
        'id': ticket.id,
        'titulo': ticket.titulo,
        'descripcion': ticket.descripcion or '',
        'categoria': ticket.categoria or '',
        'estado': ticket.estado,
        'prioridad': ticket.prioridad,
        'fecha': ticket.fecha_creacion.strftime('%d/%m/%Y') if ticket.fecha_creacion else '',
        'hora': ticket.fecha_creacion.strftime('%H:%M') if ticket.fecha_creacion else '',
        'fecha_resolucion': ticket.fecha_resolucion.strftime('%d/%m/%Y') if ticket.fecha_resolucion else '',
        'creador_nombre': creador_nombre,
        'creador_inicial': creador_nombre[:1].upper(),
        'creador_area': creador.area if creador else '',
        'tecnico_id': ticket.tecnico_id,
        'tecnico_nombre': tecnico_nombre,
        'tecnico_area': tecnico.area if tecnico else '',
        'tecnico_email': tecnico.email if tecnico else '',
        'informe_fecha': ticket.informe_fecha.strftime('%d/%m/%Y %H:%M') if ticket.informe_fecha else '',
        'informe_cierre': ticket.informe_cierre or '',
        'tiene_informe': bool(ticket.informe_cierre),
        'url': url_for('ver_ticket', ticket_id=ticket.id),
        'assign_url': url_for('admin_asignar_ticket', ticket_id=ticket.id),
        'delete_url': url_for('admin_eliminar_ticket', ticket_id=ticket.id),
    }


def _usuario_json(usuario):
    rol_label = {
        ADMIN_ROLE: 'Administrador',
        TECH_ROLE: 'Tecnico',
        USER_ROLE: 'Usuario',
    }.get(usuario.rol, usuario.rol)
    return {
        'id': usuario.id,
        'nombre': usuario.nombre,
        'inicial': usuario.nombre[:1].upper(),
        'email': usuario.email,
        'rol': usuario.rol,
        'rol_label': rol_label,
        'area': usuario.area or '',
        'fecha_registro': usuario.fecha_registro.strftime('%d/%m/%Y') if usuario.fecha_registro else '',
        'make_tech_url': url_for('admin_hacer_tecnico', user_id=usuario.id),
        'remove_tech_url': url_for('admin_quitar_tecnico', user_id=usuario.id),
        'delete_url': url_for('admin_eliminar_usuario', user_id=usuario.id),
    }


@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.rol in STAFF_ROLES:
            return redirect(url_for('dashboard_tecnico'))
        return redirect(url_for('dashboard_usuario'))

    if request.method == 'POST':
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        user     = Usuario.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if not user.email_verificado:
                session['pending_email'] = email
                flash('Debes verificar tu correo electrónico antes de iniciar sesión.', 'warning')
                return redirect(url_for('verificar_pendiente'))
            db.session.commit()  # persiste migración de hash si aplicó
            login_user(user)
            if user.rol in STAFF_ROLES:
                return redirect(url_for('dashboard_tecnico'))
            return redirect(url_for('dashboard_usuario'))

        flash('Correo o contraseña incorrectos.', 'danger')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard_usuario'))

    if request.method == 'POST':
        nombre   = request.form['nombre'].strip()
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        confirm  = request.form['confirm_password']
        area     = request.form.get('area', '').strip()

        if not nombre or not email or not password:
            flash('Todos los campos son obligatorios.', 'danger')
            return render_template('register.html')
        if password != confirm:
            flash('Las contraseñas no coinciden.', 'danger')
            return render_template('register.html')
        if len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
            return render_template('register.html')
        if Usuario.query.filter_by(email=email).first():
            flash('Ya existe una cuenta con ese correo electrónico.', 'danger')
            return render_template('register.html')

        nuevo = Usuario(nombre=nombre, email=email,
                        rol=USER_ROLE, area=area, activo=True,
                        email_verificado=False)
        nuevo.set_password(password)
        db.session.add(nuevo)
        db.session.commit()

        session['pending_email'] = email
        try:
            _send_verification_email(nuevo)
        except Exception as e:
            _log_mail_error('enviando correo de verificacion', e)
            flash('Cuenta creada, pero no pudimos enviar el correo de verificación. '
                  'Usa reenviar cuando el correo este configurado.', 'warning')

        return redirect(url_for('verificar_pendiente'))

    return render_template('register.html')


@app.route('/verificar-pendiente')
def verificar_pendiente():
    email = session.get('pending_email', '')
    return render_template('verificar_pendiente.html', email=email)


@app.route('/verificar-email/<token>')
def verificar_email(token):
    try:
        email = serializer.loads(token, salt=SALT_VERIFICACION, max_age=86400)  # 24 h
    except SignatureExpired:
        flash('El enlace de verificación ha expirado. Solicita uno nuevo.', 'danger')
        return redirect(url_for('login'))
    except BadSignature:
        flash('Enlace de verificación inválido.', 'danger')
        return redirect(url_for('login'))

    user = Usuario.query.filter_by(email=email).first_or_404()
    if user.email_verificado:
        flash('Tu cuenta ya estapytrba verificada. ¡Puedes iniciar sesión!', 'info')
    else:
        user.email_verificado = True
        db.session.commit()
        flash('¡Correo verificado exitosamente! Ya puedes iniciar sesión.', 'success')

    return redirect(url_for('login'))


@app.route('/reenviar-verificacion', methods=['POST'])
def reenviar_verificacion():
    email = request.form.get('email', '').strip().lower()
    user  = Usuario.query.filter_by(email=email).first()

    if user and not user.email_verificado:
        try:
            _send_verification_email(user)
            flash('Correo de verificación reenviado. Revisa tu bandeja de entrada.', 'success')
        except Exception as e:
            _log_mail_error('reenviando correo de verificacion', e)
            flash('No se pudo reenviar el correo. Intenta más tarde.', 'danger')
    else:
        flash('Si el correo existe y no está verificado, recibirás el mensaje.', 'info')

    session['pending_email'] = email
    return redirect(url_for('verificar_pendiente'))


# ==================== RECUPERACIÓN DE CONTRASEÑA ====================
@app.route('/recuperar-password', methods=['GET', 'POST'])
def recuperar_password():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        user  = Usuario.query.filter_by(email=email).first()

        if user:
            try:
                _send_reset_email(user)
            except Exception as e:
                _log_mail_error('enviando correo de reset', e)

        # Mismo mensaje siempre para no revelar si el correo existe
        flash('Si el correo está registrado, recibirás un enlace para restablecer tu contraseña.', 'info')
        return redirect(url_for('login'))

    return render_template('recuperar_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt=SALT_RESET, max_age=1800)  # 30 min
    except SignatureExpired:
        flash('El enlace ha expirado. Solicita uno nuevo.', 'danger')
        return redirect(url_for('recuperar_password'))
    except BadSignature:
        flash('Enlace inválido.', 'danger')
        return redirect(url_for('recuperar_password'))

    user = Usuario.query.filter_by(email=email).first_or_404()

    if request.method == 'POST':
        nueva     = request.form['password']
        confirmar = request.form['confirm_password']

        if len(nueva) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
            return render_template('reset_password.html', token=token)
        if nueva != confirmar:
            flash('Las contraseñas no coinciden.', 'danger')
            return render_template('reset_password.html', token=token)

        user.set_password(nueva)
        db.session.commit()
        flash('Contraseña actualizada correctamente. Ya puedes iniciar sesión.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ==================== USUARIO ====================
@app.route('/dashboard/usuario')
@login_required
def dashboard_usuario():
    if current_user.rol != USER_ROLE:
        return redirect(url_for('dashboard_tecnico'))
    tickets    = Ticket.query.filter_by(usuario_id=current_user.id).order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = sum(1 for t in tickets if t.estado == 'Pendiente')
    en_proceso = sum(1 for t in tickets if t.estado == 'En Proceso')
    resueltos  = sum(1 for t in tickets if t.estado in ['Resuelto', 'Cerrado'])
    return render_template('dashboard_usuario.html', tickets=tickets,
                           pendientes=pendientes, en_proceso=en_proceso, resueltos=resueltos)


@app.route('/api/usuario/tickets')
@login_required
def api_usuario_tickets():
    if current_user.rol != USER_ROLE:
        return jsonify({'error': 'No autorizado'}), 403

    tickets = Ticket.query.filter_by(usuario_id=current_user.id).order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = sum(1 for t in tickets if t.estado == 'Pendiente')
    en_proceso = sum(1 for t in tickets if t.estado == 'En Proceso')
    resueltos = sum(1 for t in tickets if t.estado in ['Resuelto', 'Cerrado'])

    return jsonify({
        'tickets': [_ticket_json(ticket) for ticket in tickets],
        'stats': {
            'total': len(tickets),
            'pendientes': pendientes,
            'en_proceso': en_proceso,
            'resueltos': resueltos,
        }
    })


@app.route('/ticket/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_ticket():
    if current_user.rol != USER_ROLE:
        return redirect(url_for('dashboard_tecnico'))

    if request.method == 'POST':
        titulo      = request.form['titulo']
        descripcion = request.form['descripcion']
        categoria   = request.form['categoria']
        prioridad   = request.form.get('prioridad', 'Media')
        nuevo = Ticket(titulo=titulo, descripcion=descripcion, categoria=categoria,
                       prioridad=prioridad, usuario_id=current_user.id, estado='Pendiente')
        db.session.add(nuevo)
        db.session.commit()
        _notify_admins(
            'Nuevo ticket pendiente',
            f'{current_user.nombre} creo el ticket #{nuevo.id}: {nuevo.titulo}',
            nuevo.id,
        )
        db.session.commit()
        flash('Ticket creado exitosamente. El equipo de soporte lo atenderá pronto.', 'success')
        return redirect(url_for('dashboard_usuario'))
    return render_template('nuevo_ticket.html')


@app.route('/ticket/<int:ticket_id>')
@login_required
def ver_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not _can_view_ticket(ticket):
        flash('No tienes permiso para ver este ticket.', 'danger')
        return redirect(url_for('dashboard_tecnico') if _is_staff() else url_for('dashboard_usuario'))
    mensajes_ia = MensajeIA.query.filter_by(ticket_id=ticket.id).order_by(MensajeIA.timestamp).all()
    mensajes_ticket = MensajeTicket.query.filter_by(ticket_id=ticket.id).order_by(MensajeTicket.fecha.asc()).all()
    return render_template(
        'detalle_ticket.html',
        ticket=ticket,
        mensajes_ia=mensajes_ia,
        mensajes_ticket=mensajes_ticket,
        tecnicos=_tecnicos_activos(),
    )


@app.route('/api/ticket/<int:ticket_id>/resumen')
@login_required
def api_ticket_resumen(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not _can_view_ticket(ticket):
        return jsonify({'error': 'No autorizado'}), 403

    return jsonify({
        'ticket': _ticket_json(ticket),
        'mensajes_ia': MensajeIA.query.filter_by(ticket_id=ticket.id).count(),
    })


@app.route('/api/chat-ia', methods=['POST'])
@login_required
def chat_ia():
    data      = request.get_json()
    pregunta  = data.get('pregunta', '')
    ticket_id = data.get('ticket_id')
    respuesta = get_ai_response(pregunta, current_user.nombre)
    if ticket_id:
        ticket = Ticket.query.get(ticket_id)
        if ticket and _can_view_ticket(ticket):
            db.session.add(MensajeIA(ticket_id=ticket.id,
                                     usuario_pregunta=pregunta,
                                     ia_respuesta=respuesta))
            db.session.commit()
    return jsonify({'respuesta': respuesta})


@app.route('/ticket/<int:ticket_id>/cerrar')
@login_required
def cerrar_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.usuario_id == current_user.id or _can_manage_ticket(ticket):
        _set_ticket_status(ticket, 'Cerrado')
        db.session.commit()
        flash('Ticket cerrado correctamente.', 'success')
    return redirect(url_for('ver_ticket', ticket_id=ticket.id))


# ==================== TÉCNICO / ADMIN ====================
@app.route('/dashboard/tecnico')
@login_required
def dashboard_tecnico():
    if not _is_staff():
        return redirect(url_for('dashboard_usuario'))

    todos      = _ticket_query_for_current_user().order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = [t for t in todos if t.estado == 'Pendiente']
    en_proceso = [t for t in todos if t.estado == 'En Proceso']
    resueltos  = [t for t in todos if t.estado == 'Resuelto']
    cerrados   = [t for t in todos if t.estado == 'Cerrado']
    total_usuarios = Usuario.query.filter_by(rol=USER_ROLE).count()
    recientes  = todos[:10]

    return render_template('dashboard_tecnico.html',
                           todos_tickets=todos,
                           tickets_pendientes=pendientes,
                           tickets_en_proceso=en_proceso,
                           tickets_resueltos=resueltos,
                           tickets_cerrados=cerrados,
                           total_usuarios=total_usuarios,
                           recientes=recientes,
                           tecnicos=_tecnicos_activos())


@app.route('/api/tecnico/tickets')
@login_required
def api_tecnico_tickets():
    if not _is_staff():
        return jsonify({'error': 'No autorizado'}), 403

    todos = _ticket_query_for_current_user().order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = sum(1 for t in todos if t.estado == 'Pendiente')
    en_proceso = sum(1 for t in todos if t.estado == 'En Proceso')
    resueltos = sum(1 for t in todos if t.estado == 'Resuelto')
    cerrados = sum(1 for t in todos if t.estado == 'Cerrado')

    return jsonify({
        'tickets': [_ticket_json(ticket) for ticket in todos],
        'stats': {
            'total': len(todos),
            'pendientes': pendientes,
            'en_proceso': en_proceso,
            'resueltos': resueltos,
            'cerrados': cerrados,
        }
    })


@app.route('/admin/ticket/<int:ticket_id>/asignar', methods=['POST'])
@login_required
def admin_asignar_ticket(ticket_id):
    if not _is_admin():
        return redirect(url_for('dashboard_usuario'))

    ticket = Ticket.query.get_or_404(ticket_id)
    tecnico_id = request.form.get('tecnico_id', type=int)
    if not tecnico_id:
        ticket.tecnico_id = None
        if ticket.estado == 'En Proceso':
            ticket.estado = 'Pendiente'
        _notify_user(
            ticket.usuario_id,
            'Ticket sin tecnico asignado',
            f'Tu ticket #{ticket.id} quedo pendiente de reasignacion.',
            ticket.id,
        )
        db.session.commit()
        flash('Ticket dejado sin asignar.', 'success')
        return redirect(request.referrer or url_for('ver_ticket', ticket_id=ticket.id))

    tecnico = Usuario.query.filter_by(id=tecnico_id, rol=TECH_ROLE, activo=True).first()
    if not tecnico:
        flash('Selecciona un tecnico valido para asignar el ticket.', 'danger')
        return redirect(url_for('ver_ticket', ticket_id=ticket.id))

    ticket.tecnico_id = tecnico.id
    if ticket.estado == 'Pendiente':
        ticket.estado = 'En Proceso'
    _notify_user(
        tecnico.id,
        'Ticket asignado',
        f'Se te asigno el ticket #{ticket.id}: {ticket.titulo}',
        ticket.id,
    )
    _notify_user(
        ticket.usuario_id,
        'Tecnico asignado',
        f'{tecnico.nombre} fue asignado a tu ticket #{ticket.id}.',
        ticket.id,
    )
    db.session.commit()
    flash(f'Ticket asignado a {tecnico.nombre}.', 'success')
    return redirect(request.referrer or url_for('ver_ticket', ticket_id=ticket.id))


@app.route('/tecnico/ticket/<int:ticket_id>/asignar', methods=['POST'])
@login_required
def asignar_ticket(ticket_id):
    return redirect(url_for('ver_ticket', ticket_id=ticket_id))


@app.route('/tecnico/ticket/<int:ticket_id>/update', methods=['POST'])
@login_required
def update_ticket_estado(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not _can_manage_ticket(ticket):
        flash('No tienes permiso para modificar este ticket.', 'danger')
        return redirect(url_for('dashboard_tecnico') if _is_staff() else url_for('dashboard_usuario'))

    nuevo_estado = request.form.get('estado')
    if nuevo_estado in ['Pendiente', 'En Proceso', 'Resuelto', 'Cerrado']:
        _set_ticket_status(ticket, nuevo_estado)
        db.session.commit()
        flash('Estado del ticket actualizado.', 'success')
    return redirect(url_for('ver_ticket', ticket_id=ticket.id))


@app.route('/admin/ticket/<int:ticket_id>/eliminar', methods=['POST'])
@login_required
def admin_eliminar_ticket(ticket_id):
    if not _is_admin():
        return redirect(url_for('dashboard_usuario'))

    ticket = Ticket.query.get_or_404(ticket_id)
    titulo = ticket.titulo
    MensajeIA.query.filter_by(ticket_id=ticket.id).delete()
    MensajeTicket.query.filter_by(ticket_id=ticket.id).delete()
    Notificacion.query.filter_by(ticket_id=ticket.id).delete()
    db.session.delete(ticket)
    db.session.commit()
    flash(f'Ticket "{titulo}" eliminado.', 'success')
    return redirect(url_for('dashboard_tecnico'))


@app.route('/ticket/<int:ticket_id>/informe/regenerar', methods=['POST'])
@login_required
def regenerar_informe_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not _can_manage_ticket(ticket):
        flash('No tienes permiso para generar el informe.', 'danger')
        return redirect(url_for('ver_ticket', ticket_id=ticket.id))

    if ticket.estado not in ['Resuelto', 'Cerrado']:
        flash('El informe se genera cuando el ticket esta resuelto o cerrado.', 'warning')
        return redirect(url_for('ver_ticket', ticket_id=ticket.id))

    _ensure_ticket_report(ticket, force=True)
    db.session.commit()
    flash('Informe actualizado.', 'success')
    return redirect(url_for('ver_ticket', ticket_id=ticket.id))


@app.route('/api/ticket/<int:ticket_id>/mensajes', methods=['GET', 'POST'])
@login_required
def api_ticket_mensajes(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not _can_view_ticket(ticket):
        return jsonify({'error': 'No autorizado'}), 403

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        mensaje = (data.get('mensaje') or '').strip()
        if not mensaje:
            return jsonify({'error': 'El mensaje no puede estar vacio'}), 400

        nuevo = MensajeTicket(ticket_id=ticket.id, usuario_id=current_user.id, mensaje=mensaje)
        db.session.add(nuevo)

        if current_user.id == ticket.usuario_id:
            if ticket.tecnico_id:
                _notify_user(
                    ticket.tecnico_id,
                    'Nuevo mensaje del usuario',
                    f'{current_user.nombre} escribio en el ticket #{ticket.id}.',
                    ticket.id,
                )
            else:
                _notify_admins(
                    'Mensaje en ticket sin asignar',
                    f'{current_user.nombre} escribio en el ticket #{ticket.id}.',
                    ticket.id,
                )
        else:
            _notify_user(
                ticket.usuario_id,
                'Nuevo mensaje de soporte',
                f'{current_user.nombre} escribio en tu ticket #{ticket.id}.',
                ticket.id,
            )
            if _is_admin() and ticket.tecnico_id and ticket.tecnico_id != current_user.id:
                _notify_user(
                    ticket.tecnico_id,
                    'Nuevo mensaje del administrador',
                    f'{current_user.nombre} escribio en el ticket #{ticket.id}.',
                    ticket.id,
                )
            if _is_assigned_tech(ticket):
                _notify_admins(
                    'Soporte respondio un ticket',
                    f'{current_user.nombre} respondio el ticket #{ticket.id}.',
                    ticket.id,
                    exclude_user_id=current_user.id,
                )

        db.session.commit()
        return jsonify({'mensaje': _mensaje_ticket_json(nuevo)}), 201

    mensajes = MensajeTicket.query.filter_by(ticket_id=ticket.id).order_by(MensajeTicket.fecha.asc()).all()
    return jsonify({'mensajes': [_mensaje_ticket_json(mensaje) for mensaje in mensajes]})


@app.route('/api/notificaciones')
@login_required
def api_notificaciones():
    notificaciones = Notificacion.query.filter_by(usuario_id=current_user.id).order_by(Notificacion.fecha.desc()).limit(12).all()
    sin_leer = Notificacion.query.filter_by(usuario_id=current_user.id, leida=False).count()
    return jsonify({
        'sin_leer': sin_leer,
        'notificaciones': [_notification_json(notificacion) for notificacion in notificaciones],
    })


@app.route('/api/notificaciones/marcar-leidas', methods=['POST'])
@login_required
def api_notificaciones_marcar_leidas():
    Notificacion.query.filter_by(usuario_id=current_user.id, leida=False).update(
        {Notificacion.leida: True},
        synchronize_session=False,
    )
    db.session.commit()
    return jsonify({'ok': True})


# ==================== ADMIN USUARIOS ====================
@app.route('/admin/usuarios')
@login_required
def admin_usuarios():
    if not _is_admin():
        return redirect(url_for('dashboard_usuario'))
    usuarios = Usuario.query.order_by(Usuario.fecha_registro.desc()).all()
    return render_template('admin_usuarios.html', usuarios=usuarios)


@app.route('/api/admin/usuarios')
@login_required
def api_admin_usuarios():
    if not _is_admin():
        return jsonify({'error': 'No autorizado'}), 403

    usuarios = Usuario.query.order_by(Usuario.fecha_registro.desc()).all()
    usuarios_rol = sum(1 for u in usuarios if u.rol == USER_ROLE)
    tecnicos_rol = sum(1 for u in usuarios if u.rol == TECH_ROLE)
    admins_rol = sum(1 for u in usuarios if u.rol == ADMIN_ROLE)

    return jsonify({
        'usuarios': [_usuario_json(usuario) for usuario in usuarios],
        'stats': {
            'total': len(usuarios),
            'usuarios': usuarios_rol,
            'tecnicos': tecnicos_rol,
            'admins': admins_rol,
        }
    })


@app.route('/admin/usuario/crear', methods=['POST'])
@login_required
def admin_crear_usuario():
    if not _is_admin():
        return redirect(url_for('dashboard_usuario'))
    flash('Los usuarios deben registrarse desde el formulario publico. Luego puedes usar "Hacer tecnico".', 'warning')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:user_id>/hacer-tecnico', methods=['POST'])
@login_required
def admin_hacer_tecnico(user_id):
    if not _is_admin():
        return redirect(url_for('dashboard_usuario'))

    usuario = Usuario.query.get_or_404(user_id)
    if usuario.rol == ADMIN_ROLE:
        flash('No se puede cambiar el rol de un administrador desde esta accion.', 'danger')
        return redirect(url_for('admin_usuarios'))

    usuario.rol = TECH_ROLE
    usuario.activo = True
    usuario.email_verificado = True
    _notify_user(
        usuario.id,
        'Ahora eres tecnico de soporte',
        'El administrador te habilito como tecnico. Ya puedes atender tickets asignados.',
    )
    db.session.commit()
    flash(f'{usuario.nombre} ahora es tecnico de soporte.', 'success')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:user_id>/quitar-tecnico', methods=['POST'])
@login_required
def admin_quitar_tecnico(user_id):
    if not _is_admin():
        return redirect(url_for('dashboard_usuario'))

    usuario = Usuario.query.get_or_404(user_id)
    if usuario.rol != TECH_ROLE:
        flash('Ese usuario no es tecnico.', 'warning')
        return redirect(url_for('admin_usuarios'))

    tickets_abiertos = Ticket.query.filter(
        Ticket.tecnico_id == usuario.id,
        Ticket.estado.in_(['Pendiente', 'En Proceso'])
    ).all()
    for ticket in tickets_abiertos:
        ticket.tecnico_id = None
        ticket.estado = 'Pendiente'
        _notify_user(
            ticket.usuario_id,
            'Ticket pendiente de reasignacion',
            f'Tu ticket #{ticket.id} quedo pendiente de un nuevo tecnico.',
            ticket.id,
        )

    usuario.rol = USER_ROLE
    _notify_user(
        usuario.id,
        'Rol de tecnico retirado',
        'El administrador retiro tu rol de tecnico de soporte.',
    )
    db.session.commit()
    flash(f'{usuario.nombre} volvio a rol usuario.', 'success')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:user_id>/eliminar', methods=['POST'])
@login_required
def admin_eliminar_usuario(user_id):
    if not _is_admin():
        return redirect(url_for('dashboard_usuario'))
    usuario = Usuario.query.get_or_404(user_id)
    if usuario.id == current_user.id:
        flash('No puedes eliminar tu propia cuenta.', 'danger')
        return redirect(url_for('admin_usuarios'))
    nombre = usuario.nombre
    db.session.delete(usuario)
    db.session.commit()
    flash(f'Usuario "{nombre}" eliminado.', 'success')
    return redirect(url_for('admin_usuarios'))


# ==================== ESTADÍSTICAS ====================
@app.route('/reportes/estadisticas')
@login_required
def estadisticas():
    if not _is_admin():
        return redirect(url_for('dashboard_usuario'))
    tickets_por_categoria = [(cat, cnt) for cat, cnt in db.session.query(Ticket.categoria, func.count(Ticket.id)).group_by(Ticket.categoria).all()]
    tickets_por_estado    = [(est, cnt) for est, cnt in db.session.query(Ticket.estado, func.count(Ticket.id)).group_by(Ticket.estado).all()]
    tickets_por_prioridad = [(pri, cnt) for pri, cnt in db.session.query(Ticket.prioridad, func.count(Ticket.id)).group_by(Ticket.prioridad).all()]
    total = Ticket.query.count()
    return render_template('estadisticas.html',
                           por_categoria=tickets_por_categoria,
                           por_estado=tickets_por_estado,
                           por_prioridad=tickets_por_prioridad,
                           total=total)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
