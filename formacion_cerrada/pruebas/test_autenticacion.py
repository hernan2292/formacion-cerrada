"""
Pruebas de la autenticacion de la malla.

Cada prueba corresponde a un ataque concreto contra el canal por el que los
drones se creen entre si. Si alguna deja de pasar, el enjambre vuelve a ser
vulnerable a que un tercero desde afuera decida quien es sospechoso.
"""

import time

import pytest

from formacion_cerrada.malla.autenticacion import (
    Autenticador,
    ErrorAutenticacion,
    generar_clave,
)


def par_de_nodos():
    """Dos drones de la misma formacion: comparten clave."""
    clave = generar_clave()
    return Autenticador(clave, 0), Autenticador(clave, 1)


def test_mensaje_legitimo_pasa():
    emisor, receptor = par_de_nodos()
    crudo = emisor.empaquetar({"residuos": [0.4, 0.5, 0.3, 0.6, 0.4]})
    sobre = receptor.desempaquetar(crudo)
    assert sobre.emisor == 0
    assert sobre.contenido["residuos"][0] == 0.4


def test_tercero_sin_clave_no_puede_inyectar():
    """
    El ataque que motiva todo este modulo: alguien que no esta en la formacion
    fabrica un voto para que el enjambre expulse a un nodo sano.
    """
    _, receptor = par_de_nodos()
    intruso = Autenticador(generar_clave(), 3)  # clave distinta
    crudo = intruso.empaquetar({"sospechosos": [False, False, False, False, True]})
    with pytest.raises(ErrorAutenticacion, match="firma invalida"):
        receptor.desempaquetar(crudo)


def test_contenido_alterado_se_rechaza():
    """Interceptar un mensaje legitimo y cambiarle un numero tampoco sirve."""
    emisor, receptor = par_de_nodos()
    crudo = bytearray(emisor.empaquetar({"residuo": 0.4}))
    # Se toca un byte del cuerpo, dejando la firma intacta.
    crudo[-5] = crudo[-5] ^ 0xFF
    with pytest.raises(ErrorAutenticacion, match="firma invalida"):
        receptor.desempaquetar(bytes(crudo))


def test_reenvio_del_mismo_mensaje_se_rechaza():
    """
    Un atacante que no puede fabricar mensajes igual puede GRABARLOS y
    repetirlos. Un mensaje viejo es criptograficamente impecable: lo unico que
    lo delata es el contador.
    """
    emisor, receptor = par_de_nodos()
    crudo = emisor.empaquetar({"residuo": 0.4})
    receptor.desempaquetar(crudo)  # la primera vez entra
    with pytest.raises(ErrorAutenticacion, match="contador"):
        receptor.desempaquetar(crudo)  # la segunda, no


def test_mensaje_viejo_se_rechaza_aunque_el_contador_sirva():
    """
    Segunda barrera, para el nodo que se reinicio y perdio su tabla de
    contadores: un mensaje de hace un minuto se descarta por antiguedad.
    """
    emisor, _ = par_de_nodos()
    crudo = emisor.empaquetar({"residuo": 0.4})
    receptor_nuevo = Autenticador(emisor.clave, 1)  # tabla vacia
    time.sleep(0.01)

    import formacion_cerrada.malla.autenticacion as auth

    original = auth.ANTIGUEDAD_MAXIMA_S
    auth.ANTIGUEDAD_MAXIMA_S = 0.001
    try:
        with pytest.raises(ErrorAutenticacion, match="ventana temporal"):
            receptor_nuevo.desempaquetar(crudo)
    finally:
        auth.ANTIGUEDAD_MAXIMA_S = original


def test_perdida_de_paquetes_no_rompe_la_secuencia():
    """
    La malla pierde mensajes. El contador tiene que tolerar huecos: si exigiera
    consecutividad estricta, una perdida normal dejaria al nodo incomunicado.
    """
    emisor, receptor = par_de_nodos()
    receptor.desempaquetar(emisor.empaquetar({"n": 1}))
    for _ in range(20):
        emisor.empaquetar({"descartado": True})  # se pierden en el aire
    sobre = receptor.desempaquetar(emisor.empaquetar({"n": 22}))
    assert sobre.contenido["n"] == 22
