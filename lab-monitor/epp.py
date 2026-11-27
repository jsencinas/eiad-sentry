"""Text to display for the PPE state of the people in front of the camera.

Pure function, no Flask or database: app.py reads the row of
`estado_laboratorio` (written by ppe_detector.py --db every 2 s) and passes
it here. Only safety glasses and lab coat are checked.
"""

from __future__ import annotations

from typing import Mapping

OK = "OK"
SIN_SENAL = "Sin señal de la cámara"


def estado_epp(fila: Mapping | None) -> dict:
    """Return {"texto", "estado", "personas", "falta_lentes", "falta_bata"}.

    texto is "OK" when nobody in view lacks anything, "Falta: ..." with the
    missing items otherwise, and "Sin señal de la cámara" when the detector
    reports no frames (showing "OK" then would hide the problem).
    """
    if not fila or fila.get("deteccion_estado") == "SIN_SENAL":
        return {"texto": SIN_SENAL, "estado": "SIN_SENAL", "personas": 0,
                "falta_lentes": False, "falta_bata": False}

    falta_lentes = (fila.get("sin_lentes") or 0) > 0
    falta_bata = (fila.get("sin_bata") or 0) > 0
    faltantes = [nombre for nombre, falta in (("lentes de seguridad", falta_lentes),
                                              ("bata", falta_bata)) if falta]
    return {
        "texto": "Falta: " + " y ".join(faltantes) if faltantes else OK,
        "estado": "FALTA" if faltantes else "OK",
        "personas": fila.get("cantidad_personas") or 0,
        "falta_lentes": falta_lentes,
        "falta_bata": falta_bata,
    }
