"""
Cinematica y dinamica de vuelo del enjambre.

Lo importante de este modulo, y lo que hace que la demostracion signifique
algo: cada dron navega hacia donde CREE estar, no hacia donde esta.

Ese es el ataque entero. Si el receptor satelital le dice a una aeronave que
esta veinte metros al este de su posicion real, la aeronave calcula que se
paso, corrige hacia el oeste, y se mueve fisicamente hacia el oeste. El
atacante no empuja al dron: lo convence de que se empuje solo. Por eso el lazo
tiene que estar cerrado en el simulador -- con la cinematica desacoplada del
estimador, un ataque de falsificacion no produce ninguna consecuencia y la
demostracion no demuestra nada.

Modelo de vuelo: multirrotor con guiado en cascada.

    lazo externo   error de posicion -> velocidad deseada   (saturada)
    lazo interno   error de velocidad -> aceleracion        (saturada)
    viento         se suma a la velocidad respecto del suelo

Las saturaciones son las que hacen que el vuelo se parezca al real: una
aeronave no se teletransporta a la posicion comandada, acelera hacia ella con
un limite que en un multirrotor es el angulo de alabeo maximo. Con 5 m/s^2 el
alabeo ronda los 27 grados, que es un valor de crucero razonable.

Convencion de ejes:  x = este,  y = norte,  z = altura.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Formacion en V de cinco nodos, en metros respecto del centro de formacion.
#
# CON ESCALONAMIENTO VERTICAL, y no es un detalle estetico. Una formacion que
# vuela toda exactamente a la misma altura es COPLANAR, y su reconstruccion
# geometrica tiene rango 2 en un espacio de tres dimensiones: queda una
# direccion sin determinar y el alineamiento rigido se vuelve mal condicionado.
# Medido, con la formacion plana y un nodo ya aislado, un companiero sano
# llegaba a mostrar 9 m de residuo y el quorum se caia a 3/5 sin que hubiera
# ningun segundo atacante.
#
# La solucion coincide con lo que hacen las formaciones reales por razones que
# no tienen nada que ver con esto: se escalonan en altura para evitar la estela
# del que va adelante y para tener separacion vertical ante una falla. Dos
# El escalonamiento tiene que ser INDEPENDIENTE de la posicion horizontal, y
# es facil errarle: una altura proporcional al desplazamiento lateral -- el
# primer intento fue z = 0,43*x -- no rompe nada, solo INCLINA el plano. Los
# cinco nodos siguen siendo coplanares y el rango sigue siendo 2. Conviene
# verificarlo numericamente en vez de confiar en que "estan a distinta altura".
#
# El caso coplanar sigue cubierto en pruebas/test_geometria.py: la matematica
# tiene que soportarlo igual, porque una formacion puede aplanarse en vuelo.
OFFSETS_V = np.array(
    [
        [0.0, 4.5, 4.0],  # D-01 GUIA
        [-6.0, 0.0, -2.0],  # D-02 ALA-I
        [6.0, 0.0, 2.0],  # D-03 ALA-D
        [-11.5, -4.5, -3.0],  # D-04 RET-I
        [11.5, -4.5, 3.0],  # D-05 RET-D
    ]
)

IDENTIFICADORES = ["D-01", "D-02", "D-03", "D-04", "D-05"]
ETIQUETAS = ["GUIA", "ALA-I", "ALA-D", "RET-I", "RET-D"]
INDICATIVOS = ["PENTA-LEAD", "WING-PORT", "WING-STBD", "TAIL-PORT", "TAIL-STBD"]


@dataclass
class ParametrosVuelo:
    # Crucero de multirrotor tactico.
    velocidad_crucero: float = 12.0  # m/s
    velocidad_maxima: float = 18.0  # m/s
    # 5 m/s^2 equivale a unos 27 grados de alabeo: crucero, no acrobacia.
    aceleracion_maxima: float = 5.0  # m/s^2
    aceleracion_vertical_maxima: float = 3.0  # m/s^2
    # Guiado en cascada. La ganancia externa fija cuan agresivamente se
    # persigue la posicion; la interna, cuan rapido se alcanza la velocidad
    # comandada. La externa tiene que ser bastante mas lenta que la interna o
    # el lazo oscila.
    ganancia_posicion: float = 0.9  # 1/s
    ganancia_velocidad: float = 2.5  # 1/s
    # Termino integral: anula el error estacionario contra el viento sostenido.
    # Sin el, un viento de 3,7 m/s deja al enjambre volando 4 m corrido de su
    # ruta de forma permanente, y esa desviacion constante se confunde con el
    # efecto del ataque, que es justamente lo que hay que poder distinguir.
    #
    # Ojo: el integrador trabaja sobre el error CREIDO, asi que no defiende de
    # nada. Bajo un ataque de falsificacion anula la discrepancia que el dron
    # percibe, y el resultado es que la posicion real queda corrida exactamente
    # lo que miente el satelite. El lazo hace su trabajo perfectamente y por eso
    # mismo el ataque funciona.
    # Cohesion de formacion por distancias medidas entre pares.
    #
    # Es la capacidad que salva al enjambre cuando pierde la referencia
    # absoluta. En repliegue cada aeronave navega por su propia inercial, y
    # esas derivas son INDEPENDIENTES: sin este termino la formacion se abre
    # sola, y lo hace ademas de forma visible para la prueba de rigidez, que
    # empieza a marcar residuos donde no hay ningun atacante.
    #
    # La distancia entre pares se sigue midiendo por radio aunque el satelite
    # este comprometido y aunque la posicion absoluta se haya perdido. Dicho de
    # otra forma: el enjambre puede no saber DONDE esta, y saber perfectamente
    # como esta dispuesto. Eso alcanza para mantener la formacion.
    ganancia_cohesion: float = 0.35  # 1/s
    ganancia_integral: float = 0.18  # 1/s
    # El tope tiene que alcanzar para cancelar el viento sostenido: con
    # ganancia 0,18 hacen falta unos 21 m*s para compensar 3,7 m/s. Con un tope
    # menor el integrador satura y queda un error estacionario permanente.
    integral_maxima: float = 40.0  # m*s, tope anti-saturacion
    # Viento sostenido mas rafagas. El controlador lo combate solo, y es lo que
    # hace que las trayectorias no queden geometricamente perfectas.
    viento: tuple[float, float, float] = (3.2, -1.8, 0.0)  # m/s
    sigma_rafaga: float = 0.9  # m/s
    tau_rafaga: float = 4.0  # s
    # Ruta de patrulla.
    radio_patrulla: float = 260.0  # m
    altitud: float = 40.0  # m


@dataclass
class Enjambre:
    """
    Estado fisico verdadero del enjambre. Es la verdad de terreno: nadie a
    bordo tiene acceso a esto, y el detector nunca lo ve.
    """

    parametros: ParametrosVuelo = field(default_factory=ParametrosVuelo)
    semilla: int = 4242
    t: float = 0.0
    posiciones: np.ndarray = field(init=False)
    velocidades: np.ndarray = field(init=False)  # respecto del suelo
    _vel_aire: np.ndarray = field(init=False)
    _rafaga: np.ndarray = field(init=False)
    _integral: np.ndarray = field(init=False)
    _rng: np.random.Generator = field(init=False)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.semilla)
        self._rafaga = np.zeros(3)
        self._integral = np.zeros((len(OFFSETS_V), 3))
        objetivo = self.formacion_planificada(0.0)
        self.posiciones = objetivo.copy()
        self.velocidades = np.tile(self.velocidad_planificada(0.0), (self.n, 1))
        self._vel_aire = self.velocidades - np.array(self.parametros.viento)

    @property
    def n(self) -> int:
        return len(OFFSETS_V)

    # ------------------------------------------------------------------- ruta
    def centro_planificado(self, t: float) -> np.ndarray:
        """Donde DEBERIA estar el centro de la formacion en el instante t."""
        p = self.parametros
        omega = p.velocidad_crucero / p.radio_patrulla
        ang = omega * t
        return np.array(
            [
                p.radio_patrulla * np.cos(ang),
                p.radio_patrulla * np.sin(ang),
                p.altitud,
            ]
        )

    def velocidad_planificada(self, t: float) -> np.ndarray:
        p = self.parametros
        omega = p.velocidad_crucero / p.radio_patrulla
        ang = omega * t
        return np.array(
            [
                -p.radio_patrulla * omega * np.sin(ang),
                p.radio_patrulla * omega * np.cos(ang),
                0.0,
            ]
        )

    def _giro(self, t: float) -> np.ndarray:
        """La formacion vuela encarada hacia donde va."""
        p = self.parametros
        rumbo = (p.velocidad_crucero / p.radio_patrulla) * t + np.pi / 2.0
        c, s = np.cos(rumbo), np.sin(rumbo)
        return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])

    def formacion_planificada(self, t: float) -> np.ndarray:
        """Posicion de consigna de cada dron: su lugar en la formacion."""
        return self.centro_planificado(t) + OFFSETS_V @ self._giro(t).T

    # ------------------------------------------------------------------ vuelo
    def avanzar(
        self,
        dt: float,
        posiciones_creidas: np.ndarray | None = None,
        reiniciar_integral: np.ndarray | None = None,
        distancias_medidas: np.ndarray | None = None,
        navegacion_absoluta: np.ndarray | None = None,
    ) -> None:
        """
        Un paso de vuelo.

        posiciones_creidas : lo que cada dron CREE que es su posicion. Es la
            entrada del guiado. Si es None se usa la posicion real, que
            equivale a un enjambre con navegacion perfecta -- util solo para
            calibrar el simulador, nunca para una demostracion.
        reiniciar_integral : mascara de drones que acaban de cambiar de fuente
            de navegacion.

        Lo segundo no es un detalle. El integrador acumula error contra UNA
        referencia; cuando el dron cambia de fuente -- de satelital a inercial,
        o a la posicion trilaterada desde sus pares -- ese acumulado quedo
        cargado contra una referencia que ya no existe, y sigue empujando
        varios segundos contra un error imaginario. Medido: un nodo que pasaba
        a estado colectivo con el integrador cargado se iba casi cuarenta
        metros fuera de formacion y arrastraba al detector a marcar como
        sospechosos a sus vecinos. Todo piloto automatico reinicia integradores
        al cambiar de modo, y por esto.
        """
        p = self.parametros
        self.t += dt

        if reiniciar_integral is not None:
            self._integral[np.asarray(reiniciar_integral, dtype=bool)] = 0.0

        creidas = self.posiciones if posiciones_creidas is None else posiciones_creidas
        objetivo = self.formacion_planificada(self.t)
        vel_ruta = self.velocidad_planificada(self.t)

        # NO se cambia a guiado relativo cuando falta la referencia absoluta,
        # y conviene dejar escrito por que, porque parece la idea correcta.
        #
        # Que cada dron mantenga su puesto respecto del centro del enjambre en
        # vez de respecto del terreno conserva la formacion, si. Pero el
        # enjambre deja de perseguir su ruta y queda a la deriva entero:
        # medido, el desvio con defensa trepaba a los mismos 230 m que sin
        # defensa, y la defensa dejaba de servir para lo unico que importa.
        #
        # Navegando a estima contra la ruta ABSOLUTA, el desvio queda acotado
        # por la deriva del sensor -- 16 m contra 92 m sin defensa -- a costa de
        # que la formacion se abra con el tiempo. Entre perder cohesion y perder
        # el rumbo, se elige perder cohesion.
        # --- lazo externo: a donde hay que ir, segun lo que el dron cree
        error = objetivo - creidas
        self._integral = np.clip(
            self._integral + error * dt, -p.integral_maxima, p.integral_maxima
        )
        vel_deseada = (
            vel_ruta + p.ganancia_posicion * error + p.ganancia_integral * self._integral
        )
        # --- cohesion: corrige la geometria con las distancias medidas, que
        #     siguen disponibles aunque no se sepa donde esta el enjambre
        if distancias_medidas is not None and p.ganancia_cohesion > 0:
            deseadas = np.linalg.norm(
                OFFSETS_V[:, None, :] - OFFSETS_V[None, :, :], axis=2
            )
            correccion = np.zeros_like(vel_deseada)
            for i in range(self.n):
                for j in range(self.n):
                    if i == j:
                        continue
                    separacion = creidas[i] - creidas[j]
                    modulo = np.linalg.norm(separacion)
                    if modulo < 1e-6:
                        continue
                    direccion = separacion / modulo
                    # Si la distancia medida supera la deseada, el par esta
                    # demasiado abierto y hay que acercarlo.
                    correccion[i] += (
                        (deseadas[i, j] - distancias_medidas[i, j]) * direccion
                    )
            vel_deseada = vel_deseada + p.ganancia_cohesion * correccion / (self.n - 1)

        rapidez = np.linalg.norm(vel_deseada, axis=1, keepdims=True)
        exceso = rapidez > p.velocidad_maxima
        vel_deseada = np.where(
            exceso, vel_deseada * (p.velocidad_maxima / np.maximum(rapidez, 1e-9)), vel_deseada
        )

        # --- lazo interno: aceleracion, con el limite de alabeo de la aeronave
        aceleracion = p.ganancia_velocidad * (vel_deseada - self._vel_aire)
        horizontal = aceleracion[:, :2]
        modulo = np.linalg.norm(horizontal, axis=1, keepdims=True)
        horizontal = np.where(
            modulo > p.aceleracion_maxima,
            horizontal * (p.aceleracion_maxima / np.maximum(modulo, 1e-9)),
            horizontal,
        )
        aceleracion = np.column_stack(
            [
                horizontal,
                np.clip(
                    aceleracion[:, 2],
                    -p.aceleracion_vertical_maxima,
                    p.aceleracion_vertical_maxima,
                ),
            ]
        )

        # --- rafaga: proceso correlacionado, no ruido blanco. El viento real
        #     tiene memoria, y un controlador lo persigue de otra manera.
        a = np.exp(-dt / p.tau_rafaga)
        self._rafaga = a * self._rafaga + self._rng.normal(
            0.0, p.sigma_rafaga * np.sqrt(1 - a**2), 3
        )
        viento = np.array(p.viento) + self._rafaga

        self._vel_aire = self._vel_aire + aceleracion * dt
        self.velocidades = self._vel_aire + viento
        self.posiciones = self.posiciones + self.velocidades * dt

    # ---------------------------------------------------------------- metrica
    def desvio_de_ruta(self) -> float:
        """
        Cuanto se aparto FISICAMENTE el enjambre de su ruta planificada.

        Es la metrica que mide el dano real del ataque. El residuo dice si el
        sistema se dio cuenta; esto dice si el atacante consiguio lo que
        queria.
        """
        objetivo = self.formacion_planificada(self.t)
        return float(np.linalg.norm(self.posiciones.mean(axis=0) - objetivo.mean(axis=0)))

    def distancias_reales(self) -> np.ndarray:
        """Distancias verdaderas entre pares: lo que la radio deberia medir."""
        dif = self.posiciones[:, None, :] - self.posiciones[None, :, :]
        return np.linalg.norm(dif, axis=2)
