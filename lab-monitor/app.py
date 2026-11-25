import os
import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request, send_from_directory

# Ejemplo: export DATABASE_URL="postgresql://localhost/lab_monitor"
DATABASE_URL = os.environ["DATABASE_URL"]

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
