import os
import json
import random
from datetime import date, datetime
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import qrcode
import requests
import xml.etree.ElementTree as ET
from functools import wraps
from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, flash, session, send_file,
    send_from_directory
)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from PyPDF2 import PdfMerger

from flask_sqlalchemy import SQLAlchemy
from models import db, Sorteo, Vendedor, Planilla, DailyOrder




# ─── APP CONFIG ──────────────────────────────────────────────
app = Flask(__name__)

# ─── SECRET KEY & SQLALCHEMY ─────────────────────────────────
app.config['SECRET_KEY'] = 'mI_ClAv3_UlTrA_s3CrEtA_4_bInG0_!@#'
app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:///sorteos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ─── INICIALIZAR EXTENSIONES ─────────────────────────────────
db.init_app(app)

# ─── CONTEXT PROCESSOR ───────────────────────────────────────
@app.context_processor
def inject_current_year():
    # Devuelve un entero, no una lambda
    return {'current_year': datetime.now().year}

# ─── CREAR TABLAS SI NO EXISTEN ──────────────────────────────
with app.app_context():
    db.create_all()

# ─── RUTAS y CONSTANTES ──────────────────────────────────────
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
USUARIOS_JSON    = os.path.join(BASE_DIR, 'usuarios.json')
VENDEDORES_JSON  = os.path.join(BASE_DIR, 'vendedores.json')
MOVIMIENTOS_JSON = os.path.join(BASE_DIR, 'movimientos.json')
SORTEOS_JSON     = os.path.join(BASE_DIR, 'sorteos.json')

DATA_DIR       = os.path.join(BASE_DIR, "data")
REINTEGROS_DIR = os.path.join(DATA_DIR, "REINTEGROS")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REINTEGROS_DIR, exist_ok=True)

# Parámetros de diseño…
MARGEN_IZQ, MARGEN_SUP = 20, 55
ESPACIO_X, ESPACIO_Y   = 60, 85
COLUMNAS, FILAS        = 2, 4
SIZE_NUM, SIZE_INFO    = 22, 9
REIN_W, REIN_H         = 40, 40

SERIE_MAP = {
    "Srs_ib1.xlsx":    "V",
    "Srs_ib2.xlsx":    "+",
    "Srs_ib3.xlsx":    "&",
    "Srs_Manila.xlsx": "M"
}

