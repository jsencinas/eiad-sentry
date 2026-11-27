import sys
import types

import pytest

from sentry.inference import use_litert_for_edgetpu


@pytest.fixture
def fake_litert(monkeypatch):
    """A stand-in ai_edge_litert, and no real tflite_runtime installed."""
    interpreter = types.ModuleType("ai_edge_litert.interpreter")
    package = types.ModuleType("ai_edge_litert")
    package.interpreter = interpreter
    monkeypatch.setitem(sys.modules, "ai_edge_litert", package)
    monkeypatch.setitem(sys.modules, "ai_edge_litert.interpreter", interpreter)
    monkeypatch.delitem(sys.modules, "tflite_runtime", raising=False)
    monkeypatch.delitem(sys.modules, "tflite_runtime.interpreter", raising=False)
    yield interpreter
    sys.modules.pop("tflite_runtime", None)
    sys.modules.pop("tflite_runtime.interpreter", None)


def test_litert_is_registered_as_tflite_runtime(fake_litert):
    use_litert_for_edgetpu()
    assert sys.modules["tflite_runtime.interpreter"] is fake_litert


def test_loading_a_second_edgetpu_model_works(fake_litert):
    use_litert_for_edgetpu()
    use_litert_for_edgetpu()  # used to raise ValueError: tflite_runtime.__spec__ is None
    assert sys.modules["tflite_runtime.interpreter"] is fake_litert
