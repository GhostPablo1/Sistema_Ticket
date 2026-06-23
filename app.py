import os
import sys
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from database import db, Usuario, Ticket, MensajeIA
from ai_assistant import get_ai_response
from datetime import datetime
from sqlalchemy import text, func
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'clave-secreta-ticketia-2026'

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

db_path = get_valid_database_path()
db_uri = 'sqlite:///' + db_path.replace('\\', '/')
app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
print(f"📁 URI final: {db_uri}", file=sys.stderr)

db.init_app(app)

# ============================================================
# CONFIGURACIÓN DE CORREO
# ============================================================
app.config['MAIL_SERVER']         = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT']           = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS']        = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME']       = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD']       = os.environ.get('MAIL_PASSWORD', '').replace(' ', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')

mail = Mail(app)
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

SALT_VERIFICACION = 'email-verify-2026'
SALT_RESET        = 'password-reset-2026'

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

    # Migración: columna activo
    try:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN activo BOOLEAN DEFAULT 1"))
            conn.commit()
    except Exception:
        pass

    # Migración: columna email_verificado
    try:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN email_verificado BOOLEAN DEFAULT 0"))
            conn.commit()
    except Exception:
        pass

    # Los usuarios ya existentes quedan como verificados automáticamente
    with db.engine.connect() as conn:
        conn.execute(text(
            "UPDATE usuarios SET email_verificado = 1 WHERE email_verificado IS NULL OR email_verificado = 0"
        ))
        conn.commit()

    if not Usuario.query.filter_by(email='tecnico@bellavista.gob.pe').first():
        db.session.add(Usuario(
            nombre='Administrador TI',
            email='tecnico@bellavista.gob.pe',
            password='tecnico0123',
            rol='tecnico',
            area='Informática',
            activo=True,
            email_verificado=True,
        ))

    if not Usuario.query.filter_by(email='usuario@bellavista.gob.pe').first():
        db.session.add(Usuario(
            nombre='Usuario Demo',
            email='usuario@bellavista.gob.pe',
            password='usuario123',
            rol='usuario',
            area='Administración',
            activo=True,
            email_verificado=True,
        ))

    db.session.commit()
    print("✅ Base de datos lista.", file=sys.stderr)


# ============================================================
# HELPERS DE CORREO
# ============================================================
def _send_verification_email(usuario):
    token = serializer.dumps(usuario.email, salt=SALT_VERIFICACION)
    link  = url_for('verificar_email', token=token, _external=True)
    msg   = Message('Confirma tu cuenta — TicketIA Bellavista', recipients=[usuario.email])
    msg.html = render_template('emails/verificar_cuenta.html',
                               nombre=usuario.nombre, link=link)
    mail.send(msg)


def _send_reset_email(usuario):
    token = serializer.dumps(usuario.email, salt=SALT_RESET)
    link  = url_for('reset_password', token=token, _external=True)
    msg   = Message('Recupera tu contraseña — TicketIA Bellavista', recipients=[usuario.email])
    msg.html = render_template('emails/reset_password.html',
                               nombre=usuario.nombre, link=link)
    mail.send(msg)


# ==================== RUTAS PÚBLICAS ====================
@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.rol == 'tecnico':
            return redirect(url_for('dashboard_tecnico'))
        return redirect(url_for('dashboard_usuario'))

    if request.method == 'POST':
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        user     = Usuario.query.filter_by(email=email).first()

        if user and user.password == password:
            if not user.email_verificado:
                session['pending_email'] = email
                flash('Debes verificar tu correo electrónico antes de iniciar sesión.', 'warning')
                return redirect(url_for('verificar_pendiente'))
            login_user(user)
            if user.rol == 'tecnico':
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

        nuevo = Usuario(nombre=nombre, email=email, password=password,
                        rol='usuario', area=area, activo=True,
                        email_verificado=False)
        db.session.add(nuevo)
        db.session.commit()

        try:
            _send_verification_email(nuevo)
        except Exception as e:
            print(f"❌ Error enviando correo: {e}", file=sys.stderr)
            flash('Cuenta creada, pero no pudimos enviar el correo de verificación. '
                  'Contacta al administrador.', 'warning')
            return redirect(url_for('login'))

        session['pending_email'] = email
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
            print(f"❌ Error reenviando correo: {e}", file=sys.stderr)
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
                print(f"❌ Error enviando reset: {e}", file=sys.stderr)

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

        user.password = nueva
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
    if current_user.rol != 'usuario':
        return redirect(url_for('dashboard_tecnico'))
    tickets    = Ticket.query.filter_by(usuario_id=current_user.id).order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = sum(1 for t in tickets if t.estado == 'Pendiente')
    en_proceso = sum(1 for t in tickets if t.estado == 'En Proceso')
    resueltos  = sum(1 for t in tickets if t.estado in ['Resuelto', 'Cerrado'])
    return render_template('dashboard_usuario.html', tickets=tickets,
                           pendientes=pendientes, en_proceso=en_proceso, resueltos=resueltos)


@app.route('/ticket/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_ticket():
    if request.method == 'POST':
        titulo      = request.form['titulo']
        descripcion = request.form['descripcion']
        categoria   = request.form['categoria']
        prioridad   = request.form.get('prioridad', 'Media')
        nuevo = Ticket(titulo=titulo, descripcion=descripcion, categoria=categoria,
                       prioridad=prioridad, usuario_id=current_user.id, estado='Pendiente')
        db.session.add(nuevo)
        db.session.commit()
        flash('Ticket creado exitosamente. El equipo de soporte lo atenderá pronto.', 'success')
        return redirect(url_for('dashboard_usuario'))
    return render_template('nuevo_ticket.html')


@app.route('/ticket/<int:ticket_id>')
@login_required
def ver_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if current_user.rol != 'tecnico' and ticket.usuario_id != current_user.id:
        flash('No tienes permiso para ver este ticket.', 'danger')
        return redirect(url_for('dashboard_usuario'))
    mensajes_ia = MensajeIA.query.filter_by(ticket_id=ticket.id).order_by(MensajeIA.timestamp).all()
    return render_template('detalle_ticket.html', ticket=ticket, mensajes_ia=mensajes_ia)


@app.route('/api/chat-ia', methods=['POST'])
@login_required
def chat_ia():
    data      = request.get_json()
    pregunta  = data.get('pregunta', '')
    ticket_id = data.get('ticket_id')
    respuesta = get_ai_response(pregunta, current_user.nombre)
    if ticket_id:
        ticket = Ticket.query.get(ticket_id)
        if ticket and (ticket.usuario_id == current_user.id or current_user.rol == 'tecnico'):
            db.session.add(MensajeIA(ticket_id=ticket.id,
                                     usuario_pregunta=pregunta,
                                     ia_respuesta=respuesta))
            db.session.commit()
    return jsonify({'respuesta': respuesta})


@app.route('/ticket/<int:ticket_id>/cerrar')
@login_required
def cerrar_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.usuario_id == current_user.id or current_user.rol == 'tecnico':
        ticket.estado = 'Cerrado'
        ticket.fecha_resolucion = datetime.utcnow()
        db.session.commit()
        flash('Ticket cerrado correctamente.', 'success')
    return redirect(url_for('ver_ticket', ticket_id=ticket.id))


# ==================== TÉCNICO / ADMIN ====================
@app.route('/dashboard/tecnico')
@login_required
def dashboard_tecnico():
    if current_user.rol != 'tecnico':
        return redirect(url_for('dashboard_usuario'))

    todos      = Ticket.query.order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = [t for t in todos if t.estado == 'Pendiente']
    en_proceso = [t for t in todos if t.estado == 'En Proceso']
    resueltos  = [t for t in todos if t.estado == 'Resuelto']
    cerrados   = [t for t in todos if t.estado == 'Cerrado']
    total_usuarios = Usuario.query.filter_by(rol='usuario').count()
    recientes  = todos[:10]

    return render_template('dashboard_tecnico.html',
                           todos_tickets=todos,
                           tickets_pendientes=pendientes,
                           tickets_en_proceso=en_proceso,
                           tickets_resueltos=resueltos,
                           tickets_cerrados=cerrados,
                           total_usuarios=total_usuarios,
                           recientes=recientes)


@app.route('/tecnico/ticket/<int:ticket_id>/asignar', methods=['POST'])
@login_required
def asignar_ticket(ticket_id):
    if current_user.rol != 'tecnico':
        return redirect(url_for('dashboard_usuario'))
    ticket = Ticket.query.get_or_404(ticket_id)
    ticket.tecnico_id = current_user.id
    ticket.estado = 'En Proceso'
    db.session.commit()
    flash('Ticket asignado y en proceso.', 'success')
    return redirect(url_for('ver_ticket', ticket_id=ticket.id))


@app.route('/tecnico/ticket/<int:ticket_id>/update', methods=['POST'])
@login_required
def update_ticket_estado(ticket_id):
    if current_user.rol != 'tecnico':
        return redirect(url_for('dashboard_usuario'))
    ticket = Ticket.query.get_or_404(ticket_id)
    nuevo_estado = request.form.get('estado')
    if nuevo_estado in ['Pendiente', 'En Proceso', 'Resuelto', 'Cerrado']:
        ticket.estado = nuevo_estado
        if nuevo_estado in ['Resuelto', 'Cerrado'] and not ticket.fecha_resolucion:
            ticket.fecha_resolucion = datetime.utcnow()
        db.session.commit()
        flash('Estado del ticket actualizado.', 'success')
    return redirect(url_for('ver_ticket', ticket_id=ticket.id))


# ==================== ADMIN USUARIOS ====================
@app.route('/admin/usuarios')
@login_required
def admin_usuarios():
    if current_user.rol != 'tecnico':
        return redirect(url_for('dashboard_usuario'))
    usuarios = Usuario.query.order_by(Usuario.fecha_registro.desc()).all()
    return render_template('admin_usuarios.html', usuarios=usuarios)


@app.route('/admin/usuario/crear', methods=['POST'])
@login_required
def admin_crear_usuario():
    if current_user.rol != 'tecnico':
        return redirect(url_for('dashboard_usuario'))
    nombre   = request.form['nombre'].strip()
    email    = request.form['email'].strip().lower()
    password = request.form['password']
    rol      = request.form.get('rol', 'usuario')
    area     = request.form.get('area', '').strip()

    if Usuario.query.filter_by(email=email).first():
        flash('Ya existe un usuario con ese correo electrónico.', 'danger')
        return redirect(url_for('admin_usuarios'))

    db.session.add(Usuario(nombre=nombre, email=email, password=password,
                           rol=rol, area=area, activo=True, email_verificado=True))
    db.session.commit()
    flash(f'Usuario "{nombre}" creado exitosamente.', 'success')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:user_id>/eliminar', methods=['POST'])
@login_required
def admin_eliminar_usuario(user_id):
    if current_user.rol != 'tecnico':
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
    if current_user.rol != 'tecnico':
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
