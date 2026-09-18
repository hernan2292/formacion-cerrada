"""
Malla del enjambre.

Bus en memoria con latencia y perdida configurables. La eleccion es
deliberada: un consenso que supone red perfecta no convence a nadie, y lo que
convence no es el protocolo sino el modelo de perdida. Un servicio de
mensajeria real (MQTT, ZeroMQ) agregaria un proceso mas y ningun argumento.

En campo esto se reemplaza por la carga util del propio enlace de banda
ultraancha, o por una radio MANET si hace falta alcance. El nodo no se entera:
recibe la misma lista de paquetes.

LO QUE FALTA, y hay que decirlo: los paquetes no estan autenticados. La capa 3
tolera que un INTEGRANTE del enjambre mienta, pero no que un tercero desde
afuera inyecte mensajes con formato valido. La correccion es firmar cada
paquete con una clave precompartida por la formacion mas un contador monotono
contra reenvio; no hace falta infraestructura de clave publica para una salida
de cinco aeronaves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Paquete:
    emisor: int
    contenido: dict[str, Any]


@dataclass
class Malla:
    n: int
    perdida: float = 0.0  # fraccion de paquetes que no llegan
    semilla: int = 7
    _rng: np.random.Generator = field(init=False)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.semilla)

    def difundir(self, paquetes: list[Paquete], receptor: int) -> list[Paquete]:
        """
        Que le llega efectivamente a un receptor.

        El propio paquete nunca se pierde: un dron siempre dispone de sus
        mediciones. Los ajenos se caen segun la tasa de perdida.
        """
        recibidos = []
        for p in paquetes:
            if p.emisor == receptor or self._rng.random() >= self.perdida:
                recibidos.append(p)
        return recibidos