# ─── JSON UTILITIES ──────────────────────────────────────────
def _cargar(path, default):
    if not os.path.exists(path):
        return default
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def _guardar(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

cargar_usuarios     = lambda: _cargar(USUARIOS_JSON, {})
guardar_usuarios    = lambda u: _guardar(USUARIOS_JSON, u)
cargar_vendedores   = lambda: _cargar(VENDEDORES_JSON, {})
guardar_vendedores  = lambda v: _guardar(VENDEDORES_JSON, v)
cargar_movimientos  = lambda: _cargar(MOVIMIENTOS_JSON, [])
guardar_movimientos = lambda m: _guardar(MOVIMIENTOS_JSON, m)
cargar_sorteos      = lambda: _cargar(SORTEOS_JSON, [])
guardar_sorteos     = lambda s: _guardar(SORTEOS_JSON, s)

def obtener_reintegros():
    return sorted(f for f in os.listdir(REINTEGROS_DIR) if f.lower().endswith('.png'))

# ─── DECORADORES ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapped

def requiere_rol(*roles):
    def deco(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if session.get('rol') not in roles:
                flash('No tienes permisos.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return wrapped
    return deco

# ─── AUTH ────────────────────────────────────────────────────
@app.route('/', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form['usuario'].strip().upper()
        p = request.form['clave']
        us = cargar_usuarios()
        if u in us and us[u]['clave'] == p:
            session.update({
                'usuario': u,
                'rol':      us[u]['rol'],
                'avatar':   us[u].get('avatar','avatar-male.png'),
                'permisos': us[u].get('permisos', [])
            })
            return redirect(url_for('dashboard'))
        flash('Usuario o clave incorrectos','danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


    

# ─── DASHBOARD ───────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    # 1) Buscamos primero un sorteo marcado como activo
    sorteo = Sorteo.query.filter_by(activo=True).first()

    # 2) Si no hay ninguno activo, tomamos el último creado
    if not sorteo:
        sorteo = (
            Sorteo.query
                   .order_by(Sorteo.fecha_evento.desc(), Sorteo.id.desc())
                   .first()
        )

    # 3) Calculamos siempre las métricas (o devolvemos 0 si no existe ningún sorteo)
    if sorteo:
        boletos_vendidos  = Planilla.query.filter_by(sorteo_id=sorteo.id, vendido=True).count()
        boletos_devueltos = Planilla.query.filter_by(sorteo_id=sorteo.id, vendido=False).count()

        # Ajusta el cálculo según tu modelo: aquí asumo precio_boleto en Sorteo
        precio = sorteo.precio_boleto  
        ganancia_empresa = round(boletos_vendidos * (precio * 0.70), 2)
        ahorro           = round(boletos_vendidos * (precio * 0.30), 2)

        resumen_mensual = {
            'Vendido':  boletos_vendidos,
            'Devuelto': boletos_devueltos,
            'Empresa':  ganancia_empresa,
            'Gastos':   0,            # Si tienes gastos, sustituye este 0
            'Ahorro':   ahorro
        }
    else:
        boletos_vendidos = boletos_devueltos = 0
        ganancia_empresa = ahorro = 0
        resumen_mensual  = {
            'Vendido':0, 'Devuelto':0, 'Empresa':0, 'Gastos':0, 'Ahorro':0
        }

    # 4) Renderizamos el template pasándole siempre las variables
    return render_template(
        'dashboard.html',
        boletos_vendidos  = boletos_vendidos,
        boletos_devueltos = boletos_devueltos,
        ganancia_empresa  = ganancia_empresa,
        ahorro            = ahorro,
        resumen_mensual   = resumen_mensual
    )




# ─── USUARIOS ────────────────────────────────────────────────
@app.route('/gestion_usuarios')
@login_required
@requiere_rol('superadmin')
def gestion_usuarios():
    # le pasamos además usuario, rol y avatar a la plantilla
    return render_template('gestion_usuarios.html',
        usuarios=cargar_usuarios(),
        usuario=session['usuario'],
        rol=session['rol'],
        avatar=session['avatar']
    )

@app.route('/api/usuario/<u>', methods=['GET','DELETE'], endpoint='api_usuario')
@login_required
@requiere_rol('superadmin')
def api_usuario(u):
    us = cargar_usuarios()
    key = u.strip().upper()
    if request.method == 'GET':
        if key not in us:
            return jsonify(ok=False, msg='No existe'), 404
        d = us[key]; d['usuario'] = key
        return jsonify(ok=True, **d)
    us.pop(key, None)
    guardar_usuarios(us)
    return jsonify(ok=True)

@app.route('/api/crear_usuario', methods=['POST'], endpoint='api_crear_usuario')
@login_required
@requiere_rol('superadmin')
def api_crear_usuario():
    d = request.get_json(); us = cargar_usuarios()
    u = d['usuario'].strip().upper()
    if u in us:
        return jsonify(ok=False, msg='Existe'), 400
    us[u] = {
        'clave':    d['clave'],
        'rol':      d['rol'],
        'sexo':     d['sexo'],
        'avatar':   d['avatar'],
        'permisos': d.get('permisos', [])
    }
    guardar_usuarios(us)
    return jsonify(ok=True)

@app.route('/api/editar_usuario', methods=['POST'], endpoint='api_editar_usuario')
@login_required
@requiere_rol('superadmin')
def api_editar_usuario():
    d = request.get_json(); us = cargar_usuarios()
    u = d['usuario'].strip().upper()
    if u not in us:
        return jsonify(ok=False, msg='No existe'), 404
    us[u].update({
        'rol':      d['rol'],
        'sexo':     d['sexo'],
        'avatar':   d['avatar'],
        'permisos': d.get('permisos', [])
    })
    if d.get('clave'):
        us[u]['clave'] = d['clave']
    guardar_usuarios(us)
    return jsonify(ok=True)





@app.route('/cambiar_clave', methods=['GET','POST'], endpoint='cambiar_clave')
@login_required
def cambiar_clave():
    u = session['usuario']
    if request.method == 'POST':
        actual = request.form['actual']
        nueva  = request.form['nueva']
        us = cargar_usuarios()
        if us[u]['clave'] == actual:
            us[u]['clave'] = nueva
            guardar_usuarios(us)
            flash('¡Clave actualizada!','success')
            return redirect(url_for('dashboard'))
        flash('Clave actual incorrecta','danger')
    return render_template('cambiar_clave.html',
        usuario=u, rol=session['rol'], avatar=session['avatar']
    )




# ─── VENDEDORES ─────────────────────────────────────────────
@app.route('/vendedores', endpoint='vendedores')
@requiere_rol('superadmin','admin')
def vista_vendedores():
    return render_template('vendedores.html',
        vendedores=cargar_vendedores()
    )

@app.route('/api/crear_vendedor', methods=['POST'])
@requiere_rol('superadmin','admin')
def api_crear_vendedor():
    j = request.get_json(); d = cargar_vendedores()
    n = j['nombre'].strip().upper()
    if n in d:
        return jsonify(ok=False, msg='Existe'), 400
    d[n] = {'seudonimo': j['seudonimo'].strip()}
    guardar_vendedores(d)
    return jsonify(ok=True)

@app.route('/api/editar_vendedor', methods=['POST'])
@requiere_rol('superadmin','admin')
def api_editar_vendedor():
    j = request.get_json(); d = cargar_vendedores()
    n = j['nombre'].strip().upper()
    if n not in d:
        return jsonify(ok=False, msg='No existe'), 404
    d[n]['seudonimo'] = j['seudonimo'].strip()
    guardar_vendedores(d)
    return jsonify(ok=True)

@app.route('/api/eliminar_vendedor', methods=['POST'])
@requiere_rol('superadmin','admin')
def api_eliminar_vendedor():
    j = request.get_json(); d = cargar_vendedores()
    n = j['nombre'].strip().upper()
    d.pop(n, None)
    guardar_vendedores(d)
    return jsonify(ok=True)


#--------------------ASIGNACION DE PLANILLAS---------------#


@app.route('/asignacion_planillas', methods=['GET', 'POST'])
@login_required
@requiere_rol('superadmin','admin')    # pon aquí los roles que necesites
def asignacion_planillas():
    vendedores = Vendedor.query.order_by(Vendedor.nombre).all()
    fecha_hoy  = date.today().isoformat()

    if request.method == 'POST':
        vid    = request.form['vendedor']
        inicio = int(request.form['inicio'])
        fin    = int(request.form['fin'])
        fecha  = request.form['fecha_planilla']
        vend   = Vendedor.query.get_or_404(vid)

        for num in range(inicio, fin+1):
            p = Planilla(
                vendedor_id=vend.id,
                numero=num,
                fecha_asig=fecha,
                estado='asignada'
            )
            db.session.add(p)
        db.session.commit()
        flash(f'Asignadas planillas {inicio}–{fin} a {vend.nombre}', 'success')
        return redirect(url_for('asignacion_planillas'))

    asignaciones = Planilla.query.order_by(Planilla.fecha_asig.desc()).all()
    return render_template(
        'asignacion_planillas.html',
        vendedores=vendedores,
        fecha_hoy=fecha_hoy,
        asignaciones=asignaciones
    )




# ─── VMIX ────────────────────────────────────────────────────
@app.route('/config_vmix', methods=['GET','POST'])
@requiere_rol('superadmin','admin')
def config_vmix():
    cfg = os.path.join(BASE_DIR, 'config_vmix.json')
    ip, msg = '', ''
    if request.method == 'POST':
        ip = request.form.get('ip','')
        json.dump({'ip':ip}, open(cfg,'w'))
        msg = '¡IP guardada!'
    if os.path.exists(cfg):
        try:
            ip = json.load(open(cfg))['ip']
        except:
            ip = ''
    return render_template('config_vmix.html', ip=ip, mensaje=msg)

@app.route('/probar_vmix', methods=['POST'])
def probar_vmix():
    ip = request.get_json().get('ip','')
    try:
        r = requests.get(f'http://{ip}:8088/api/', timeout=2)
        if r.status_code == 200 and '<vmix>' in r.text.lower():
            return jsonify(ok=True)
    except: pass
    return jsonify(ok=False)

# ─── ÚLTIMOS MOVIMIENTOS ────────────────────────────────────
def obtener_pais_por_ip(ip):
    if ip == '127.0.0.1':
        return 'Localhost'
    try:
        r = requests.get(f'http://ipinfo.io/{ip}/country', timeout=2)
        if r.status_code == 200:
            return r.text.strip()
    except: pass
    return 'Desconocido'

def registrar_movimiento(u, accion, detalles=''):
    m = cargar_movimientos()
    ip = request.remote_addr
    p  = obtener_pais_por_ip(ip)
    m.append({
        'usuario' : u,
        'accion'  : accion,
        'detalles': detalles,
        'ip'      : ip,
        'pais'    : p,
        'fecha'   : datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    guardar_movimientos(m[-500:])

@app.route('/ultimos_movimientos')
@requiere_rol('superadmin','admin')
def ultimos_movimientos():
    m = cargar_movimientos(); m.reverse()
    return render_template('ultimos_movimientos.html', movimientos=m)



@app.route('/sorteos')
@requiere_rol('superadmin','admin')
def vista_sorteos():
    sorteos = Sorteo.query.order_by(Sorteo.fecha_evento.desc()).all()
    return render_template('sorteos.html', sorteos=sorteos)





# ─── (DESACTIVADO) CREAR SORTEO ───────────────────────────────────────────────
# @app.route('/sorteos/nuevo', methods=['GET', 'POST'])
# @requiere_rol('superadmin', 'admin')
# def nuevo_sorteo():
#     if request.method == 'POST':
#         try:
#             codigo     = request.form['codigo'].strip()
#             nombre     = request.form['nombre'].strip()
#             fecha_str  = request.form['fecha_evento']
#             premio_str = request.form['premio_gordo']
#             fecha_evento = datetime.strptime(fecha_str, '%Y-%m-%d').date()
#             premio_gordo = Decimal(premio_str)
#             s = Sorteo(
#                 codigo=codigo,
#                 nombre=nombre,
#                 fecha_evento=fecha_evento,
#                 premio_gordo=premio_gordo
#             )
#             db.session.add(s)
#             db.session.commit()
#             flash('Sorteo creado correctamente', 'success')
#             return redirect(url_for('vista_sorteos'))
#         except Exception as e:
#             db.session.rollback()
#             flash(f'Error al crear sorteo: {e}', 'danger')
#             return redirect(url_for('vista_sorteos'))
#
#     # GET → render_template('nuevo_sorteo.html', …)









# ─── CONTABILIDAD ──────────────────────────────────────────
@app.route('/contabilidad')
@requiere_rol('admin','superadmin')
def contabilidad():
    return render_template('contabilidad.html',
        usuario=session['usuario'],
        rol=session['rol'],
        avatar=session['avatar'],
        permisos=session['permisos']
    )

# ─── JUEGO ──────────────────────────────────────────────────
@app.route('/jugar', endpoint='jugar')
@app.route('/juego', endpoint='juego')
@requiere_rol('operador','admin','superadmin')
def juego():
    return render_template(
        'jugar.html',
        usuario=session['usuario'],
        rol=session['rol'],
        avatar=session['avatar'],
        permisos=session['permisos']
    )
# ─── REDIRECCIÓN + IMPRESIÓN ─────────────────────────────────

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.path.join(BASE_DIR, "data")
REINTEGROS_DIR = os.path.join(DATA_DIR, "REINTEGROS")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REINTEGROS_DIR, exist_ok=True)

MARGEN_IZQ     = 20
MARGEN_SUP     = 60
ESPACIO_X      = 65
ESPACIO_Y      = 120
COLUMNAS       = 2
FILAS          = 4

SIZE_NUM       = 20
SIZE_INFO      = 13
SIZE_VALOR     = 18
REINTEGRO_W    = 50
REINTEGRO_H    = 50

DELTA_Y_FILA_3 = 2
DELTA_Y_FILA_4 = 5

SERIE_MAP = {
    "Srs_ib1.xlsx":    "V",
    "Srs_ib2.xlsx":    "+",
    "Srs_ib3.xlsx":    "&",
    "Srs_Manila.xlsx": "M"
}

per_cell_offsets = {
    0: {"grid_x":-15,"grid_y":  30, "info_x": -20, "info_y":  25, "tel_x": 165, "tel_y":  25, "rein_x": 190, "rein_y":  25},
    1: {"grid_x":-55, "grid_y":  30, "info_x":-70, "info_y":  25, "tel_x":125,"tel_y":  25, "rein_x": 150, "rein_y":  25},
    2: {"grid_x": -15, "grid_y":  90, "info_x": -20, "info_y":  85, "tel_x": 165, "tel_y":  85, "rein_x": 190, "rein_y": -25},
    3: {"grid_x":-55, "grid_y":  90, "info_x": -70, "info_y":  85, "tel_x": 125, "tel_y":  85, "rein_x": 150, "rein_y": -30},
    4: {"grid_x":-15, "grid_y": 150, "info_x":  -20, "info_y": 145, "tel_x": 165, "tel_y": 145, "rein_x": 190, "rein_y": -85},
    5: {"grid_x": -55, "grid_y": 150, "info_x": -70, "info_y": 145, "tel_x": 125, "tel_y": 145, "rein_x": 150, "rein_y": -85},
    6: {"grid_x":-15, "grid_y": 210, "info_x":  -20, "info_y": 205, "tel_x": 165, "tel_y": 205, "rein_x": 190, "rein_y": -150},
    7: {"grid_x": -55, "grid_y": 210, "info_x": -70, "info_y": 205, "tel_x": 125, "tel_y": 205, "rein_x": 155, "rein_y": -150},
}




# ─── RUTA IMPRESIÓN ─────────────────────────────────────────────────────────
@app.route('/impresion', methods=['GET', 'POST'], endpoint='impresion')
@login_required
def impresion():
    # ── Listado de series y reintegros disponibles ──
    files      = sorted(f for f in os.listdir(DATA_DIR) if f.lower().endswith(('.xlsx', '.csv')))
    series     = [(f, SERIE_MAP.get(f, f)) for f in files]
    reintegros = sorted(f for f in os.listdir(REINTEGROS_DIR) if f.lower().endswith('.png'))
    fecha_hoy  = date.today().strftime('%Y-%m-%d')

    if request.method == 'POST':
        form_type = request.form.get('form_type')

        # ── BOLETOS ─────────────────────────────────────────────
        if form_type == 'boletos':
            # Parámetros del formulario
            nombre     = request.form['serie_archivo']
            start      = request.form.get('serie_inicio', '')
            end        = request.form.get('serie_fin', '')
            valor      = request.form['valor']
            telefono   = request.form['telefono']
            fecha_str  = request.form.get('fecha_sorteo', fecha_hoy)
            rein_esp   = request.form.get('reintegro_especial', '')
            cnt_esp    = int(request.form.get('cant_reintegro_especial', 0))
            incA       = (request.form.get('incluir_aleatorio', '1') == '1')

            # Cargo DataFrame y lista de IDs
            path = os.path.join(DATA_DIR, nombre)
            if nombre.lower().endswith('.csv'):
                df = pd.read_csv(path, dtype=str).fillna('')
            else:
                df = pd.read_excel(path, dtype=str).fillna('')
            all_ids = df[df.columns[0]].astype(str).tolist()

            # Índices según rango
            try:
                s_idx = all_ids.index(start) if start else 0
            except ValueError:
                flash(f'Boleto inicial “{start}” no existe.', 'danger')
                return redirect(url_for('impresion'))
            try:
                e_idx = all_ids.index(end) + 1 if end else len(all_ids)
            except ValueError:
                flash(f'Boleto final “{end}” no existe.', 'danger')
                return redirect(url_for('impresion'))

            ids       = all_ids[s_idx:e_idx]
            registros = df[df[df.columns[0]].astype(str).isin(ids)].to_dict('records')

            # Generar PDF de boletos
            buf_b = generar_pdf_boletos_excel(
                ids, registros, valor, telefono,
                nombre, rein_esp, cnt_esp,
                reintegros, incA, fecha_str
            )
            return send_file(buf_b, download_name='boletos_bingo.pdf', as_attachment=True)

        # ── PLANILLA ───────────────────────────────────────────
        elif form_type == 'planilla':
            archivo = request.form['serie_archivo_planilla']
            inicio  = int(request.form['planilla_inicio'])
            fin     = int(request.form['planilla_fin'])
            fecha_p = request.form.get('fecha_planilla', fecha_hoy)

            path = os.path.join(DATA_DIR, archivo)
            if archivo.lower().endswith('.csv'):
                df2 = pd.read_csv(path, dtype=str).fillna('')
            else:
                df2 = pd.read_excel(path, dtype=str).fillna('')
            all_ids = df2[df2.columns[0]].astype(str).tolist()
            ids_p   = all_ids[inicio-1:fin]

            merger = PdfMerger()
            for offset in range(0, len(ids_p), 40):
                sub_ids    = ids_p[offset:offset+40]
                start_b    = inicio + offset
                end_b      = min(start_b + len(sub_ids) - 1, fin)
                buf = generar_pdf_planilla(
                    sub_ids, archivo, session['usuario'],
                    fecha_p, start_b, end_b, SERIE_MAP
                )
                merger.append(buf)

            salida = BytesIO()
            merger.write(salida)
            salida.seek(0)
            return send_file(salida,
                             download_name=f'planilla_{inicio}_a_{fin}.pdf',
                             as_attachment=True)

        # ── ZIP (ambos) ────────────────────────────────────────
        elif form_type == 'zip':
            # ... tu lógica de ZIP aquí ...
            pass

    # ─ GET / formulario ───────────────────────────────────────
    return render_template(
        'impresion_boletos_excel.html',
        series=series,
        reintegros=reintegros,
        fecha_hoy=fecha_hoy,
        usuario=session.get('usuario'),
        rol=session.get('rol'),
        avatar=session.get('avatar'),
        permisos=session.get('permisos')
    )

# ─── GENERAR PDF BOLETOS ─────────────────────────────────────
def generar_pdf_boletos_excel(
    ids, registros, valor, telefono,
    nombre, rein_esp, cnt_esp,
    reintegros, incA, fecha_sorteo
):
    buf = BytesIO()
    c   = canvas.Canvas(buf, pagesize=A4)
    ancho, alto = A4
    N = len(registros)

    esp_idx = random.sample(range(N), min(N, cnt_esp)) if rein_esp else []
    ale_idx = [i for i in range(N) if i not in esp_idx] if incA else []

    for start in range(0, N, FILAS*COLUMNAS):
        page = registros[start:start+FILAS*COLUMNAS]
        for i, row in enumerate(page):
            pos = start + i
            col = i % COLUMNAS
            fil = i // COLUMNAS

            ancho_b = (ancho + 2*MARGEN_IZQ - ESPACIO_X*(COLUMNAS-1)) / COLUMNAS
            alto_b  = (alto  + 2*MARGEN_SUP - ESPACIO_Y*(FILAS-1))   / FILAS
            x0 = MARGEN_IZQ + col*(ancho_b + ESPACIO_X)
            y0 = alto - MARGEN_SUP - fil*(alto_b + ESPACIO_Y)
            if fil == 2: y0 -= DELTA_Y_FILA_3
            if fil == 3: y0 -= DELTA_Y_FILA_4

            size = min(ancho_b, alto_b) / 5
            offs = per_cell_offsets[i]

            # — Dibujo de la rejilla 5×5 y QR en N3 —
            bx0 = x0 + offs['grid_x']
            by0 = y0 + offs['grid_y']
            c.setFont('Helvetica-Bold', SIZE_NUM)
            for r in range(5):
                for j, letra in enumerate('bingo'):
                    cx = bx0 + j*size
                    cy = by0 - r*size
                    if letra=='n' and r==2:
                        qr = qrcode.make(f"{ids[pos]}|{fecha_sorteo}")
                        buf_qr = BytesIO(); qr.save(buf_qr,'PNG'); buf_qr.seek(0)
                        c.drawImage(ImageReader(buf_qr),
                                    cx+2, cy+2, size-4, size-4)
                    else:
                        v = str(row.get(f"{letra}{r+1}", "-"))
                        c.drawCentredString(cx+size/2, cy+size*0.28, v)

            # — Texto de serie, fecha y valor —
            info = f"{ids[pos]}{SERIE_MAP.get(nombre,nombre)} | {fecha_sorteo} | ${valor}"
            c.setFont('Helvetica', SIZE_INFO)
            c.drawString(
                x0 + offs['info_x'],
                y0 - size*5 + offs['info_y'],
                info
            )

            # — Texto de teléfono separado —
            tel = f"Tel: {telefono}"
            c.setFont('Helvetica', SIZE_INFO)
            c.drawString(
                x0 + offs['tel_x'],
                y0 - size*5 + offs['tel_y'],
                tel
            )

            # — Icono de reintegro —
            img_file = None
            if pos in esp_idx and rein_esp:
                img_file = rein_esp
            elif pos in ale_idx and reintegros:
                otros = [r for r in reintegros if r != rein_esp]
                img_file = random.choice(otros) if otros else None

            if img_file:
                c.drawImage(
                    ImageReader(os.path.join(REINTEGROS_DIR, img_file)),
                    x0 + offs['rein_x'], y0 - offs['rein_y'],
                    REINTEGRO_W, REINTEGRO_H,
                    mask='auto'
                )

        c.showPage()

    c.save()
    buf.seek(0)
    return buf


def generar_pdf_planilla(ids, serie_archivo, vendedor, fecha, inicio, fin, serie_map, num_planilla=None):
    from io import BytesIO
    from datetime import datetime
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    import os, qrcode

    # — Formatear fecha en español —
    dt = datetime.strptime(fecha, "%Y-%m-%d")
    dias   = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
    meses  = {
        1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",
        5:"Mayo",6:"Junio",7:"Julio",8:"Agosto",
        9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"
    }
    formatted_date = f"{dias[dt.weekday()]}, {dt.day} de {meses[dt.month]} del {dt.year}"
    fecha_limpia   = dt.strftime("%Y%m%d")
    serie_letra    = serie_map.get(serie_archivo, "")

    # — Rangos —
    left_desde  = inicio
    left_hasta  = min(inicio + 19, fin)
    right_desde = inicio + 20
    right_hasta = min(inicio + 39, fin)
    full_desde  = inicio
    full_hasta  = min(inicio + 39, fin)

    # — Generadores de cadena QR —
    def qr_cadena(tipo, desde, hasta, serie):
        return f"SORTEO{fecha_limpia}{tipo}A{desde}A{hasta}{serie}"

    # — Preparar canvas —
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    ancho, alto = landscape(A4)

    # — Márgenes y constantes —
    M_LEFT, M_RIGHT, M_BOTTOM = 20, 20, 20
    GUTTER, HEADER_H, QR_SIZE = 20, 60, 40

    # — Área útil para tablas —
    HALF_W  = (ancho - M_LEFT - M_RIGHT - GUTTER) / 2
    TOP_Y   = alto - HEADER_H - 5
    BOT_Y   = M_BOTTOM
    AVAIL_H = TOP_Y - BOT_Y

    # — Filas y altura dinámica —
    NUM_ROWS = 21
    ROW_H    = AVAIL_H / NUM_ROWS

    # — Posiciones X y ancho de tabla —
    X_L, X_R = M_LEFT, M_LEFT + HALF_W + GUTTER
    TABLE_W  = HALF_W - 20
    PAD      = 10

    # — Recursos y fuentes —
    LOGO_PATH = os.path.join("static","golpe_suerte_logo.png")
    FB, FR    = "Helvetica-Bold", "Helvetica"

    # — Índices de planilla según rango —
    left_index  = (left_desde - 1) // 20 + 1
    right_index = (right_desde - 1) // 20 + 1

    # — Función: dibuja header en x0 con QR de su rango —
    def draw_header(x0, sheet_num, tipo, desde, hasta):
        # fondo gris
        c.setFillColorRGB(0.9,0.9,0.9)
        c.rect(x0, alto - HEADER_H, HALF_W, HEADER_H, fill=1, stroke=0)
        c.setFillColor(colors.black)

        # logo grande manteniendo proporción
        img = ImageReader(LOGO_PATH)
        ow, oh = img.getSize()
        dh = HEADER_H - 4
        dw = dh * ow / oh
        c.drawImage(img,
                    x0 + 8,
                    alto - HEADER_H + 2,
                    width=dw, height=dh,
                    mask="auto")

        # recuadros de fecha
        box_w = HALF_W * 0.45
        box_h = 20
        bx = x0 + (HALF_W - box_w) / 2
        by = alto - HEADER_H + 8
        c.setLineWidth(1.5)
        c.setFillColor(colors.white)
        c.roundRect(bx, by + box_h + 4, box_w, box_h, 4, stroke=1, fill=1)  # vacío arriba
        c.roundRect(bx, by,               box_w, box_h, 4, stroke=1, fill=1)  # fecha
        c.setFillColor(colors.black)
        c.setFont(FB, 10)
        c.drawCentredString(bx + box_w/2, by + box_h/2 - 4, formatted_date)

        # QR de rango para esta mitad
        data_qr = qr_cadena(tipo, desde, hasta, serie_letra)
        buf = BytesIO(); qrcode.make(data_qr).save(buf,format="PNG"); buf.seek(0)
        qx = x0 + HALF_W - QR_SIZE - 8
        qy = alto - HEADER_H + 8
        c.drawImage(ImageReader(buf), qx, qy, QR_SIZE, QR_SIZE)

        # recuadro del número a la izquierda del QR
        pn_w, pn_h = 36, 28
        px = qx - pn_w - 6
        py = qy + (QR_SIZE - pn_h)/2
        c.setFillColor(colors.white)
        c.setLineWidth(1.5)
        c.roundRect(px, py, pn_w, pn_h, 4, stroke=1, fill=1)
        c.setFillColor(colors.black)
        c.setFont(FB, 18)
        c.drawCentredString(px + pn_w/2, py + pn_h/2 - 5, str(sheet_num))

    # — Dibujar headers izquierdo y derecho con sus propios QR —
    draw_header(X_L, left_index,  "L1", left_desde,  left_hasta)
    draw_header(X_R, right_index, "L2", right_desde, right_hasta)

    # — Línea divisoria central —
    c.setLineWidth(2)
    c.line(X_R, TOP_Y, X_R, BOT_Y)

    # — QR central de rango completo (40 boletos) —
    data_full = qr_cadena("RG", full_desde, full_hasta, serie_letra)
    buf2 = BytesIO(); qrcode.make(data_full).save(buf2,format="PNG"); buf2.seek(0)
    mid_y = BOT_Y + (AVAIL_H/2) - (QR_SIZE/2)
    c.drawImage(ImageReader(buf2),
                ancho/2 - QR_SIZE/2,
                mid_y,
                QR_SIZE, QR_SIZE)

    # — Construir siempre 21 filas por tabla —
    left_data = [["Boleto / Nombres Apellidos",""]]
    for i in range(20):
        n = inicio + i
        left_data.append([str(n) if n <= fin else "", ""])
    right_data = [["Boleto / Nombres Apellidos",""]]
    for i in range(20):
        n = inicio + 20 + i
        right_data.append([str(n) if n <= fin else "", ""])

    # — Recuadro en encabezado de tabla —
    header_y = TOP_Y - ROW_H
    c.setLineWidth(1.5)
    c.roundRect(X_L + PAD, header_y, TABLE_W, ROW_H, 4, stroke=1, fill=0)
    c.roundRect(X_R + PAD, header_y, TABLE_W, ROW_H, 4, stroke=1, fill=0)

    # — Estilo de tabla —
    from reportlab.platypus import Table
    style = TableStyle([
        ("SPAN",        (0,0),(1,0)),
        ("FONT",        (0,0),(1,0), FB, 10),
        ("ALIGN",       (0,0),(1,0),"CENTER"),
        ("FONT",        (0,1),(0,-1), FB, 12),
        ("FONT",        (1,1),(1,-1), FR, 8),
        ("VALIGN",      (0,0),(-1,-1),"MIDDLE"),
        ("INNERGRID",   (0,0),(-1,-1),1,colors.black),
        ("BOX",         (0,0),(-1,-1),2,colors.black),
        ("LEFTPADDING", (0,0),(-1,-1),3),
        ("RIGHTPADDING",(0,0),(-1,-1),3),
    ])

    # — Renderizar tablas —
    tblL = Table(left_data,  colWidths=[40, TABLE_W-40], rowHeights=[ROW_H]*NUM_ROWS)
    tblL.setStyle(style); tblL.wrapOn(c,0,0); tblL.drawOn(c, X_L+PAD, BOT_Y)
    tblR = Table(right_data, colWidths=[40, TABLE_W-40], rowHeights=[ROW_H]*NUM_ROWS)
    tblR.setStyle(style); tblR.wrapOn(c,0,0); tblR.drawOn(c, X_R+PAD, BOT_Y)

    c.save()
    buffer.seek(0)
    return buffer




from flask import send_from_directory

@app.route('/reintegros/<path:filename>')
def reintegros_static(filename):
    return send_from_directory(os.path.join(DATA_DIR, 'REINTEGROS'), filename)




# ─── STUBS PARA LAS OPCIONES DEL SIDEBAR ─────────────────────────

# ─── STUBS PARA LAS OPCIONES DEL SIDEBAR QUE FALTAN ────────────

@app.route('/config_boletos')
@login_required
def config_boletos():
    return render_template('config_boletos.html')

@app.route('/impresion_boletos_excel')
@login_required
def impresion_boletos_excel():
    return render_template('impresion_boletos_excel.html')

@app.route('/registrar')
@login_required
@requiere_rol('superadmin')
def registrar():
    return render_template('registrar.html')



@app.route('/historial_sorteos')
@login_required
def historial_sorteos():
    # Si en el futuro necesitas pasar datos, cámbialo aquí.
    # Por ejemplo: sorteos = Sorteo.query.order_by(...).all()
    return render_template('historial_sorteos.html')


# ─── FIN DE STUBS ──────────────────────────────────────────────

# ─── ERROR 404 ───────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

# ─── RUN SERVER ─────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True)

















