import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request, send_file, send_from_directory

# Ejemplo: export DATABASE_URL="postgresql://localhost/lab_monitor"
DATABASE_URL = os.environ["DATABASE_URL"]

# Folder where the detector (ppe_detector.py --save-dir) saves alert photos.
# Photos are only served from here, whatever path the database holds.
FOTOS_DIR = Path(os.environ.get("SENTRY_FOTOS_DIR", "/srv/grupo_iot/eiad-sentry/out")).resolve()

app = Flask(__name__, static_folder="static", static_url_path="/static")


def consulta(sql, params=None, uno=False, commit=False):
    """Abre conexión, ejecuta y devuelve filas como diccionarios."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        cur = conn.execute(sql, params or ())
        if commit:
            conn.commit()
        if cur.description is None:
            return None
        return cur.fetchone() if uno else cur.fetchall()


@app.get("/")
def inicio():
    return send_from_directory("static", "index.html")


@app.get("/api/en-laboratorio")
def en_laboratorio():
    return jsonify(consulta("""
        SELECT p.nombre, a.entrada FROM accesos a
        JOIN personas p ON p.id = a.persona_id
        WHERE a.salida IS NULL ORDER BY a.entrada"""))


@app.get("/api/historial")
def historial():
    return jsonify(consulta("""
        SELECT p.nombre, a.entrada, a.salida FROM accesos a
        JOIN personas p ON p.id = a.persona_id
        ORDER BY a.entrada DESC LIMIT 200"""))


@app.get("/api/reportes")
def reportes():
    return jsonify(consulta(
        "SELECT descripcion, creado FROM reportes ORDER BY creado DESC LIMIT 100"))


@app.get("/api/estado")
def estado():
    return jsonify(consulta("SELECT activo, alerta FROM estado WHERE id = 1", uno=True))


@app.post("/api/estado")
def guardar_estado():
    d = request.get_json(force=True)
    # Consulta parametrizada: nunca concatenes valores en el SQL
    consulta("UPDATE estado SET activo = %s, alerta = %s WHERE id = 1",
             (bool(d.get("activo")), bool(d.get("alerta"))), commit=True)
    return jsonify(ok=True)

@app.get("/api/esp32")
def control_esp32():
    datos = consulta("""
        SELECT led_verde, led_rojo, buzzer, modo, actualizado
        FROM control_esp32
        WHERE id = 1
    """, uno=True)

    if datos is None:
        return jsonify(error="Estado no disponible"), 503

    return jsonify(datos)


@app.get("/api/estado-laboratorio")
def estado_laboratorio():
    datos = consulta("""
        SELECT personas, proteccion, modo_alerta
        FROM public.estado_laboratorio
        WHERE id = 1
    """, uno=True)

    return jsonify(datos)


@app.post("/api/estado-laboratorio")
def actualizar_estado_laboratorio():
    d = request.get_json(silent=True)

    if not isinstance(d, dict):
        return jsonify(error="JSON invalido"), 400

    if type(d.get("personas")) is not bool:
        return jsonify(error="personas debe ser booleano"), 400

    if type(d.get("proteccion")) is not bool:
        return jsonify(error="proteccion debe ser booleano"), 400

    consulta("""
        UPDATE public.estado_laboratorio
        SET personas = %s, proteccion = %s
        WHERE id = 1
    """, (d["personas"], d["proteccion"]), commit=True)

    return jsonify(ok=True)


# --- Detector data (written by ppe_detector.py --db) --------------------------

@app.get("/api/deteccion")
def deteccion():
    """Live state from the camera: people in view and what they lack."""
    return jsonify(consulta("""
        SELECT cantidad_personas, sin_lentes, sin_bata, proteccion,
               deteccion_estado, ultima_deteccion
        FROM public.estado_laboratorio
        WHERE id = 1
    """, uno=True))


@app.get("/api/incidencias")
def incidencias():
    """Latest PPE alerts: when, which tracked person, what was missing."""
    return jsonify(consulta("""
        SELECT id, creado, track_id, falta_lentes, falta_bata, faltante,
               foto IS NOT NULL AS tiene_foto
        FROM public.incidencias
        ORDER BY creado DESC
        LIMIT 100
    """))


@app.get("/api/incidencias/<int:incidencia_id>/foto")
def foto_incidencia(incidencia_id):
    """The annotated photo of one alert, only from FOTOS_DIR."""
    fila = consulta("SELECT foto FROM public.incidencias WHERE id = %s",
                    (incidencia_id,), uno=True)
    if not fila or not fila["foto"]:
        return jsonify(error="Esta incidencia no tiene foto"), 404
    ruta = Path(fila["foto"]).resolve()
    if not ruta.is_relative_to(FOTOS_DIR) or not ruta.is_file():
        return jsonify(error="Foto no disponible"), 404
    return send_file(ruta, mimetype="image/jpeg")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
