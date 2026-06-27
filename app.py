"""
Punto de entrada principal de TicketIA.
Configura la aplicación Flask, registra blueprints y arranca el servidor.
"""
import logging
import os
import secrets
import sqlite3
import sys

from flask import Flask, redirect, url_for
from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from sqlalchemy import inspect, text
from dotenv import load_dotenv

from database import db, Usuario

load_dotenv()

# ──────────────────────────────────────────────────────────────
# Logging estructurado
# ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('ticketia')


# ──────────────────────────────────────────────────────────────
# Helpers de configuración de base de datos
# ──────────────────────────────────────────────────────────────

def _get_valid_database_path() -> str:
    candidate_paths = [
        r"C:\tickets.db",
        os.path.join(os.environ.get('USERPROFILE', r'C:\Users\Pablo'), 'tickets.db'),
        os.path.abspath('tickets.db'),
    ]
    for path in candidate_paths:
        try:
            test_conn = sqlite3.connect(path)
            test_conn.close()
            logger.info('Base de datos válida en: %s', path)
            return path
        except Exception as exc:
            logger.debug('No se pudo usar %s: %s', path, exc)
    logger.critical('No se pudo encontrar una ubicación para la base de datos.')
    sys.exit(1)


def _normalize_database_uri(database_url: str) -> str:
    if database_url.startswith('postgres://'):
        return 'postgresql+psycopg://' + database_url[len('postgres://'):]
    if database_url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + database_url[len('postgresql://'):]
    return database_url


def _safe_db_uri_for_log(uri: str) -> str:
    if '://' not in uri or '@' not in uri:
        return uri
    scheme, rest = uri.split('://', 1)
    host_part = rest.split('@', 1)[1]
    return f'{scheme}://***:***@{host_part}'


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


# ──────────────────────────────────────────────────────────────
# Factory de la aplicación
# ──────────────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__)

    # ── Seguridad ──
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        secret_key = secrets.token_hex(32)
        logger.warning(
            'SECRET_KEY no configurada en .env — usando clave efímera. '
            'Las sesiones no sobrevivirán reinicios.'
        )
    app.config['SECRET_KEY'] = secret_key

    # ── Base de datos ──
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        db_uri = _normalize_database_uri(database_url)
    else:
        db_path = _get_valid_database_path()
        if os.environ.get('RENDER') or not db_path.startswith(('C:', 'D:')):
            db_uri = 'sqlite:///test.db'
        else:
            db_uri = 'sqlite:///' + db_path.replace('\\', '/')

    app.config['SQLALCHEMY_DATABASE_URI']  = db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
    logger.info('URI de base de datos: %s', _safe_db_uri_for_log(db_uri))

    # ── Correo ──
    app.config['MAIL_SERVER']         = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    app.config['MAIL_PORT']           = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS']        = _env_bool('MAIL_USE_TLS', True)
    app.config['MAIL_USE_SSL']        = _env_bool('MAIL_USE_SSL', False)
    app.config['MAIL_USERNAME']       = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD']       = (os.environ.get('MAIL_PASSWORD') or '').replace(' ', '')
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER') or app.config['MAIL_USERNAME']
    app.config['MAIL_TIMEOUT']        = int(os.environ.get('MAIL_TIMEOUT', 10))
    app.config['MAIL_PROVIDER']       = os.environ.get('MAIL_PROVIDER', '').strip().lower()
    app.config['RESEND_API_KEY']      = os.environ.get('RESEND_API_KEY')
    app.config['RESEND_FROM']         = os.environ.get('RESEND_FROM', 'TicketIA Bellavista <onboarding@resend.dev>')
    app.config['BREVO_API_KEY']       = os.environ.get('BREVO_API_KEY')
    app.config['BREVO_FROM_EMAIL']    = os.environ.get('BREVO_FROM_EMAIL') or app.config['MAIL_USERNAME']
    app.config['BREVO_FROM_NAME']     = os.environ.get('BREVO_FROM_NAME', 'TicketIA Bellavista')

    # ── Extensiones ──
    db.init_app(app)
    Migrate(app, db)
    Mail(app)

    # ── Login Manager ──
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Debes iniciar sesión para acceder.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        return Usuario.query.get(int(user_id))

    # ── Blueprints ──
    from blueprints.auth    import auth_bp
    from blueprints.tickets import tickets_bp
    from blueprints.admin   import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(admin_bp)

    # ── Ruta raíz ──
    @app.route('/')
    def index():
        return redirect(url_for('auth.login'))

    # ── Compatibilidad con rutas legacy (evita romper bookmarks/emails) ──
    @app.route('/dashboard/usuario')
    def _compat_dashboard_usuario():
        return redirect(url_for('tickets.dashboard_usuario'))

    @app.route('/dashboard/tecnico')
    def _compat_dashboard_tecnico():
        return redirect(url_for('tickets.dashboard_tecnico'))

    @app.route('/admin/usuarios')
    def _compat_admin_usuarios():
        return redirect(url_for('admin.usuarios'))

    @app.route('/reportes/estadisticas')
    def _compat_estadisticas():
        return redirect(url_for('admin.estadisticas'))

    # ── Inicialización / migración de BD ──
    with app.app_context():
        _init_db(app)

    logger.info('Aplicación TicketIA lista.')
    return app


