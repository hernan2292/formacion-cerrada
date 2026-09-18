"""
Fusion de las tres capas en un indice de confianza.

Aca vive la REGLA DE COMBINACION, que es la unica parte del sistema que cubre
el caso B y conviene leerla despacio:

    La capa 1 sola no puede bajar la confianza global.

    Si todos los residuos son chicos pero la capa 2 discrepa en todo el
    enjambre, la formacion ENTERA queda sospechada.

Sin esa regla, un ataque de traslacion coherente produce exactamente la misma
salida que un vuelo nominal: cinco residuos planos, quorum 5/5, todo verde. La
capa 1 vota que esta todo bien y tiene razon dentro de su propio marco -- el
problema es que su marco es el enjambre, y el enjambre entero fue desplazado.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .capa2_anclas import EstadoCapa2
from .capa3_consenso import EstadoCapa3
from .capa1_rigidez import VotoCapa1


@dataclass
class Confianza:
    por_dron: np.ndarray  # 0.0 (no confiable) a 1.0 (nominal)
    formacion: float
    capa1_disparada: bool
    capa2_disparada_global: bool
    capa3_disparada: bool
    motivo: str


def _confianza_desde_residuo(residuo: float, umbral: float) -> float:
    """
    Confianza graduada, no alarma binaria.

    Cae suave desde 1.0 en residuo nulo hasta 0.0 en el doble del umbral, para
    que la interfaz pueda mostrar deterioro antes de que algo se dispare.
    """
    if umbral <= 0:
        return 1.0
    relacion = residuo / umbral
    if relacion <= 0.5:
        return 1.0
    if relacion >= 2.0:
        return 0.0
    return float(np.clip(1.0 - (relacion - 0.5) / 1.5, 0.0, 1.0))


def fusionar(
    voto_capa1: VotoCapa1,
    estados_capa2: list[EstadoCapa2],
    estado_capa3: EstadoCapa3,
) -> Confianza:
    n = len(estados_capa2)

    por_dron = np.array(
        [
            _confianza_desde_residuo(voto_capa1.residuos[i], voto_capa1.umbral)
            for i in range(n)
        ]
    )

    # Un nodo expulsado por la capa 3 pierde toda la confianza, sin gradiente.
    por_dron[estado_capa3.expulsados] = 0.0

    # Cada dron descuenta ademas por sus propias anclas absolutas.
    for i, c2 in enumerate(estados_capa2):
        if c2.disparada:
            por_dron[i] = min(por_dron[i], 0.25)

    capa1_disparada = bool(voto_capa1.sospechosos.any())
    capa3_disparada = bool(estado_capa3.expulsados.any())

    # El caso B: las anclas discrepan en TODOS los nodos a la vez. Ningun
    # dron individual es el problema; el problema es que la referencia comun
    # se movio debajo de todos.
    disparadas = sum(1 for c2 in estados_capa2 if c2.disparada)
    capa2_global = disparadas >= max(2, int(np.ceil(n * 0.6)))

    if capa2_global:
        # La formacion entera queda sospechada aunque la capa 1 vea todo liso.
        formacion = 0.0
        motivo = (
            "Anclas absolutas discrepan en todo el enjambre: traslacion "
            "coherente. La capa 1 esta ciega por construccion."
        )
    elif capa3_disparada:
        formacion = float(np.median(por_dron))
        motivo = "Nodo bizantino aislado por asimetria de pares y voto por mediana."
    elif capa1_disparada:
        formacion = float(np.median(por_dron))
        motivo = "Residuo de rigidez sobre umbral robusto en al menos un nodo."
    elif voto_capa1.formacion_degenerada:
        formacion = 0.5
        motivo = "Geometria degenerada: los nodos estan casi alineados."
    else:
        formacion = float(np.min(por_dron))
        motivo = "Formacion rigida y anclas consistentes."

    return Confianza(
        por_dron=por_dron,
        formacion=formacion,
        capa1_disparada=capa1_disparada,
        capa2_disparada_global=capa2_global,
        capa3_disparada=capa3_disparada,
        motivo=motivo,
    )
