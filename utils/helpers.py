"""
Helpers de negocio: permisos, notificaciones, serialización JSON,
construcción de informes de cierre y actualización de estado de tickets.
"""
from datetime import datetime

from flask import url_for
from flask_login import current_user

from database import db, Usuario, Ticket, MensajeTicket, Notificacion
from utils.constants import ADMIN_ROLE, TECH_ROLE, USER_ROLE, STAFF_ROLES


# ──────────────────────────────────────────────────────────────
# Permisos
# ──────────────────────────────────────────────────────────────

def is_admin(user=None) -> bool:
    user = user or current_user
    return user.is_authenticated and user.rol == ADMIN_ROLE


def is_staff(user=None) -> bool:
    user = user or current_user
    return user.is_authenticated and user.rol in STAFF_ROLES


def is_assigned_tech(ticket, user=None) -> bool:
    user = user or current_user
    return user.is_authenticated and user.rol == TECH_ROLE and ticket.tecnico_id == user.id


def can_view_ticket(ticket) -> bool:
    return (
        is_admin()
        or ticket.usuario_id == current_user.id
        or is_assigned_tech(ticket)
    )


def can_manage_ticket(ticket) -> bool:
    return is_admin() or is_assigned_tech(ticket)


def ticket_query_for_current_user():
    if is_admin():
        return Ticket.query
    if current_user.rol == TECH_ROLE:
        return Ticket.query.filter_by(tecnico_id=current_user.id)
    return Ticket.query.filter_by(usuario_id=current_user.id)


def tecnicos_activos():
    return (
        Usuario.query
        .filter_by(rol=TECH_ROLE, activo=True)
        .order_by(Usuario.nombre.asc())
        .all()
    )


def admin_users():
    return Usuario.query.filter_by(rol=ADMIN_ROLE, activo=True).all()


# ──────────────────────────────────────────────────────────────
# Notificaciones
# ──────────────────────────────────────────────────────────────

def notify_user(usuario_id, titulo, mensaje, ticket_id=None) -> None:
    if not usuario_id:
        return
    db.session.add(Notificacion(
        usuario_id=usuario_id,
        ticket_id=ticket_id,
        titulo=titulo,
        mensaje=mensaje,
        leida=False,
    ))


def notify_admins(titulo, mensaje, ticket_id=None, exclude_user_id=None) -> None:
    for admin in admin_users():
        if admin.id != exclude_user_id:
            notify_user(admin.id, titulo, mensaje, ticket_id)


# ──────────────────────────────────────────────────────────────
# Serialización JSON
# ──────────────────────────────────────────────────────────────

def notification_json(notificacion) -> dict:
    return {
        'id':     notificacion.id,
        'titulo': notificacion.titulo,
        'mensaje': notificacion.mensaje,
        'leida':  notificacion.leida,
        'fecha':  notificacion.fecha.strftime('%d/%m/%Y %H:%M') if notificacion.fecha else '',
        'url':    url_for('tickets.ver_ticket', ticket_id=notificacion.ticket_id)
                  if notificacion.ticket_id else '',
    }


def mensaje_ticket_json(mensaje) -> dict:
    autor = mensaje.usuario
    return {
        'id':        mensaje.id,
        'mensaje':   mensaje.mensaje,
        'autor':     autor.nombre if autor else 'Usuario',
        'autor_rol': autor.rol if autor else '',
        'propio':    mensaje.usuario_id == current_user.id,
        'fecha':     mensaje.fecha.strftime('%d/%m/%Y %H:%M') if mensaje.fecha else '',
    }


def ticket_json(ticket) -> dict:
    creador = ticket.creador
    tecnico = ticket.tecnico
    creador_nombre = creador.nombre if creador else ''
    tecnico_nombre = tecnico.nombre if tecnico else ''

    return {
        'id':             ticket.id,
        'titulo':         ticket.titulo,
        'descripcion':    ticket.descripcion or '',
        'categoria':      ticket.categoria or '',
        'estado':         ticket.estado,
        'prioridad':      ticket.prioridad,
        'fecha':          ticket.fecha_creacion.strftime('%d/%m/%Y') if ticket.fecha_creacion else '',
        'hora':           ticket.fecha_creacion.strftime('%H:%M') if ticket.fecha_creacion else '',
        'fecha_resolucion': ticket.fecha_resolucion.strftime('%d/%m/%Y') if ticket.fecha_resolucion else '',
        'creador_nombre': creador_nombre,
        'creador_inicial': creador_nombre[:1].upper(),
        'creador_area':   creador.area if creador else '',
        'tecnico_id':     ticket.tecnico_id,
        'tecnico_nombre': tecnico_nombre,
        'tecnico_area':   tecnico.area if tecnico else '',
        'tecnico_email':  tecnico.email if tecnico else '',
        'informe_fecha':  ticket.informe_fecha.strftime('%d/%m/%Y %H:%M') if ticket.informe_fecha else '',
        'informe_cierre': ticket.informe_cierre or '',
        'tiene_informe':  bool(ticket.informe_cierre),
        'url':            url_for('tickets.ver_ticket', ticket_id=ticket.id),
        'assign_url':     url_for('admin.asignar_ticket', ticket_id=ticket.id),
        'delete_url':     url_for('admin.eliminar_ticket', ticket_id=ticket.id),
    }


