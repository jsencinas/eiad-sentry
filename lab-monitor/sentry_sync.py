
"""Interpretar detecciones de eiad-sentry para Lab Monitor.

Primera versión: pruebas sin escribir en PostgreSQL.
"""

import json


def interpretar_detecciones(datos):
    if not isinstance(datos, dict):
        raise ValueError("La respuesta debe ser un objeto JSON")

    people = datos.get("people")

    if not isinstance(people, list):
        raise ValueError("Falta la lista de personas")

    personas = len(people) > 0
    proteccion = None

    if personas:
        veredictos = []

        for persona in people:
            if not isinstance(persona, dict):
                raise ValueError("Deteccion invalida")

            veredicto = persona.get("verdict")

            if veredicto not in (
                "OK", "MISSING", "PENDING", "UNVERIFIABLE"
            ):
                raise ValueError("Veredicto desconocido")

            veredictos.append(veredicto)

        if "MISSING" in veredictos:
            proteccion = False
        elif all(v == "OK" for v in veredictos):
            proteccion = True

    return {
        "personas": personas,
        "proteccion": proteccion,
        "detecciones": len(people),
        "datos_recientes": False,
    }


if __name__ == "__main__":
    ejemplo = {
        "people": [
            {"id": 1, "verdict": "OK", "missing": []},
            {
                "id": 2,
                "verdict": "MISSING",
                "missing": ["glasses"],
            },
        ]
    }

    resultado = interpretar_detecciones(ejemplo)

    print(json.dumps(resultado, indent=2))
    print("AVISO: resultados simulados, no detecciones reales")
