from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class Usuario(UserMixin, db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    rol = db.Column(db.String(20), nullable=False)
    area = db.Column(db.String(100))
    activo = db.Column(db.Boolean, default=True, nullable=True)
    email_verificado = db.Column(db.Boolean, default=False, nullable=True)
    fecha_registro = db.Column(db.DateTime, default=datetime.utcnow)

    tickets_creados = db.relationship('Ticket', foreign_keys='Ticket.usuario_id', backref='creador', lazy=True)
    tickets_asignados = db.relationship('Ticket', foreign_keys='Ticket.tecnico_id', backref='tecnico', lazy=True)

class Ticket(db.Model):
    __tablename__ = 'tickets'
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    categoria = db.Column(db.String(50))
    estado = db.Column(db.String(20), default='Pendiente')
    prioridad = db.Column(db.String(20), default='Media')
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_resolucion = db.Column(db.DateTime, nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    tecnico_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    conversacion_ia = db.Column(db.Text, nullable=True)
    informe_cierre = db.Column(db.Text, nullable=True)
    informe_fecha = db.Column(db.DateTime, nullable=True)

class MensajeIA(db.Model):
    __tablename__ = 'mensajes_ia'
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    usuario_pregunta = db.Column(db.Text, nullable=False)
    ia_respuesta = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class MensajeTicket(db.Model):
    __tablename__ = 'mensajes_ticket'
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    mensaje = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

    usuario = db.relationship('Usuario', backref='mensajes_ticket', lazy=True)

class Notificacion(db.Model):
    __tablename__ = 'notificaciones'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=True)
    titulo = db.Column(db.String(140), nullable=False)
    mensaje = db.Column(db.Text, nullable=False)
    leida = db.Column(db.Boolean, default=False, nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

    usuario = db.relationship('Usuario', backref='notificaciones', lazy=True)
    ticket = db.relationship('Ticket', backref='notificaciones', lazy=True)