# ──────────────────────────────────────────────────────────────
# Inicialización de base de datos
# ──────────────────────────────────────────────────────────────

def _init_db(app: Flask) -> None:
    from utils.constants import ADMIN_ROLE, USER_ROLE

    db.create_all()
    inspector = inspect(db.engine)
    usuario_columns = {col['name'] for col in inspector.get_columns('usuarios')}
    ticket_columns  = {col['name'] for col in inspector.get_columns('tickets')}

    default_true  = '1' if db.engine.dialect.name == 'sqlite' else 'TRUE'
    default_false = '0' if db.engine.dialect.name == 'sqlite' else 'FALSE'
    added_email_verificado = False

    # Migraciones legacy (columnas añadidas antes de usar Flask-Migrate)
    _add_column_if_missing(
        'usuarios', 'activo', f'BOOLEAN DEFAULT {default_true}', usuario_columns
    )
    if 'email_verificado' not in usuario_columns:
        _add_column_if_missing(
            'usuarios', 'email_verificado', f'BOOLEAN DEFAULT {default_false}', set()
        )
        added_email_verificado = True

    _add_column_if_missing('tickets', 'informe_cierre', 'TEXT', ticket_columns)
    _add_column_if_missing('tickets', 'informe_fecha',  'TIMESTAMP', ticket_columns)

    if added_email_verificado:
        # Cuentas anteriores a la verificación se marcan automáticamente
        Usuario.query.filter(
            (Usuario.email_verificado.is_(None)) | (Usuario.email_verificado == False)
        ).update({Usuario.email_verificado: True}, synchronize_session=False)
        db.session.commit()

    # Usuarios demo
    _ensure_admin_demo(ADMIN_ROLE)
    _ensure_user_demo(USER_ROLE)
    db.session.commit()
    logger.info('Base de datos lista.')


def _add_column_if_missing(table: str, column: str, definition: str, existing: set) -> None:
    if column in existing:
        return
    try:
        with db.engine.connect() as conn:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {definition}'))
            conn.commit()
        logger.info('Columna %s.%s agregada.', table, column)
    except Exception as exc:
        logger.warning('No se pudo agregar columna %s.%s: %s', table, column, exc)


def _ensure_admin_demo(admin_role: str) -> None:
    admin = Usuario.query.filter_by(email='tecnico@bellavista.gob.pe').first()
    if admin:
        admin.rol              = admin_role
        admin.area             = admin.area or 'Informática'
        admin.email_verificado = True
        # Migrar hash si aún está en texto plano
        if not (admin.password.startswith('pbkdf2:') or admin.password.startswith('scrypt:')):
            admin.set_password(admin.password)
    else:
        admin = Usuario(
            nombre='Administrador TI',
            email='tecnico@bellavista.gob.pe',
            rol=admin_role,
            area='Informática',
            activo=True,
            email_verificado=True,
        )
        admin.set_password('tecnico0123')
        db.session.add(admin)


def _ensure_user_demo(user_role: str) -> None:
    user = Usuario.query.filter_by(email='usuario@bellavista.gob.pe').first()
    if not user:
        user = Usuario(
            nombre='Usuario Demo',
            email='usuario@bellavista.gob.pe',
            rol=user_role,
            area='Administración',
            activo=True,
            email_verificado=True,
        )
        user.set_password('usuario123')
        db.session.add(user)
    elif not (user.password.startswith('pbkdf2:') or user.password.startswith('scrypt:')):
        user.set_password(user.password)


# ──────────────────────────────────────────────────────────────
# Punto de entrada
# ──────────────────────────────────────────────────────────────

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
