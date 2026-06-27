"""
Blueprint de administración: gestión de usuarios, asignación/eliminación
de tickets y estadísticas.
"""
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify,
)
from flask_login import login_required, current_user
from sqlalchemy import func

from database import db, Usuario, Ticket, MensajeIA, MensajeTicket, Notificacion
from utils.constants import ADMIN_ROLE, TECH_ROLE, USER_ROLE
from utils.helpers import (
    is_admin, tecnicos_activos, notify_user, notify_admins,
    ticket_json, usuario_json, set_ticket_status,
)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _require_admin():
    """Redirige si el usuario no es admin. Retorna True si debe continuar."""
    if not is_admin():
        return False
    return True


# ──────────────────────────────────────────────────────────────
# Gestión de tickets
# ──────────────────────────────────────────────────────────────

@admin_bp.route('/ticket/<int:ticket_id>/asignar', methods=['POST'])
@login_required
def asignar_ticket(ticket_id):
    if not _require_admin():
        return redirect(url_for('tickets.dashboard_usuario'))

    ticket     = Ticket.query.get_or_404(ticket_id)
    tecnico_id = request.form.get('tecnico_id', type=int)

    if not tecnico_id:
        ticket.tecnico_id = None
        if ticket.estado == 'En Proceso':
            ticket.estado = 'Pendiente'
        notify_user(
            ticket.usuario_id,
            'Ticket sin técnico asignado',
            f'Tu ticket #{ticket.id} quedó pendiente de reasignación.',
            ticket.id,
        )
        db.session.commit()
        flash('Ticket dejado sin asignar.', 'success')
        return redirect(request.referrer or url_for('tickets.ver_ticket', ticket_id=ticket.id))

    tecnico = Usuario.query.filter_by(id=tecnico_id, rol=TECH_ROLE, activo=True).first()
    if not tecnico:
        flash('Selecciona un técnico válido para asignar el ticket.', 'danger')
        return redirect(url_for('tickets.ver_ticket', ticket_id=ticket.id))

    ticket.tecnico_id = tecnico.id
    if ticket.estado == 'Pendiente':
        ticket.estado = 'En Proceso'

    notify_user(
        tecnico.id,
        'Ticket asignado',
        f'Se te asignó el ticket #{ticket.id}: {ticket.titulo}',
        ticket.id,
    )
    notify_user(
        ticket.usuario_id,
        'Técnico asignado',
        f'{tecnico.nombre} fue asignado a tu ticket #{ticket.id}.',
        ticket.id,
    )
    db.session.commit()
    flash(f'Ticket asignado a {tecnico.nombre}.', 'success')
    return redirect(request.referrer or url_for('tickets.ver_ticket', ticket_id=ticket.id))


@admin_bp.route('/ticket/<int:ticket_id>/eliminar', methods=['POST'])
@login_required
def eliminar_ticket(ticket_id):
    if not _require_admin():
        return redirect(url_for('tickets.dashboard_usuario'))

    ticket = Ticket.query.get_or_404(ticket_id)
    titulo = ticket.titulo
    MensajeIA.query.filter_by(ticket_id=ticket.id).delete()
    MensajeTicket.query.filter_by(ticket_id=ticket.id).delete()
    Notificacion.query.filter_by(ticket_id=ticket.id).delete()
    db.session.delete(ticket)
    db.session.commit()
    flash(f'Ticket "{titulo}" eliminado.', 'success')
    return redirect(url_for('tickets.dashboard_tecnico'))


# ──────────────────────────────────────────────────────────────
# Gestión de usuarios
# ──────────────────────────────────────────────────────────────

@admin_bp.route('/usuarios')
@login_required
def usuarios():
    if not _require_admin():
        return redirect(url_for('tickets.dashboard_usuario'))
    all_usuarios = Usuario.query.order_by(Usuario.fecha_registro.desc()).all()
    return render_template('admin_usuarios.html', usuarios=all_usuarios)


@admin_bp.route('/api/usuarios')
@login_required
def api_usuarios():
    if not is_admin():
        return jsonify({'error': 'No autorizado'}), 403

    all_usuarios = Usuario.query.order_by(Usuario.fecha_registro.desc()).all()
    usuarios_rol = sum(1 for u in all_usuarios if u.rol == USER_ROLE)
    tecnicos_rol = sum(1 for u in all_usuarios if u.rol == TECH_ROLE)
    admins_rol   = sum(1 for u in all_usuarios if u.rol == ADMIN_ROLE)

    return jsonify({
        'usuarios': [usuario_json(u) for u in all_usuarios],
        'stats': {
            'total':    len(all_usuarios),
            'usuarios': usuarios_rol,
            'tecnicos': tecnicos_rol,
            'admins':   admins_rol,
        },
    })


