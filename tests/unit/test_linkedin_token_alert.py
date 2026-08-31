"""La decisión de avisar del vencimiento del token de LinkedIn.

El aviso viejo era un `print` de `scripts/check_publishers.py`, que solo corre
a mano: se disparaba únicamente si alguien lo ejecutaba entre el día 50 y el
60. Esta es la mitad decidible de darle un canal, separada del envío para que
se pueda testear sin red.
"""

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def alerta():
    """El script, cargado por ruta: `scripts/` no es un paquete."""
    spec = importlib.util.spec_from_file_location(
        "linkedin_token_alert", ROOT / "scripts" / "linkedin_token_alert.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hoy_con_edad(edad: int) -> tuple[date, str]:
    """Un par (hoy, fecha_de_emision) que da exactamente `edad` días.

    La fecha de hoy se inyecta en vez de congelar el reloj: `mensaje_de_aviso`
    la recibe como argumento justamente para que estos tests no dependan del
    día en que corran.
    """
    hoy = date(2026, 10, 15)
    return hoy, (hoy - timedelta(days=edad)).isoformat()


@pytest.mark.parametrize("edad", [50, 55, 58])
def test_los_tres_pulsos_avisan(alerta, edad):
    hoy, emitido = _hoy_con_edad(edad)
    mensaje = alerta.mensaje_de_aviso(hoy, "true", emitido)
    assert mensaje is not None
    assert str(60 - edad) in mensaje


@pytest.mark.parametrize("edad", [0, 1, 49, 51, 54, 56, 57, 59])
def test_entre_pulsos_hay_silencio(alerta, edad):
    hoy, emitido = _hoy_con_edad(edad)
    assert alerta.mensaje_de_aviso(hoy, "true", emitido) is None


@pytest.mark.parametrize("edad", [60, 61, 120, 400])
def test_vencido_avisa_todos_los_dias(alerta, edad):
    hoy, emitido = _hoy_con_edad(edad)
    mensaje = alerta.mensaje_de_aviso(hoy, "true", emitido)
    assert mensaje is not None
    assert "vencido" in mensaje


@pytest.mark.parametrize("edad", [10, 50, 55, 58, 60, 400])
def test_la_bandera_apagada_silencia_a_cualquier_edad(alerta, edad):
    """El tercer eje de ADR-009: una red apagada no participa."""
    hoy, emitido = _hoy_con_edad(edad)
    assert alerta.mensaje_de_aviso(hoy, "false", emitido) is None


def test_la_fecha_ausente_avisa(alerta):
    """Fail-loud: una fecha ausente desarma la alarma, que es el problema."""
    mensaje = alerta.mensaje_de_aviso(date(2026, 10, 15), "true", "")
    assert mensaje is not None
    assert "desarmada" in mensaje


def test_la_fecha_ilegible_avisa(alerta):
    mensaje = alerta.mensaje_de_aviso(date(2026, 10, 15), "true", "21/08/2026")
    assert mensaje is not None
    assert "desarmada" in mensaje


def test_la_bandera_se_mira_antes_que_la_fecha(alerta):
    """El orden de las guardas es sustantivo, no estético.

    Con LinkedIn apagado y la fecha ilegible el resultado tiene que ser
    silencio. Si se miraran al revés, apagar la red dejaría de silenciar justo
    cuando la fecha quedó sin mantener —el caso más probable después de un
    apagado largo— y el aviso volvería a sonar todas las semanas por algo ya
    decidido. Invertir las dos guardas tiene que hacer caer este test.
    """
    assert alerta.mensaje_de_aviso(date(2026, 10, 15), "false", "no-es-fecha") is None
    assert alerta.mensaje_de_aviso(date(2026, 10, 15), "false", "") is None


def test_una_bandera_ilegible_no_silencia(alerta):
    """`maybe` no puede ser idéntico a un apagado deliberado.

    Es la misma trampa que el orden de las dos primeras ramas de
    `_startup_exit_code`: el valor que no se entiende tiene que caer del lado
    ruidoso, nunca del silencioso.
    """
    hoy, emitido = _hoy_con_edad(400)
    assert alerta.mensaje_de_aviso(hoy, "maybe", emitido) is not None


def test_la_bandera_ausente_no_silencia(alerta):
    """Ausente = participar, igual que `component_enabled`."""
    hoy, emitido = _hoy_con_edad(400)
    assert alerta.mensaje_de_aviso(hoy, "", emitido) is not None


@pytest.mark.parametrize("bandera", ["false", "False", "FALSE", "  false  ", "FaLsE"])
def test_la_bandera_apagada_se_normaliza(alerta, bandera):
    """`.strip()` y `.lower()` de la guarda no los fijaba ningun test.

    Quitando cualquiera de los dos los 26 tests seguian en verde, y una
    variable de repo tipeada como `False` es perfectamente plausible.
    """
    hoy, emitido = _hoy_con_edad(400)
    assert alerta.mensaje_de_aviso(hoy, bandera, emitido) is None


def test_una_fecha_futura_avisa(alerta):
    """Un typo de anio no puede desarmar la alarma en silencio.

    `2027-08-21` por `2026-08-21` daba edad negativa, caia al `return None`
    final, y la alarma quedaba muda un anio entero. Es la misma familia que la
    fecha ausente y la ilegible.
    """
    hoy = date(2026, 10, 15)
    futuro = (hoy + timedelta(days=30)).isoformat()
    mensaje = alerta.mensaje_de_aviso(hoy, "true", futuro)
    assert mensaje is not None
    assert "desarmada" in mensaje
