
"""Sincronizacion segura de YOLO con Lab Monitor."""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


SENTRY_URL = os.environ.get(
    "SENTRY_STATUS_URL",
    "http://127.0.0.1:8082/status.json",
)

DB_NAME = "lab_monitor"
MAX_EDAD_SEGUNDOS = 10
TOLERANCIA_FUTURO_SEGUNDOS = 2


def verificar_vigencia(datos):
    """Comprueba la fecha del ultimo resultado de YOLO."""

    frame_id = datos.get("frame_id")
    fecha_texto = datos.get("updated_at")

    if type(frame_id) is not int or frame_id <= 0:
        return False

    if not isinstance(fecha_texto, str):
        return False

    try:
        fecha = datetime.fromisoformat(
            fecha_texto.replace("Z", "+00:00")
        )
    except ValueError:
        return False

    if fecha.tzinfo is None:
        return False

    ahora = datetime.now(timezone.utc)
    edad = (ahora - fecha).total_seconds()

    return (
        -TOLERANCIA_FUTURO_SEGUNDOS
        <= edad
        <= MAX_EDAD_SEGUNDOS
    )


def interpretar_detecciones(datos):
    if not isinstance(datos, dict):
        raise ValueError("La respuesta debe ser un objeto JSON")

    people = datos.get("people")

    if not isinstance(people, list):
        raise ValueError("Falta la lista de personas")

    veredictos = []

    for persona in people:
        if not isinstance(persona, dict):
            raise ValueError("Deteccion invalida")

        veredicto = persona.get("verdict")

        if veredicto not in (
            "OK",
            "MISSING",
            "PENDING",
            "UNVERIFIABLE",
        ):
            raise ValueError("Veredicto desconocido")

        veredictos.append(veredicto)

    personas = len(people) > 0
    proteccion = None

    if personas:
        if "MISSING" in veredictos:
            proteccion = False
        elif all(v == "OK" for v in veredictos):
            proteccion = True

    return {
        "personas": personas,
        "proteccion": proteccion,
        "detecciones": len(people),
        "datos_recientes": verificar_vigencia(datos),
    }


def consultar_detector(url=SENTRY_URL):
    with urlopen(url, timeout=3) as respuesta:
        if respuesta.status != 200:
            raise ValueError("Respuesta HTTP inesperada")

        datos = json.load(respuesta)

    return interpretar_detecciones(datos)


def preparar_metadata(resultado):
    if resultado is None:
        return {
            "deteccion_estado": "SIN_SENAL",
            "cantidad_personas": 0,
        }

    if not resultado["datos_recientes"]:
        return {
            "deteccion_estado": "PENDIENTE",
            "cantidad_personas": 0,
        }

    if resultado["personas"] and resultado["proteccion"] is None:
        estado = "PENDIENTE"
    else:
        estado = "CONFIRMADA"

    return {
        "deteccion_estado": estado,
        "cantidad_personas": resultado["detecciones"],
    }


def guardar_metadata(metadata):
    import psycopg

    with psycopg.connect(dbname=DB_NAME) as conexion:
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                UPDATE public.estado_laboratorio
                SET deteccion_estado = %s,
                    cantidad_personas = %s
                WHERE id = 1
                """,
                (
                    metadata["deteccion_estado"],
                    metadata["cantidad_personas"],
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "No se encontro la fila del laboratorio"
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-metadata",
        action="store_true",
        help="Actualizar solo metadatos en PostgreSQL",
    )
    args = parser.parse_args()

    print("Lab Monitor - Sincronizador YOLO", flush=True)

    if args.write_metadata:
        print("Modo: ESCRITURA DE METADATOS", flush=True)
    else:
        print("Modo: SOLO LECTURA", flush=True)

    while True:
        try:
            resultado = consultar_detector()
            metadata = preparar_metadata(resultado)

            print(
                json.dumps(
                    {
                        "deteccion": resultado,
                        "metadata": metadata,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        except (
            HTTPError,
            URLError,
            TimeoutError,
            ValueError,
            OSError,
        ) as error:
            print(
                f"Detector no disponible: {error}",
                flush=True,
            )
            metadata = preparar_metadata(None)

        if args.write_metadata:
            try:
                guardar_metadata(metadata)
                print("Metadatos actualizados", flush=True)
            except Exception as error:
                print(
                    f"Error de PostgreSQL: {error}",
                    flush=True,
                )

        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSincronizador detenido")
