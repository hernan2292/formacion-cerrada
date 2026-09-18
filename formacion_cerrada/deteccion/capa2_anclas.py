"""
Capa 2: anclas absolutas.

Son las unicas verificaciones que NO dependen del enjambre. La capa 1 compara
a los drones entre si, asi que un atacante que les miente a todos por igual la
deja ciega. Estas anclas cada dron las puede evaluar solo, sin hablar con
nadie, y por eso son las que sobreviven a la traslacion coherente.

Dos anclas implementadas:

  INERCIAL   El dron integra sus propios acelerometros y se lleva una posicion
             estimada a ciegas. Si el satelite dice que te moviste 200 m al
             este y tus sensores no sintieron nada, alguien miente. Cubre las
             tres dimensiones, y es la unica que muerde en el plano
             HORIZONTAL, que es justamente donde va a trasladar un atacante
             competente.

  BAROMETRO  La presion del aire da altura y no se puede falsificar por radio.
             Es un ancla fortisima pero SOLO restringe la vertical: contra una
             traslacion horizontal no aporta absolutamente nada.

Falta una tercera que cerraria el hueco del plano horizontal: el sesgo de
reloj del receptor. El receptor no resuelve tres incognitas sino cuatro (las
tres de posicion mas el desfasaje de su propio reloj), y un ataque de reenvio
corre ese reloj si o si. No esta implementada todavia; iria como una clase mas
en este modulo, alimentada por la solucion de reloj que el receptor ya emite.


SOBRE EL ESTADISTICO DE DETECCION, que es donde esta la sutileza:

Lo que se suaviza es el VECTOR de innovacion, no su magnitud. Es la diferencia
entre que el ancla funcione y que no. Una magnitud es siempre positiva, asi
que promediarla no cancela el ruido: converge al valor medio de la magnitud
del ruido. Promediando el vector, en cambio, el ruido blanco se cancela hacia
cero y lo que sobrevive es el sesgo sistematico -- que es exactamente lo que
introduce un ataque.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ParametrosAnclas:
    # Cotas dimensionadas contra el ruido de LO QUE SE COMPARA ya suavizado,
    # no contra la precision del sensor que ancla. El barometro tiene 30 cm de
    # ruido propio pero se compara contra la altura satelital, que tiene tres
    # metros: dimensionar su cota contra los 30 cm hace que dispare sola en
    # vuelo nominal. Los valores salen de medir vuelo limpio y tomar el
    # percentil 99,9 con margen.
    cota_inercial_m: float = 3.0
    cota_barometrica_m: float = 2.5
    tau_suavizado_s: float = 3.0

    # Filtro complementario de segundo orden. `ganancia_posicion` corrige la
    # posicion estimada y `ganancia_sesgo` estima la deriva de la propia
    # unidad inercial para cancelarla. Sin el segundo termino el error de
    # velocidad es un camino aleatorio sin techo y la estimacion se va sola.
    #
    # `ganancia_posicion` fija el PISO DE DETECCION de esta ancla: bajo un
    # arrastre a v metros por segundo, la innovacion se estabiliza en v
    # dividido esta ganancia. Con 0,25 un arrastre de 1,6 m/s deja 6,4 m de
    # innovacion sostenida, muy por encima del ruido; uno de 0,2 m/s deja 0,8 m
    # y queda enterrado. Ese es el limite honesto de la capa 2 en el plano
    # horizontal y no se puede evitar con una sola ancla inercial.
    ganancia_posicion: float = 0.25
    ganancia_sesgo: float = 0.008

    # Deriva de la estimacion inercial una vez que se dejo de creer al
    # satelite. Del orden de decenas de metros por minuto, que es lo que da
    # una unidad inercial de grado comercial sin correccion externa.
    deriva_aislado_m_por_s: float = 0.5


@dataclass
class AnclaInercial:
    """Navegacion a estima por dron, con su propia cota de confianza."""

    parametros: ParametrosAnclas = field(default_factory=ParametrosAnclas)
    pos_estimada: np.ndarray | None = None
    sesgo_velocidad: np.ndarray = field(default_factory=lambda: np.zeros(3))
    innovacion_suave: np.ndarray = field(default_factory=lambda: np.zeros(3))
    delta_baro_suave: float = 0.0
    segundos_sin_anclaje: float = 0.0
    _iniciada: bool = False

    def reiniciar(self, pos: np.ndarray) -> None:
        self.pos_estimada = np.array(pos, dtype=float)
        self.sesgo_velocidad = np.zeros(3)
        self.innovacion_suave = np.zeros(3)
        self.delta_baro_suave = 0.0
        self.segundos_sin_anclaje = 0.0
        self._iniciada = True

    def actualizar(
        self,
        dt: float,
        pos_satelital: np.ndarray,
        vel_inercial: np.ndarray,
        confiar_en_satelite: bool,
    ) -> tuple[float, float]:
        """Devuelve (discrepancia, cota). Dispara cuando discrepancia > cota."""
        pos_satelital = np.asarray(pos_satelital, dtype=float)
        if not self._iniciada:
            self.reiniciar(pos_satelital)
            return 0.0, self.parametros.cota_inercial_m

        p = self.parametros

        # Propagacion a ciegas, ya descontado el sesgo estimado del sensor.
        vel = np.asarray(vel_inercial, dtype=float) - self.sesgo_velocidad
        self.pos_estimada = self.pos_estimada + vel * dt
        innovacion = pos_satelital - self.pos_estimada

        # Suavizado del VECTOR: el ruido blanco se cancela, el sesgo queda.
        alfa = 1.0 - float(np.exp(-dt / p.tau_suavizado_s))
        self.innovacion_suave = self.innovacion_suave + alfa * (
            innovacion - self.innovacion_suave
        )
        discrepancia = float(np.linalg.norm(self.innovacion_suave))

        # EL FILTRO CORRIGE SIEMPRE, se le crea al satelite o no.
        #
        # Esta ancla es un DETECTOR, no la solucion de navegacion. Son dos
        # cosas distintas y confundirlas produce un enganche del que no se
        # vuelve: si al dejar de confiar en el satelite el filtro deja de
        # corregir, la estimacion diverge sin techo, la discrepancia crece
        # sola, y el criterio de recuperacion -- que se evalua sobre esa misma
        # discrepancia -- no se puede cumplir nunca. El nodo queda aislado para
        # siempre por un unico pico de ruido.
        #
        # Corriendo siempre, el comportamiento es el correcto en los dos
        # sentidos: mientras el ataque se sostiene la innovacion se queda
        # clavada en v/ganancia y sigue por encima de la cota; cuando el ataque
        # cesa, decae y el nodo puede volver a confiar.
        #
        # Que hacer con la posicion mientras tanto es problema de la maquina de
        # estados y de la trilateracion, no de este modulo.
        self.pos_estimada = self.pos_estimada + p.ganancia_posicion * dt * innovacion
        self.sesgo_velocidad = self.sesgo_velocidad - p.ganancia_sesgo * dt * innovacion

        if confiar_en_satelite:
            self.segundos_sin_anclaje = 0.0
        else:
            # Cuanto hace que la navegacion no tiene una referencia absoluta
            # confiable. No entra en la deteccion: sirve para saber cuanto
            # puede sostenerse el estado AISLADO antes de replegarse.
            self.segundos_sin_anclaje += dt

        return discrepancia, p.cota_inercial_m

    def actualizar_barometro(
        self, dt: float, altura_satelital: float, altura_barometrica: float
    ) -> tuple[float, float]:
        """
        Compara altura satelital contra barometrica.

        Se suaviza la diferencia CON SIGNO por el mismo motivo que la
        innovacion inercial, y recien despues se toma el valor absoluto.
        """
        p = self.parametros
        delta = float(altura_satelital) - float(altura_barometrica)
        alfa = 1.0 - float(np.exp(-dt / p.tau_suavizado_s))
        self.delta_baro_suave += alfa * (delta - self.delta_baro_suave)
        return abs(self.delta_baro_suave), p.cota_barometrica_m


@dataclass
class EstadoCapa2:
    disparada: bool
    discrepancia_inercial: float
    cota_inercial: float
    discrepancia_barometrica: float
    cota_barometrica: float

    @property
    def metrica(self) -> float:
        """Cuanto excede la peor de las dos anclas su propia cota, normalizado."""
        a = self.discrepancia_inercial / max(self.cota_inercial, 1e-9)
        b = self.discrepancia_barometrica / max(self.cota_barometrica, 1e-9)
        return max(a, b)


def evaluar_anclas(
    ancla: AnclaInercial,
    dt: float,
    pos_satelital: np.ndarray,
    vel_inercial: np.ndarray,
    altura_barometrica: float,
    confiar_en_satelite: bool,
    parametros: ParametrosAnclas,
) -> EstadoCapa2:
    """Corre las dos anclas de un dron y resume su veredicto."""
    disc_i, cota_i = ancla.actualizar(
        dt, pos_satelital, vel_inercial, confiar_en_satelite
    )
    disc_b, cota_b = ancla.actualizar_barometro(
        dt, pos_satelital[2], altura_barometrica
    )
    return EstadoCapa2(
        disparada=bool(disc_i > cota_i or disc_b > cota_b),
        discrepancia_inercial=disc_i,
        cota_inercial=cota_i,
        discrepancia_barometrica=disc_b,
        cota_barometrica=cota_b,
    )
