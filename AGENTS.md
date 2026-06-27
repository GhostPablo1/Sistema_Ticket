# AGENTS.md

Este es un sistema de tickets Flask completo con AI de apoyo. Punto de entrada principal: `app.py`.

## Descripción general

- **Aplicación principal:** `app.py` (contenedor Flask completo con todas las rutas y lógica de negocio)
- **Packs de características:** `ai_assistant.py` (IA para soporte técnico con respuestas directas a usuarios)
- **Base de datos:** SQLite/PostgreSQL con SQLAlchemy y migraciones automáticas
- **Contacto:** `pablosaldarriaga152@gmail.com` (envíe correos a este para pruebas)

## Configuración inicial

### Requisitos mínimos

```bash
python3 app.py
```

### Variables de entorno

- `DATABASE_URL`: URL de PostgreSQL (opcional, se usa SQLite si no se provee)
- `MAIL_USERNAME`/`MAIL_PASSWORD`: Credenciales de Gmail para envío de correos (ejemplo incluido en .env)
- `ANTHROPIC_AUTH_TOKEN`: API key para OpenRouter (gratuito, opcional)

### Scripts adicionales recomendados

```bash
# Crear entorno virtual, instalar dependencias
cat requirements.txt | xargs pip install

# Ejecutar script de seed de datos (si está presente)
# python seed_data.py
```

## Comando de administración principal

### Modo de desarrollo

```bash
python3 app.py
```

- El servidor inicia en `http://localhost:5000`
- Administradores con `rol=ADMIN_ROLE` (por defecto `tecnico@bellavista.gob.pe`) pueden acceder al panel `/dashboard/tecnico`
- Los usuarios con `rol=USER_ROLE` pueden crear y gestionar tickets en `/dashboard/usuario`

## Configuración y entrenamiento del AI

### Entrenamiento del AI

- El AI usa un prompt fijo en `ai_assistant.py` con temas específicos del Perú (tinta, atascos, WiFi, equipos)
- Las CA de correo electrónico y el envío están completamente configurados y funcionan con un solo clic (usando la contraseña en .env)

## Testing (si está presente)

- No hay estructura de testing típica en este sistema
- No hay setup especializado para pruebas integrables
- Ver `templates/` para interfaces de usuario

## Flujo de trabajo típico

1. **Crear usuario:** `/register` (registra y envía automáticamente el correo electrónico de verificación)
2. **Verificar cuenta:** `/verificar-email/<token>` (revisa tu bandeja de entrada o reenviar la verificación)
3. **Iniciar sesión y crear ticket:** `/dashboard/usuario → /ticket/nuevo`
4. **Administrador asigna y actualiza tickets:** `/dashboard/tecnico` y `/admin/ticket/<id>/asignar`

## Puntos técnicos específicos

- **Migración de columnas:** Se ejecuta automáticamente con el sistema (agrega `activo`, `email_verificado` a `usuarios`; `informe_cierre` y `informe_fecha` a `tickets`)
- **Email:** Múltiples proveedores con conmutación automática (SMTP → Brevo → Resend). El SMTP se preferencia es Gmail; incluye backup para Render.
- **Paginar:** Las notificaciones se limitan a 12 por petición (`/api/notificaciones`)
- **Solo SQL:** `pagos` no son parte del sistema actual
- **Modo técnico:** Los técnicos pueden marcar tickets como "Resuelto" para generar informes automáticos de cierre

## Véase tambièn

- `templates/` para componentes de interfaz de usuario
- `requirements.txt` para dependencias exactas
- `database.py` para el esquema de base de datos (si está disponible)