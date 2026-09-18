"""
Modelo de sensores.

Es donde hay que gastar el rigor. Un detector que corre sobre datos demasiado
limpios parece magico y se desarma en la primera pregunta del jurado; el
modelo de ruido es lo que sostiene todo lo demas.

Dos detalles que parecen menores y no lo son:

  SESGO CORRELACIONADO DEL SATELITE   Sin el, la curva de falsos positivos sale
      irreal. El ruido satelital no es blanco: tiene una componente que deriva
      despacio y que es justamente la que genera falsos positivos en un
      detector por umbral.

  OBSTRUCCION EN LA RADIO ENTRE PARES  Un 3% de las mediciones sale sesgada
      hacia ARRIBA, nunca hacia abajo, porque la obstruccion de la linea de
      vista alarga el camino aparente. Sin estos valores atipicos el detector
      parece perfecto, y modelarlos es lo que hace creible la curva.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ParametrosSensores:
    sigma_gnss_horizontal: float = 1.5  # m
    sigma_gnss_vertical: float = 3.0  # m
    # Sesgo satelital correlacionado en el tiempo, separado en dos partes
    # porque fisicamente son dos cosas distintas y el detector las ve distinto.
    #
    # COMUN: retardo ionosferico y troposferico, error de efemerides. Drones
    #   separados por decenas de metros ven los MISMOS satelites con
    #   practicamente la misma geometria, asi que este sesgo es casi identico
    #   para los cinco. Modelarlo como independiente por dron -- que es el
    #   error facil de cometer -- infla artificialmente el piso del residuo de
    #   la capa 1 y hace parecer al detector mucho peor de lo que es.
    #   Ojo con la consecuencia: un sesgo comun es una TRASLACION de toda la
    #   formacion, asi que la capa 1 es ciega a el por exactamente el mismo
    #   argumento que la deja ciega al caso B. Es la fuente honesta de falsos
    #   positivos de la capa 2.
    #
    # PROPIO: multitrayecto y ruido del receptor. Este si es de cada uno, y es
    #   lo unico que la capa 1 ve como residuo.
    tau_sesgo_gnss: float = 45.0  # s
    sigma_sesgo_comun: float = 1.2  # m
    sigma_sesgo_propio: float = 0.25  # m
    sigma_uwb: float = 0.10  # m
    prob_obstruccion: float = 0.03
    sesgo_obstruccion: float = 0.8  # m, siempre positivo
    # Camino aleatorio del error de velocidad de la unidad inercial.
    sigma_deriva_inercial: float = 0.04  # m/s por raiz de segundo
    sigma_barometro: float = 0.30  # m


@dataclass
class BancoSensores:
    n: int
    parametros: ParametrosSensores = field(default_factory=ParametrosSensores)
    semilla: int = 20260917
    _rng: np.random.Generator = field(init=False)
    _sesgo_comun: np.ndarray = field(init=False)
    _sesgo_propio: np.ndarray = field(init=False)
    _error_vel: np.ndarray = field(init=False)
    _deriva_baro: float = 0.0

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.semilla)
        self._sesgo_comun = np.zeros(3)
        self._sesgo_propio = np.zeros((self.n, 3))
        self._error_vel = np.zeros((self.n, 3))

    def _avanzar_sesgos(self, dt: float) -> None:
        """Proceso de Ornstein-Uhlenbeck: deriva lenta con reversion a la media."""
        p = self.parametros
        a = np.exp(-dt / p.tau_sesgo_gnss)
        escala = np.sqrt(1 - a**2)
        self._sesgo_comun = a * self._sesgo_comun + self._rng.normal(
            0.0, p.sigma_sesgo_comun * escala, 3
        )
        self._sesgo_propio = a * self._sesgo_propio + self._rng.normal(
            0.0, p.sigma_sesgo_propio * escala, (self.n, 3)
        )

        # El error de la inercial es un camino aleatorio puro: no revierte, por
        # eso la navegacion a estima se degrada sin techo.
        self._error_vel += self._rng.normal(
            0.0, p.sigma_deriva_inercial * np.sqrt(max(dt, 1e-6)), (self.n, 3)
        )
        self._deriva_baro += float(self._rng.normal(0.0, 0.01 * np.sqrt(max(dt, 1e-6))))

    def medir(
        self, dt: float, posiciones: np.ndarray, velocidades: np.ndarray
    ) -> dict[str, np.ndarray]:
        """
        Devuelve las lecturas crudas de un ciclo.

        gnss      (n,3) posicion satelital SIN ataque (el ataque se aplica despues)
        filas_uwb (n,n) distancia que cada nodo dice haber medido a cada otro
        vel_imu   (n,3) velocidad que informa la unidad inercial
        baro      (n,)  altura barometrica
        """
        p = self.parametros
        self._avanzar_sesgos(dt)

        ruido_gnss = self._rng.normal(0.0, 1.0, (self.n, 3)) * np.array(
            [p.sigma_gnss_horizontal, p.sigma_gnss_horizontal, p.sigma_gnss_vertical]
        )
        gnss = posiciones + self._sesgo_comun + self._sesgo_propio + ruido_gnss

        dif = posiciones[:, None, :] - posiciones[None, :, :]
        reales = np.linalg.norm(dif, axis=2)
        ruido_uwb = self._rng.normal(0.0, p.sigma_uwb, (self.n, self.n))
        obstruidas = self._rng.random((self.n, self.n)) < p.prob_obstruccion
        sesgo = obstruidas * self._rng.exponential(p.sesgo_obstruccion, (self.n, self.n))
        # Cada punta del par mide por su cuenta, asi que las dos mediciones de
        # una misma distancia NO son identicas. Esa diferencia es exactamente
        # lo que la capa 3 usa para detectar al que miente.
        filas = reales + ruido_uwb + sesgo
        np.fill_diagonal(filas, 0.0)

        vel_imu = velocidades + self._error_vel
        baro = posiciones[:, 2] + self._deriva_baro + self._rng.normal(
            0.0, p.sigma_barometro, self.n
        )

        return {"gnss": gnss, "filas_uwb": filas, "vel_imu": vel_imu, "baro": baro}
