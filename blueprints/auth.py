"""
Blueprint de autenticación: registro, login, logout,
verificación de email y recuperación de contraseña.
"""
import re
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, session, current_app,
)
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import BadSignature, SignatureExpired

from database import db, Usuario
from utils.mail import send_verification_email, send_reset_email
from utils.constants import ADMIN_ROLE, TECH_ROLE, USER_ROLE, STAFF_ROLES
from utils.security import serializer, SALT_VERIFICACION, SALT_RESET

auth_bp = Blueprint('auth', __name__)


def _validar_password(password: str) -> str | None:
    """
    Valida que la contraseña cumpla los criterios de seguridad.
    Retorna un mensaje de error o None si es válida.
    """
    if len(password) < 8:
        return 'La contraseña debe tener al menos 8 caracteres.'
    if not re.search(r'[A-Z]', password):
        return 'La contraseña debe contener al menos una letra mayúscula.'
    if not re.search(r'[a-z]', password):
        return 'La contraseña debe contener al menos una letra minúscula.'
    if not re.search(r'[0-9]', password):
        return 'La contraseña debe contener al menos un número.'
    return None


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.rol in STAFF_ROLES:
            return redirect(url_for('tickets.dashboard_tecnico'))
        return redirect(url_for('tickets.dashboard_usuario'))

    if request.method == 'POST':
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        user     = Usuario.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if not user.email_verificado:
                session['pending_email'] = email
                flash('Debes verificar tu correo electrónico antes de iniciar sesión.', 'warning')
                return redirect(url_for('auth.verificar_pendiente'))
            db.session.commit()  # persiste migración de hash si ocurrió
            login_user(user)
            if user.rol in STAFF_ROLES:
                return redirect(url_for('tickets.dashboard_tecnico'))
            return redirect(url_for('tickets.dashboard_usuario'))

        flash('Correo o contraseña incorrectos.', 'danger')

    return render_template('login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('tickets.dashboard_usuario'))

    if request.method == 'POST':
        nombre  = request.form['nombre'].strip()
        email   = request.form['email'].strip().lower()
        password = request.form['password']
        confirm  = request.form['confirm_password']
        area     = request.form.get('area', '').strip()

        if not nombre or not email or not password:
            flash('Todos los campos son obligatorios.', 'danger')
            return render_template('register.html')
        if password != confirm:
            flash('Las contraseñas no coinciden.', 'danger')
            return render_template('register.html')

        # Validación de fortaleza
        error_pass = _validar_password(password)
        if error_pass:
            flash(error_pass, 'danger')
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
            send_verification_email(nuevo)
        except Exception as e:
            current_app.logger.error('enviando correo de verificacion: %s', e, exc_info=True)
            flash('Cuenta creada, pero no pudimos enviar el correo de verificación. '
                  'Usa reenviar cuando el correo esté configurado.', 'warning')

        return redirect(url_for('auth.verificar_pendiente'))

    return render_template('register.html')


@auth_bp.route('/verificar-pendiente')
def verificar_pendiente():
    email = session.get('pending_email', '')
    return render_template('verificar_pendiente.html', email=email)


@auth_bp.route('/verificar-email/<token>')
def verificar_email(token):
    try:
        email = serializer().loads(token, salt=SALT_VERIFICACION, max_age=86400)
    except SignatureExpired:
        flash('El enlace de verificación ha expirado. Solicita uno nuevo.', 'danger')
        return redirect(url_for('auth.login'))
    except BadSignature:
        flash('Enlace de verificación inválido.', 'danger')
        return redirect(url_for('auth.login'))

    user = Usuario.query.filter_by(email=email).first_or_404()
    if user.email_verificado:
        flash('Tu cuenta ya está verificada. ¡Puedes iniciar sesión!', 'info')
    else:
        user.email_verificado = True
        db.session.commit()
        flash('¡Correo verificado exitosamente! Ya puedes iniciar sesión.', 'success')

    return redirect(url_for('auth.login'))


@auth_bp.route('/reenviar-verificacion', methods=['POST'])
def reenviar_verificacion():
    email = request.form.get('email', '').strip().lower()
    user  = Usuario.query.filter_by(email=email).first()

    if user and not user.email_verificado:
        try:
            send_verification_email(user)
            flash('Correo de verificación reenviado. Revisa tu bandeja de entrada.', 'success')
        except Exception as e:
            current_app.logger.error('reenviando verificacion: %s', e, exc_info=True)
            flash('No se pudo reenviar el correo. Intenta más tarde.', 'danger')
    else:
        flash('Si el correo existe y no está verificado, recibirás el mensaje.', 'info')

    session['pending_email'] = email
    return redirect(url_for('auth.verificar_pendiente'))


@auth_bp.route('/recuperar-password', methods=['GET', 'POST'])
def recuperar_password():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        user  = Usuario.query.filter_by(email=email).first()

        if user:
            try:
                send_reset_email(user)
            except Exception as e:
                current_app.logger.error('enviando correo reset: %s', e, exc_info=True)

        flash('Si el correo está registrado, recibirás un enlace para restablecer tu contraseña.', 'info')
        return redirect(url_for('auth.login'))

    return render_template('recuperar_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer().loads(token, salt=SALT_RESET, max_age=1800)
    except SignatureExpired:
        flash('El enlace ha expirado. Solicita uno nuevo.', 'danger')
        return redirect(url_for('auth.recuperar_password'))
    except BadSignature:
        flash('Enlace inválido.', 'danger')
        return redirect(url_for('auth.recuperar_password'))

    user = Usuario.query.filter_by(email=email).first_or_404()

    if request.method == 'POST':
        nueva     = request.form['password']
        confirmar = request.form['confirm_password']

        error_pass = _validar_password(nueva)
        if error_pass:
            flash(error_pass, 'danger')
            return render_template('reset_password.html', token=token)
        if nueva != confirmar:
            flash('Las contraseñas no coinciden.', 'danger')
            return render_template('reset_password.html', token=token)

        user.set_password(nueva)
        db.session.commit()
        flash('Contraseña actualizada correctamente. Ya puedes iniciar sesión.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', token=token)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