def usuario_json(usuario) -> dict:
    rol_label = {
        ADMIN_ROLE: 'Administrador',
        TECH_ROLE:  'Tecnico',
        USER_ROLE:  'Usuario',
    }.get(usuario.rol, usuario.rol)
    return {
        'id':             usuario.id,
        'nombre':         usuario.nombre,
        'inicial':        usuario.nombre[:1].upper(),
        'email':          usuario.email,
        'rol':            usuario.rol,
        'rol_label':      rol_label,
        'area':           usuario.area or '',
        'activo':         usuario.activo,
        'email_verificado': usuario.email_verificado,
        'fecha_registro': usuario.fecha_registro.strftime('%d/%m/%Y') if usuario.fecha_registro else '',
        'make_tech_url':  url_for('admin.hacer_tecnico', user_id=usuario.id),
        'remove_tech_url': url_for('admin.quitar_tecnico', user_id=usuario.id),
        'delete_url':     url_for('admin.eliminar_usuario', user_id=usuario.id),
    }


# ──────────────────────────────────────────────────────────────
# Informes de cierre
# ──────────────────────────────────────────────────────────────

def build_ticket_report(ticket) -> str:
    mensajes = (
        MensajeTicket.query
        .filter_by(ticket_id=ticket.id)
        .order_by(MensajeTicket.fecha.asc())
        .all()
    )
    historial = []
    for m in mensajes:
        autor = m.usuario.nombre if m.usuario else 'Usuario'
        fecha = m.fecha.strftime('%d/%m/%Y %H:%M') if m.fecha else ''
        historial.append(f"- {fecha} | {autor}: {m.mensaje}")

    historial_texto = '\n'.join(historial) if historial else 'No hubo mensajes entre usuario y soporte.'
    tecnico      = ticket.tecnico.nombre if ticket.tecnico else 'Sin tecnico asignado'
    fecha_crea   = ticket.fecha_creacion.strftime('%d/%m/%Y %H:%M') if ticket.fecha_creacion else 'Sin fecha'
    fecha_cierre = (
        ticket.fecha_resolucion.strftime('%d/%m/%Y %H:%M')
        if ticket.fecha_resolucion
        else datetime.utcnow().strftime('%d/%m/%Y %H:%M')
    )

    return (
        f"INFORME DE CIERRE - TICKET #{ticket.id}\n\n"
        f"Solicitante: {ticket.creador.nombre if ticket.creador else 'No disponible'}\n"
        f"Area solicitante: {ticket.creador.area if ticket.creador and ticket.creador.area else 'No registrada'}\n"
        f"Tecnico responsable: {tecnico}\n"
        f"Categoria: {ticket.categoria or 'No registrada'}\n"
        f"Prioridad: {ticket.prioridad or 'No registrada'}\n"
        f"Estado final: {ticket.estado}\n"
        f"Fecha de creacion: {fecha_crea}\n"
        f"Fecha de cierre: {fecha_cierre}\n\n"
        f"Problema reportado:\n{ticket.descripcion or 'Sin descripcion'}\n\n"
        f"Historial de comunicacion:\n{historial_texto}\n\n"
        f"Conclusion:\nEl ticket fue marcado como {ticket.estado}. "
        f"Este informe fue generado automaticamente por TicketIA."
    )


def ensure_ticket_report(ticket, force: bool = False) -> None:
    if ticket.estado not in ['Resuelto', 'Cerrado']:
        return
    if ticket.informe_cierre and not force:
        return
    ticket.informe_cierre = build_ticket_report(ticket)
    ticket.informe_fecha  = datetime.utcnow()


def set_ticket_status(ticket, nuevo_estado: str) -> None:
    estado_anterior = ticket.estado
    ticket.estado   = nuevo_estado

    if nuevo_estado in ['Resuelto', 'Cerrado']:
        ticket.fecha_resolucion = ticket.fecha_resolucion or datetime.utcnow()
        ensure_ticket_report(ticket, force=(nuevo_estado == 'Cerrado'))
    elif estado_anterior in ['Resuelto', 'Cerrado']:
        ticket.fecha_resolucion = None

    if estado_anterior != nuevo_estado:
        notify_user(
            ticket.usuario_id,
            'Estado actualizado',
            f'Tu ticket #{ticket.id} cambió a {nuevo_estado}.',
            ticket.id,
        )
        if ticket.tecnico_id and ticket.tecnico_id != current_user.id:
            notify_user(
                ticket.tecnico_id,
                'Estado actualizado',
                f'El ticket #{ticket.id} cambió a {nuevo_estado}.',
                ticket.id,
            )
        if not is_admin():
            notify_admins(
                'Estado actualizado',
                f'El ticket #{ticket.id} cambió a {nuevo_estado}.',
                ticket.id,
                exclude_user_id=current_user.id,
            )
