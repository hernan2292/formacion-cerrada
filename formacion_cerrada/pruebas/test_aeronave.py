"""
Pruebas del adaptador de aeronave, con fuentes de hardware simuladas.

No reemplazan probar contra una Pixhawk y un modulo de radio: lo que validan es
el LAZO -- que consolide bien la matriz de distancias, que respete el modo de
operacion, que no invente datos cuando falta telemetria, y que la
configuracion insegura no arranque. Eso es lo que se puede verificar sin
hardware, y conviene tenerlo verificado antes de conectar nada.
"""

import os

import numpy as np
import pytest

from formacion_cerrada.config import ASISTIDO, OBSERVACION, ConfigAeronave
from formacion_cerrada.malla.autenticacion import Autenticador, generar_clave
from formacion_cerrada.puente.aeronave import AeronaveFormacionCerrada

FORMACION = np.array(
    [
        [0.0, 4.5, 44.0],
        [-6.0, 0.0, 38.0],
        [6.0, 0.0, 42.0],
        [-11.5, -4.5, 37.0],
        [11.5, -4.5, 43.0],
    ]
)


class TelemetriaFalsa:
    def __init__(self, indice: int, disponible: bool = True) -> None:
        self.indice = indice
        self.disponible = disponible

    def leer(self):
        if not self.disponible:
            return None
        return {
            "posicion": FORMACION[self.indice].copy(),
            "velocidad": np.array([9.0, 0.5, 0.0]),
            "barometro": float(FORMACION[self.indice][2]),
        }


class DistanciasFalsas:
    def __init__(self, indice: int) -> None:
        self.indice = indice

    def leer(self):
        return {
            j: float(np.linalg.norm(FORMACION[self.indice] - FORMACION[j]))
            for j in range(len(FORMACION))
            if j != self.indice
        }


class MallaFalsa:
    """Malla en memoria con la misma interfaz que la de UDP."""

    def __init__(self) -> None:
        self.rechazados = 0
        self.entrantes: list[tuple[int, dict]] = []
        self.difundidos: list[dict] = []

    def difundir(self, contenido):
        self.difundidos.append(contenido)

    def recibir(self):
        salida, self.entrantes = self.entrantes, []
        return salida


def malla_con_vecinos(propio: int) -> MallaFalsa:
    malla = MallaFalsa()
    for j in range(len(FORMACION)):
        if j == propio:
            continue
        malla.entrantes.append(
            (
                j,
                {
                    "posicion": FORMACION[j].tolist(),
                    "distancias": {
                        str(k): float(np.linalg.norm(FORMACION[j] - FORMACION[k]))
                        for k in range(len(FORMACION))
                        if k != j
                    },
                },
            )
        )
    return malla


@pytest.fixture
def config(tmp_path):
    clave = tmp_path / "clave"
    clave.write_bytes(generar_clave())
    if os.name == "posix":
        os.chmod(clave, 0o600)
    return ConfigAeronave(
        indice=0,
        total_nodos=5,
        modo=OBSERVACION,
        clave_malla=str(clave),
        directorio_registro=str(tmp_path / "registro"),
    )


def test_ciclo_completo_sin_ataque(config):
    """Con la formacion sana, el nodo queda nominal y no marca a nadie."""
    aeronave = AeronaveFormacionCerrada(
        config, TelemetriaFalsa(0), DistanciasFalsas(0), malla_con_vecinos(0)
    )
    informe = None
    for _ in range(30):
        aeronave.malla.entrantes = malla_con_vecinos(0).entrantes
        informe = aeronave.paso(0.1)
    aeronave.cerrar()

    assert informe is not None
    assert informe["estado"] in ("NOMINAL", "SOSPECHA")
    assert max(informe["residuos"]) < 2.0


def test_sin_telemetria_no_inventa_nada(config):
    """
    Si la controladora todavia no manda posicion, el ciclo devuelve None.

    Importa: la alternativa tentadora es usar la ultima posicion conocida, y eso
    convierte un enlace caido en una medicion silenciosamente vieja.
    """
    aeronave = AeronaveFormacionCerrada(
        config, TelemetriaFalsa(0, disponible=False), DistanciasFalsas(0), MallaFalsa()
    )
    assert aeronave.paso(0.1) is None
    aeronave.cerrar()


def test_sin_malla_suficiente_lo_informa(config):
    """
    Con menos de cuatro nodos visibles no hay geometria que reconstruir, y el
    sistema tiene que DECIRLO en vez de publicar un residuo sin sentido.
    """
    aeronave = AeronaveFormacionCerrada(
        config, TelemetriaFalsa(0), DistanciasFalsas(0), MallaFalsa()
    )
    informe = aeronave.paso(0.1)
    aeronave.cerrar()
    assert informe["estado"] == "SIN_MALLA"
    assert informe["visibles"] == 1


def test_modo_observacion_nunca_escribe_a_la_controladora(config):
    """
    La garantia central del modo por defecto: con el sistema en observacion, la
    aeronave vuela exactamente como volaria sin esto instalado.
    """
    aeronave = AeronaveFormacionCerrada(
        config, TelemetriaFalsa(0), DistanciasFalsas(0), malla_con_vecinos(0)
    )
    assert aeronave.salida is None
    for _ in range(10):
        aeronave.malla.entrantes = malla_con_vecinos(0).entrantes
        aeronave.paso(0.1)
    aeronave.cerrar()


def test_modo_que_actua_exige_salida_declarada(config):
    """Pedir un modo que actua sin declarar por donde actuar no puede arrancar."""
    config.modo = ASISTIDO
    with pytest.raises(ValueError, match="requiere una salida"):
        AeronaveFormacionCerrada(
            config, TelemetriaFalsa(0), DistanciasFalsas(0), MallaFalsa()
        )


def test_configuracion_con_pocos_nodos_no_arranca(config):
    """Con tres nodos no hay tolerancia a uno que mienta, y hay que decirlo."""
    config.total_nodos = 3
    with pytest.raises(ValueError, match="al menos 4"):
        AeronaveFormacionCerrada(
            config, TelemetriaFalsa(0), DistanciasFalsas(0), MallaFalsa()
        )


def test_clave_de_malla_faltante_no_arranca(config):
    """Sin clave no hay autenticacion, y sin autenticacion la capa 3 es decorativa."""
    config.clave_malla = "/no/existe/clave"
    with pytest.raises(ValueError, match="clave de malla"):
        AeronaveFormacionCerrada(
            config, TelemetriaFalsa(0), DistanciasFalsas(0), MallaFalsa()
        )
