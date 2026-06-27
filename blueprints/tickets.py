"""
Blueprint de tickets: dashboards de usuario y técnico,
creación, detalle, cierre, chat IA, mensajes y notificaciones.
"""
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, current_app,
)
from flask_login import login_required, current_user

from database import db, Ticket, MensajeIA, MensajeTicket, Notificacion
from ai_assistant import get_ai_response
from utils.constants import ADMIN_ROLE, TECH_ROLE, USER_ROLE, STAFF_ROLES
from utils.helpers import (
    is_admin, is_staff, is_assigned_tech,
    can_view_ticket, can_manage_ticket,
    ticket_query_for_current_user, tecnicos_activos,
    notify_user, notify_admins,
    notification_json, mensaje_ticket_json, ticket_json,
    set_ticket_status, ensure_ticket_report,
)

tickets_bp = Blueprint('tickets', __name__)


# ──────────────────────────────────────────────────────────────
# Dashboards
# ──────────────────────────────────────────────────────────────

@tickets_bp.route('/')
def index():
    return redirect(url_for('auth.login'))


@tickets_bp.route('/dashboard/usuario')
@login_required
def dashboard_usuario():
    if current_user.rol != USER_ROLE:
        return redirect(url_for('tickets.dashboard_tecnico'))
    tickets    = Ticket.query.filter_by(usuario_id=current_user.id).order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = sum(1 for t in tickets if t.estado == 'Pendiente')
    en_proceso = sum(1 for t in tickets if t.estado == 'En Proceso')
    resueltos  = sum(1 for t in tickets if t.estado in ['Resuelto', 'Cerrado'])
    return render_template('dashboard_usuario.html', tickets=tickets,
                           pendientes=pendientes, en_proceso=en_proceso, resueltos=resueltos)


@tickets_bp.route('/api/usuario/tickets')
@login_required
def api_usuario_tickets():
    if current_user.rol != USER_ROLE:
        return jsonify({'error': 'No autorizado'}), 403

    tickets    = Ticket.query.filter_by(usuario_id=current_user.id).order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = sum(1 for t in tickets if t.estado == 'Pendiente')
    en_proceso = sum(1 for t in tickets if t.estado == 'En Proceso')
    resueltos  = sum(1 for t in tickets if t.estado in ['Resuelto', 'Cerrado'])

    return jsonify({
        'tickets': [ticket_json(t) for t in tickets],
        'stats': {
            'total':      len(tickets),
            'pendientes': pendientes,
            'en_proceso': en_proceso,
            'resueltos':  resueltos,
        },
    })


@tickets_bp.route('/dashboard/tecnico')
@login_required
def dashboard_tecnico():
    if not is_staff():
        return redirect(url_for('tickets.dashboard_usuario'))

    todos      = ticket_query_for_current_user().order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = [t for t in todos if t.estado == 'Pendiente']
    en_proceso = [t for t in todos if t.estado == 'En Proceso']
    resueltos  = [t for t in todos if t.estado == 'Resuelto']
    cerrados   = [t for t in todos if t.estado == 'Cerrado']

    from database import Usuario
    total_usuarios = Usuario.query.filter_by(rol=USER_ROLE).count()

    return render_template(
        'dashboard_tecnico.html',
        todos_tickets=todos,
        tickets_pendientes=pendientes,
        tickets_en_proceso=en_proceso,
        tickets_resueltos=resueltos,
        tickets_cerrados=cerrados,
        total_usuarios=total_usuarios,
        recientes=todos[:10],
        tecnicos=tecnicos_activos(),
    )


@tickets_bp.route('/api/tecnico/tickets')
@login_required
def api_tecnico_tickets():
    if not is_staff():
        return jsonify({'error': 'No autorizado'}), 403

    todos      = ticket_query_for_current_user().order_by(Ticket.fecha_creacion.desc()).all()
    pendientes = sum(1 for t in todos if t.estado == 'Pendiente')
    en_proceso = sum(1 for t in todos if t.estado == 'En Proceso')
    resueltos  = sum(1 for t in todos if t.estado == 'Resuelto')
    cerrados   = sum(1 for t in todos if t.estado == 'Cerrado')

    return jsonify({
        'tickets': [ticket_json(t) for t in todos],
        'stats': {
            'total':      len(todos),
            'pendientes': pendientes,
            'en_proceso': en_proceso,
            'resueltos':  resueltos,
            'cerrados':   cerrados,
        },
    })


# ──────────────────────────────────────────────────────────────
# CRUD de tickets
# ──────────────────────────────────────────────────────────────