@admin_bp.route('/usuario/crear', methods=['POST'])
@login_required
def crear_usuario():
    if not _require_admin():
        return redirect(url_for('tickets.dashboard_usuario'))
    flash('Los usuarios deben registrarse desde el formulario público. Luego usa "Hacer técnico".', 'warning')
    return redirect(url_for('admin.usuarios'))


@admin_bp.route('/usuario/<int:user_id>/hacer-tecnico', methods=['POST'])
@login_required
def hacer_tecnico(user_id):
    if not _require_admin():
        return redirect(url_for('tickets.dashboard_usuario'))

    usuario = Usuario.query.get_or_404(user_id)
    if usuario.rol == ADMIN_ROLE:
        flash('No se puede cambiar el rol de un administrador desde esta acción.', 'danger')
        return redirect(url_for('admin.usuarios'))

    usuario.rol              = TECH_ROLE
    usuario.activo           = True
    usuario.email_verificado = True
    notify_user(
        usuario.id,
        'Ahora eres técnico de soporte',
        'El administrador te habilitó como técnico. Ya puedes atender tickets asignados.',
    )
    db.session.commit()
    flash(f'{usuario.nombre} ahora es técnico de soporte.', 'success')
    return redirect(url_for('admin.usuarios'))


@admin_bp.route('/usuario/<int:user_id>/quitar-tecnico', methods=['POST'])
@login_required
def quitar_tecnico(user_id):
    if not _require_admin():
        return redirect(url_for('tickets.dashboard_usuario'))

    usuario = Usuario.query.get_or_404(user_id)
    if usuario.rol != TECH_ROLE:
        flash('Ese usuario no es técnico.', 'warning')
        return redirect(url_for('admin.usuarios'))

    tickets_abiertos = Ticket.query.filter(
        Ticket.tecnico_id == usuario.id,
        Ticket.estado.in_(['Pendiente', 'En Proceso']),
    ).all()
    for ticket in tickets_abiertos:
        ticket.tecnico_id = None
        ticket.estado     = 'Pendiente'
        notify_user(
            ticket.usuario_id,
            'Ticket pendiente de reasignación',
            f'Tu ticket #{ticket.id} quedó pendiente de un nuevo técnico.',
            ticket.id,
        )

    usuario.rol = USER_ROLE
    notify_user(
        usuario.id,
        'Rol de técnico retirado',
        'El administrador retiró tu rol de técnico de soporte.',
    )
    db.session.commit()
    flash(f'{usuario.nombre} volvió a rol usuario.', 'success')
    return redirect(url_for('admin.usuarios'))


@admin_bp.route('/usuario/<int:user_id>/eliminar', methods=['POST'])
@login_required
def eliminar_usuario(user_id):
    if not _require_admin():
        return redirect(url_for('tickets.dashboard_usuario'))

    usuario = Usuario.query.get_or_404(user_id)
    if usuario.id == current_user.id:
        flash('No puedes eliminar tu propia cuenta.', 'danger')
        return redirect(url_for('admin.usuarios'))

    nombre = usuario.nombre
    db.session.delete(usuario)
    db.session.commit()
    flash(f'Usuario "{nombre}" eliminado.', 'success')
    return redirect(url_for('admin.usuarios'))


# ──────────────────────────────────────────────────────────────
# Estadísticas
# ──────────────────────────────────────────────────────────────

@admin_bp.route('/estadisticas')
@login_required
def estadisticas():
    if not _require_admin():
        return redirect(url_for('tickets.dashboard_usuario'))

    por_categoria = db.session.query(Ticket.categoria, func.count(Ticket.id)).group_by(Ticket.categoria).all()
    por_estado    = db.session.query(Ticket.estado,    func.count(Ticket.id)).group_by(Ticket.estado).all()
    por_prioridad = db.session.query(Ticket.prioridad, func.count(Ticket.id)).group_by(Ticket.prioridad).all()
    total         = Ticket.query.count()

    return render_template(
        'estadisticas.html',
        por_categoria=por_categoria,
        por_estado=por_estado,
        por_prioridad=por_prioridad,
        total=total,
    )
