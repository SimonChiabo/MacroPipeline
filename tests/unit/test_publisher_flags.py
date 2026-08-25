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