@tickets_bp.route('/ticket/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_ticket():
    if current_user.rol != USER_ROLE:
        return redirect(url_for('tickets.dashboard_tecnico'))

    if request.method == 'POST':
        titulo      = request.form['titulo']
        descripcion = request.form['descripcion']
        categoria   = request.form['categoria']
        prioridad   = request.form.get('prioridad', 'Media')
        nuevo = Ticket(
            titulo=titulo, descripcion=descripcion,
            categoria=categoria, prioridad=prioridad,
            usuario_id=current_user.id, estado='Pendiente',
        )
        db.session.add(nuevo)
        db.session.commit()
        notify_admins(
            'Nuevo ticket pendiente',
            f'{current_user.nombre} creó el ticket #{nuevo.id}: {nuevo.titulo}',
            nuevo.id,
        )
        db.session.commit()
        flash('Ticket creado exitosamente. El equipo de soporte lo atenderá pronto.', 'success')
        return redirect(url_for('tickets.dashboard_usuario'))

    return render_template('nuevo_ticket.html')


@tickets_bp.route('/ticket/<int:ticket_id>')
@login_required
def ver_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not can_view_ticket(ticket):
        flash('No tienes permiso para ver este ticket.', 'danger')
        return redirect(url_for('tickets.dashboard_tecnico') if is_staff() else url_for('tickets.dashboard_usuario'))

    mensajes_ia     = MensajeIA.query.filter_by(ticket_id=ticket.id).order_by(MensajeIA.timestamp).all()
    mensajes_ticket = MensajeTicket.query.filter_by(ticket_id=ticket.id).order_by(MensajeTicket.fecha.asc()).all()
    return render_template(
        'detalle_ticket.html',
        ticket=ticket,
        mensajes_ia=mensajes_ia,
        mensajes_ticket=mensajes_ticket,
        tecnicos=tecnicos_activos(),
    )


@tickets_bp.route('/api/ticket/<int:ticket_id>/resumen')
@login_required
def api_ticket_resumen(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not can_view_ticket(ticket):
        return jsonify({'error': 'No autorizado'}), 403

    return jsonify({
        'ticket':      ticket_json(ticket),
        'mensajes_ia': MensajeIA.query.filter_by(ticket_id=ticket.id).count(),
    })


@tickets_bp.route('/ticket/<int:ticket_id>/cerrar')
@login_required
def cerrar_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.usuario_id == current_user.id or can_manage_ticket(ticket):
        set_ticket_status(ticket, 'Cerrado')
        db.session.commit()
        flash('Ticket cerrado correctamente.', 'success')
    return redirect(url_for('tickets.ver_ticket', ticket_id=ticket.id))


@tickets_bp.route('/tecnico/ticket/<int:ticket_id>/update', methods=['POST'])
@login_required
def update_ticket_estado(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not can_manage_ticket(ticket):
        flash('No tienes permiso para modificar este ticket.', 'danger')
        return redirect(
            url_for('tickets.dashboard_tecnico') if is_staff()
            else url_for('tickets.dashboard_usuario')
        )

    nuevo_estado = request.form.get('estado')
    if nuevo_estado in ['Pendiente', 'En Proceso', 'Resuelto', 'Cerrado']:
        set_ticket_status(ticket, nuevo_estado)
        db.session.commit()
        flash('Estado del ticket actualizado.', 'success')
    return redirect(url_for('tickets.ver_ticket', ticket_id=ticket.id))


@tickets_bp.route('/ticket/<int:ticket_id>/informe/regenerar', methods=['POST'])
@login_required
def regenerar_informe_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not can_manage_ticket(ticket):
        flash('No tienes permiso para generar el informe.', 'danger')
        return redirect(url_for('tickets.ver_ticket', ticket_id=ticket.id))

    if ticket.estado not in ['Resuelto', 'Cerrado']:
        flash('El informe se genera cuando el ticket está resuelto o cerrado.', 'warning')
        return redirect(url_for('tickets.ver_ticket', ticket_id=ticket.id))

    ensure_ticket_report(ticket, force=True)
    db.session.commit()
    flash('Informe actualizado.', 'success')
    return redirect(url_for('tickets.ver_ticket', ticket_id=ticket.id))


# ──────────────────────────────────────────────────────────────
# Auto-asignación para técnicos
# ──────────────────────────────────────────────────────────────

@tickets_bp.route('/tecnico/ticket/<int:ticket_id>/auto-asignar', methods=['POST'])
@login_required
def auto_asignar_ticket(ticket_id):
    """Permite a un técnico tomarse un ticket pendiente sin asignar."""
    if current_user.rol != TECH_ROLE:
        return jsonify({'error': 'Solo los técnicos pueden auto-asignarse tickets'}), 403

    ticket = Ticket.query.get_or_404(ticket_id)

    if ticket.tecnico_id and ticket.tecnico_id != current_user.id:
        return jsonify({'error': 'Este ticket ya está asignado a otro técnico'}), 409

    ticket.tecnico_id = current_user.id
    if ticket.estado == 'Pendiente':
        ticket.estado = 'En Proceso'

    notify_user(
        ticket.usuario_id,
        'Técnico asignado',
        f'{current_user.nombre} se asignó a tu ticket #{ticket.id}.',
        ticket.id,
    )
    notify_admins(
        'Técnico se auto-asignó un ticket',
        f'{current_user.nombre} tomó el ticket #{ticket.id}: {ticket.titulo}',
        ticket.id,
    )
    db.session.commit()

    return jsonify({'ok': True, 'tecnico': current_user.nombre, 'estado': ticket.estado})


# ──────────────────────────────────────────────────────────────
# Chat IA
# ──────────────────────────────────────────────────────────────

@tickets_bp.route('/api/chat-ia', methods=['POST'])
@login_required
def chat_ia():
    data      = request.get_json()
    pregunta  = data.get('pregunta', '')
    ticket_id = data.get('ticket_id')

    # Recuperar historial para contexto
    historial = []
    if ticket_id:
        ticket = Ticket.query.get(ticket_id)
        if ticket and can_view_ticket(ticket):
            mensajes_prev = (
                MensajeIA.query
                .filter_by(ticket_id=ticket_id)
                .order_by(MensajeIA.timestamp.asc())
                .limit(10)
                .all()
            )
            for m in mensajes_prev:
                historial.append({'role': 'user',      'content': m.usuario_pregunta})
                historial.append({'role': 'assistant', 'content': m.ia_respuesta})

    respuesta = get_ai_response(pregunta, current_user.nombre, historial=historial)

    if ticket_id:
        ticket = Ticket.query.get(ticket_id)
        if ticket and can_view_ticket(ticket):
            db.session.add(MensajeIA(
                ticket_id=ticket.id,
                usuario_pregunta=pregunta,
                ia_respuesta=respuesta,
            ))
            db.session.commit()

    return jsonify({'respuesta': respuesta})


# ──────────────────────────────────────────────────────────────
# Mensajes de ticket
# ──────────────────────────────────────────────────────────────

@tickets_bp.route('/api/ticket/<int:ticket_id>/mensajes', methods=['GET', 'POST'])
@login_required
def api_ticket_mensajes(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not can_view_ticket(ticket):
        return jsonify({'error': 'No autorizado'}), 403

    if request.method == 'POST':
        data    = request.get_json(silent=True) or {}
        mensaje = (data.get('mensaje') or '').strip()
        if not mensaje:
            return jsonify({'error': 'El mensaje no puede estar vacío'}), 400

        nuevo = MensajeTicket(ticket_id=ticket.id, usuario_id=current_user.id, mensaje=mensaje)
        db.session.add(nuevo)

        if current_user.id == ticket.usuario_id:
            if ticket.tecnico_id:
                notify_user(
                    ticket.tecnico_id,
                    'Nuevo mensaje del usuario',
                    f'{current_user.nombre} escribió en el ticket #{ticket.id}.',
                    ticket.id,
                )
            else:
                notify_admins(
                    'Mensaje en ticket sin asignar',
                    f'{current_user.nombre} escribió en el ticket #{ticket.id}.',
                    ticket.id,
                )
        else:
            notify_user(
                ticket.usuario_id,
                'Nuevo mensaje de soporte',
                f'{current_user.nombre} escribió en tu ticket #{ticket.id}.',
                ticket.id,
            )
            if is_admin() and ticket.tecnico_id and ticket.tecnico_id != current_user.id:
                notify_user(
                    ticket.tecnico_id,
                    'Nuevo mensaje del administrador',
                    f'{current_user.nombre} escribió en el ticket #{ticket.id}.',
                    ticket.id,
                )
            if is_assigned_tech(ticket):
                notify_admins(
                    'Soporte respondió un ticket',
                    f'{current_user.nombre} respondió el ticket #{ticket.id}.',
                    ticket.id,
                    exclude_user_id=current_user.id,
                )

        db.session.commit()
        return jsonify({'mensaje': mensaje_ticket_json(nuevo)}), 201

    mensajes = (
        MensajeTicket.query
        .filter_by(ticket_id=ticket.id)
        .order_by(MensajeTicket.fecha.asc())
        .all()
    )
    return jsonify({'mensajes': [mensaje_ticket_json(m) for m in mensajes]})


# ──────────────────────────────────────────────────────────────
# Notificaciones
# ──────────────────────────────────────────────────────────────

@tickets_bp.route('/api/notificaciones')
@login_required
def api_notificaciones():
    notificaciones = (
        Notificacion.query
        .filter_by(usuario_id=current_user.id)
        .order_by(Notificacion.fecha.desc())
        .limit(12)
        .all()
    )
    sin_leer = Notificacion.query.filter_by(usuario_id=current_user.id, leida=False).count()
    return jsonify({
        'sin_leer':       sin_leer,
        'notificaciones': [notification_json(n) for n in notificaciones],
    })


@tickets_bp.route('/api/notificaciones/marcar-leidas', methods=['POST'])
@login_required
def api_notificaciones_marcar_leidas():
    Notificacion.query.filter_by(usuario_id=current_user.id, leida=False).update(
        {Notificacion.leida: True},
        synchronize_session=False,
    )
    db.session.commit()
    return jsonify({'ok': True})
