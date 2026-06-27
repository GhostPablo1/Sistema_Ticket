"""Helpers de seguridad: serializer para tokens firmados."""
from flask import current_app
from itsdangerous import URLSafeTimedSerializer

SALT_VERIFICACION = 'email-verify-2026'
SALT_RESET        = 'password-reset-2026'


def serializer() -> URLSafeTimedSerializer:
    """Retorna un serializer usando la SECRET_KEY de la app actual."""
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
