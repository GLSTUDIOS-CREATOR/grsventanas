from flask_sqlalchemy import SQLAlchemy

# Instancia única de la extensión
db = SQLAlchemy()

class Sorteo(db.Model):
    __tablename__ = 'sorteos'

    id            = db.Column(db.Integer, primary_key=True)
    codigo        = db.Column(db.String(20), unique=True, nullable=False)
    nombre        = db.Column(db.String(100), nullable=False)
    fecha_evento  = db.Column(db.Date, nullable=False)
    precio_boleto = db.Column(db.Numeric(10, 2), nullable=False)
    plantilla     = db.Column(db.String(50), nullable=True)
    activo        = db.Column(db.Boolean, default=False)
    generado      = db.Column(db.Boolean, default=False)

    # NUEVO CAMPO PARA CORREGIR EL ERROR
    premio_gordo  = db.Column(db.String(100), nullable=True)

    # Relación con planillas y órdenes
    planillas = db.relationship(
        'Planilla',
        back_populates='sorteo',
        cascade='all, delete-orphan'
    )
    orders = db.relationship(
        'DailyOrder',
        back_populates='sorteo',
        cascade='all, delete-orphan'
    )

    def __repr__(self):
        return f"<Sorteo {self.codigo} - {self.nombre}>"


class Vendedor(db.Model):
    __tablename__ = 'vendedores'

    id        = db.Column(db.Integer, primary_key=True)
    nombre    = db.Column(db.String(100), nullable=False)

    # Relación inversa: un vendedor puede tener varias planillas
    planillas = db.relationship(
        'Planilla',
        back_populates='vendedor',
        cascade='all, delete-orphan'
    )

    def __repr__(self):
        return f"<Vendedor {self.nombre}>"


class Planilla(db.Model):
    __tablename__ = 'planillas'

    id          = db.Column(db.Integer, primary_key=True)
    vendedor_id = db.Column(db.Integer, db.ForeignKey('vendedores.id'), nullable=False)
    sorteo_id   = db.Column(db.Integer, db.ForeignKey('sorteos.id'), nullable=False)
    numero      = db.Column(db.Integer, nullable=False)
    fecha_asig  = db.Column(db.Date, nullable=False)
    vendido     = db.Column(db.Boolean, default=False, nullable=False)

    # Relaciones
    vendedor = db.relationship(
        'Vendedor',
        back_populates='planillas'
    )
    sorteo   = db.relationship(
        'Sorteo',
        back_populates='planillas'
    )

    def __repr__(self):
        return (
            f"<Planilla {self.numero} vend:{self.vendedor_id} "
            f"sorteo:{self.sorteo_id} vendido:{self.vendido} on {self.fecha_asig}>"
        )


class DailyOrder(db.Model):
    __tablename__ = 'daily_orders'

    id           = db.Column(db.Integer, primary_key=True)
    sorteo_id    = db.Column(db.Integer, db.ForeignKey('sorteos.id'), nullable=False)
    date         = db.Column(db.Date, nullable=False)
    first_ticket = db.Column(db.String(50), nullable=False)
    last_ticket  = db.Column(db.String(50), nullable=False)
    impresos     = db.Column(db.Integer, default=0, nullable=False)
    vendidos     = db.Column(db.Integer, default=0, nullable=False)
    devoluciones = db.Column(db.Integer, default=0, nullable=False)

    # Relación inversa al sorteo
    sorteo = db.relationship(
        'Sorteo',
        back_populates='orders'
    )

    def __repr__(self):
        return (
            f"<DailyOrder {self.date} sorteo:{self.sorteo_id} "
            f"first:{self.first_ticket} last:{self.last_ticket} "
            f"imp:{self.impresos} vend:{self.vendidos} dev:{self.devoluciones}>"
        )


