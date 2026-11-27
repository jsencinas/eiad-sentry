import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab-monitor"))

from epp import estado_epp  # noqa: E402


def fila(personas=1, sin_lentes=0, sin_bata=0, estado="CONFIRMADA"):
    return {"cantidad_personas": personas, "sin_lentes": sin_lentes,
            "sin_bata": sin_bata, "deteccion_estado": estado}


@pytest.mark.parametrize(
    "row, texto",
    [
        (fila(), "OK"),
        (fila(sin_lentes=1), "Falta: lentes de seguridad"),
        (fila(sin_bata=1), "Falta: bata"),
        (fila(personas=2, sin_lentes=1, sin_bata=1), "Falta: lentes de seguridad y bata"),
        (fila(personas=0), "OK"),
        (fila(estado="PENDIENTE"), "OK"),
    ],
)
def test_text_says_what_is_missing_or_ok(row, texto):
    assert estado_epp(row)["texto"] == texto


def test_no_signal_is_never_reported_as_ok():
    assert estado_epp(fila(estado="SIN_SENAL"))["texto"] == "Sin señal de la cámara"
    assert estado_epp(None)["estado"] == "SIN_SENAL"


def test_details_come_with_the_text():
    assert estado_epp(fila(personas=3, sin_bata=2)) == {
        "texto": "Falta: bata", "estado": "FALTA", "personas": 3,
        "falta_lentes": False, "falta_bata": True,
    }
