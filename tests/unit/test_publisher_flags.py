"""Las banderas por red: parseo estricto y construccion tri-estado.

`PUBLISH_X` y `PUBLISH_LINKEDIN` deciden si una red publica. Un parseo laxo
tendria que elegir en silencio entre dos lecturas de `PUBLISH_LINKEDIN=no`
—apagada si se compara contra "true", encendida si se compara contra "false"—
y las dos son malas: una pausa que no pausa, o una pausa que nadie pidio.
"""

import pytest

from macro_pipeline.publishers.flags import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    build_publisher,
    publisher_enabled,
)


def test_the_default_is_enabled(monkeypatch):
    """Sin la variable, se publica: es el comportamiento de siempre."""
    monkeypatch.delenv(PUBLISH_X_VAR, raising=False)
    assert publisher_enabled(PUBLISH_X_VAR) is True


def test_an_empty_value_is_enabled(monkeypatch):
    """`PUBLISH_X=` es una variable sin decidir, no una red apagada."""
    monkeypatch.setenv(PUBLISH_X_VAR, "")
    assert publisher_enabled(PUBLISH_X_VAR) is True


@pytest.mark.parametrize("value", ["false", "FALSE", "False", "  false  "])
def test_false_in_any_casing_disables(monkeypatch, value):
    monkeypatch.setenv(PUBLISH_LINKEDIN_VAR, value)
    assert publisher_enabled(PUBLISH_LINKEDIN_VAR) is False


@pytest.mark.parametrize("value", ["true", "TRUE", "True", "  true  "])
def test_true_in_any_casing_enables(monkeypatch, value):
    monkeypatch.setenv(PUBLISH_LINKEDIN_VAR, value)
    assert publisher_enabled(PUBLISH_LINKEDIN_VAR) is True


@pytest.mark.parametrize("value", ["no", "0", "off", "yes", "sí"])
def test_anything_else_raises_instead_of_guessing(monkeypatch, value):
    """El valor invalido mata la run en el constructor, y eso es el punto.

    Las dos alternativas son silenciosas: tratarlo como apagada deja de
    publicar sin que nadie lo pida, tratarlo como encendida publica en una red
    que se quiso pausar. Morir con un mensaje claro en la primera run despues
    del typo es lo unico que se ve.
    """
    monkeypatch.setenv(PUBLISH_X_VAR, value)
    with pytest.raises(ValueError) as exc:
        publisher_enabled(PUBLISH_X_VAR)
    assert PUBLISH_X_VAR in str(exc.value)
    assert value in str(exc.value)


def test_the_error_shows_what_the_operator_actually_typed(monkeypatch):
    """`!r` esta para que se vea el espacio de mas; normalizar antes lo anula.

    El typo mas dificil de ver a ojo en un `.env` es un espacio o un CRLF
    pegado al final de la linea, y era justo el que el mensaje escondia.
    """
    monkeypatch.setenv(PUBLISH_X_VAR, "  Yes  ")
    with pytest.raises(ValueError) as exc:
        publisher_enabled(PUBLISH_X_VAR)
    assert "'  Yes  '" in str(exc.value)


class _Cliente:
    pass


def test_a_healthy_client_comes_back_with_no_error():
    cliente, error = build_publisher("x", _Cliente, enabled=True)
    assert isinstance(cliente, _Cliente)
    assert error is None


def test_a_disabled_publisher_is_not_constructed_at_all():
    """Apagada no es "se construye y no se usa": no se construye.

    Es lo que hace que un token de LinkedIn vencido con la bandera en false
    de una run verde y silenciosa en vez de una degradada con alerta.
    """
    llamadas = []

    def factory():
        llamadas.append(1)
        return _Cliente()

    cliente, error = build_publisher("linkedin", factory, enabled=False)

    assert cliente is None
    assert error is None, "una red apagada no es un fallo y no debe alertar"
    assert llamadas == [], "no se debe ni intentar construir el cliente"


def test_a_broken_client_comes_back_with_the_reason():
    """Rota: sin cliente, pero con el motivo, que es lo que va en la alerta."""

    def factory():
        raise ValueError("Faltan credenciales de X API.")

    cliente, error = build_publisher("x", factory, enabled=True)

    assert cliente is None
    assert error == "Faltan credenciales de X API."


def test_only_valueerror_is_swallowed():
    """Un fallo que no sea de credenciales no se disfraza de red rota."""

    def factory():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        build_publisher("x", factory, enabled=True)
