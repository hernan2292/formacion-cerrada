"""
Lo que corre a bordo de UN dron.

Este es el modulo que se desplegaria en la computadora companera de cada
aeronave. Ningun dron manda: los cinco reciben los mismos datos por la malla,
los cinco corren exactamente este codigo y los cinco votan -- incluido el
enganado, que va a votar que esta todo bien. Por eso la decision se toma por
mediana y no por unanimidad.

El ciclo tiene dos fases, y estan separadas porque en campo las separa la
radio:

    FASE 1  local     Mide, difunde su fila de distancias y su posicion
                      satelital, corre la prueba de rigidez con lo que le
                      llego, y evalua sus propias anclas absolutas.
    FASE 2  consenso  Con los votos de los demas ya recibidos, resuelve el
                      consenso robusto, fusiona las tres capas y actualiza su
                      maquina de estados.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .deteccion.capa1_rigidez import VotoCapa1, evaluar_rigidez
from .deteccion.geometria import umbral_robusto
from .deteccion.capa2_anclas import (
    AnclaInercial,
    EstadoCapa2,
    ParametrosAnclas,
    evaluar_anclas,
)
from .deteccion.capa3_consenso import (
    EstadoCapa3,
    asimetria_de_pares,
    consenso_por_mediana,
    consolidar_distancias,
)
from .deteccion.fusion import Confianza, fusionar
from .navegacion.maquina_estados import MaquinaEstados, ParametrosEstados
from .navegacion.estimador import (
    COLECTIVO as MODO_COLECTIVO,
    INERCIAL as MODO_INERCIAL,
    SATELITAL as MODO_SATELITAL,
    EstimadorNavegacion,
)
from .navegacion.trilateracion import trilaterar

# Discrepancia media tolerada entre las dos mediciones de un mismo par antes
# de considerar que un nodo esta falseando lo que publica. Se deriva del ruido
# de la radio (10 cm) mas el margen de obstruccion.
UMBRAL_ASIMETRIA_M = 1.2

# Constante de tiempo del suavizado del residuo.
TAU_RESIDUO_S = 2.0

# Constante de tiempo del promedio que se usa para DECIDIR a quien excluir.
# Mas corta que la del reporte a proposito: son dos exigencias distintas. El
# reporte tiene que dar barras limpias y estables, y puede permitirse dos
# segundos de retardo. La decision solo tiene que identificar cual es el nodo
# mas desviado, que es una pregunta mucho mas facil y que conviene contestar
# rapido: con el mismo promedio lento para las dos cosas, un salto instantaneo
# de 40 m tardaba 12,6 s en dispararse.
TAU_DECISION_S = 0.4

# Cuanto tiene que sostenerse la evidencia antes de expulsar a un nodo del
# quorum. Sin esta espera, un unico ciclo desafortunado -- una obstruccion
# simultanea en dos enlaces, un pico de ruido satelital -- expulsa a un nodo
# sano por un ciclo y lo readmite al siguiente. En pantalla eso es un quorum
# que parpadea entre 5/5 y 4/5 sin que pase nada, y en vuelo es una aeronave
# que entra y sale de la formacion logica varias veces por minuto.
#
# Es medio segundo y no dos como el aislamiento: la asimetria de pares es
# evidencia mucho mas directa que un residuo elevado, porque el otro extremo
# de cada par es testigo.
SEGUNDOS_PARA_EXPULSAR = 2.0


@dataclass
class VistaNodo:
    """Lo que este dron concluyo en este ciclo."""

    indice: int
    voto: VotoCapa1
    capa2_propia: EstadoCapa2
    capa3: EstadoCapa3 | None = None
    confianza: Confianza | None = None
    estado: str = "NOMINAL"
    posicion_recuperada: np.ndarray | None = None
    recuperacion_valida: bool = False
    # La posicion con la que este dron efectivamente NAVEGA.
    posicion_navegacion: np.ndarray | None = None
    modo_navegacion: str = MODO_SATELITAL


@dataclass
class NodoDeteccion:
    indice: int
    n: int
    parametros_anclas: ParametrosAnclas = field(default_factory=ParametrosAnclas)
    parametros_estados: ParametrosEstados = field(default_factory=ParametrosEstados)
    ancla: AnclaInercial = field(init=False)
    maquina: MaquinaEstados = field(init=False)
    navegacion: EstimadorNavegacion = field(init=False)
    # Ultima fila conocida de cada emisor. Cuando un paquete se pierde, este
    # dron sigue con el dato viejo de ese vecino en vez de quedarse sin
    # matriz: es lo que haria a bordo, y es lo que hace que la degradacion
    # con perdida de malla sea gradual y no un corte.
    _filas_cache: np.ndarray | None = field(default=None, init=False)
    _vectores_suaves: np.ndarray | None = field(default=None, init=False)
    _vectores_completo_suaves: np.ndarray | None = field(default=None, init=False)
    _marcado_s: np.ndarray | None = field(default=None, init=False)
    _baro_propio: float = field(default=0.0, init=False)
    _excluido_previo: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.ancla = AnclaInercial(parametros=self.parametros_anclas)
        self.maquina = MaquinaEstados(parametros=self.parametros_estados)
        self.navegacion = EstimadorNavegacion()

    # ---------------------------------------------------------------- fase 1
    def fase_local(
        self,
        dt: float,
        gnss_difundidas: np.ndarray,
        filas_difundidas: np.ndarray,
        visibles: list[int],
        vel_inercial_propia: np.ndarray,
        baro_propio: float,
    ) -> VistaNodo:
        """
        Corre la prueba de rigidez y las anclas propias.

        gnss_difundidas  (n,3) posicion que cada nodo dice tener
        filas_difundidas (n,n) distancia que cada nodo dice haber medido
        visibles         emisores cuyo paquete efectivamente llego este ciclo
        """
        if self._filas_cache is None:
            self._filas_cache = filas_difundidas.copy()
        else:
            for i in visibles:
                self._filas_cache[i, :] = filas_difundidas[i, :]
        # La propia fila nunca se pierde: son mediciones de este mismo dron.
        self._filas_cache[self.indice, :] = filas_difundidas[self.indice, :]

        self._baro_propio = float(baro_propio)
        D = consolidar_distancias(self._filas_cache)
        # El nodo a excluir del calce se decidio el ciclo pasado, sobre los
        # residuos ya promediados. Ver residuos_rigidez.
        voto = evaluar_rigidez(
            self.indice, gnss_difundidas, D, excluir=self._excluido_previo
        )

        # Suavizado temporal del VECTOR de residuo antes de decidir nada.
        #
        # El piso del residuo NO lo fija la radio entre pares (10 cm) sino el
        # ruido del receptor satelital (metro y medio por eje): la prueba
        # compara la forma informada contra la medida, y la informada es la
        # ruidosa. Se promedia el vector y no su norma por la misma razon que
        # en la capa 2: la norma es siempre positiva y su media converge al
        # ruido medio en vez de a cero. El desplazamiento que inyecta un ataque
        # es sistematico y sobrevive al promedio; el ruido se cancela.
        #
        # La formacion rota mientras vuela, pero a 9 m/s sobre un circuito de
        # 260 m gira 4 grados en dos segundos: despreciable frente al efecto
        # que se busca.
        alfa = 1.0 - float(np.exp(-dt / TAU_RESIDUO_S))

        def suavizar(acumulado, nuevo_valor):
            if acumulado is None or acumulado.shape != nuevo_valor.shape:
                return nuevo_valor.copy()
            return acumulado + alfa * (nuevo_valor - acumulado)

        # Dos promedios en paralelo: uno para decidir, otro para informar.
        alfa_decision = 1.0 - float(np.exp(-dt / TAU_DECISION_S))
        if (
            self._vectores_completo_suaves is None
            or self._vectores_completo_suaves.shape != voto.vectores_completo.shape
        ):
            self._vectores_completo_suaves = voto.vectores_completo.copy()
        else:
            self._vectores_completo_suaves += alfa_decision * (
                voto.vectores_completo - self._vectores_completo_suaves
            )
        self._vectores_suaves = suavizar(self._vectores_suaves, voto.vectores)

        voto.vectores = self._vectores_suaves.copy()
        voto.residuos = np.linalg.norm(self._vectores_suaves, axis=1)
        voto.umbral, voto.mediana, _ = umbral_robusto(voto.residuos)

        # La decision de a quien excluir se toma SIEMPRE sobre el ajuste
        # completo, que es imparcial. Nunca sobre el ajuste con exclusion, que
        # confirmaria su propia hipotesis.
        residuos_decision = np.linalg.norm(self._vectores_completo_suaves, axis=1)
        umbral_decision, _, _ = umbral_robusto(residuos_decision)
        peor = int(np.argmax(residuos_decision))
        self._excluido_previo = (
            peor if residuos_decision[peor] > umbral_decision else None
        )
        voto.excluido = self._excluido_previo

        confiar = self.maquina.estado in ("NOMINAL", "SOSPECHA")
        capa2 = evaluar_anclas(
            self.ancla,
            dt,
            gnss_difundidas[self.indice],
            vel_inercial_propia,
            baro_propio,
            confiar,
            self.parametros_anclas,
        )
        return VistaNodo(indice=self.indice, voto=voto, capa2_propia=capa2)

    # ---------------------------------------------------------------- fase 2
    def fase_consenso(
        self,
        dt: float,
        vista: VistaNodo,
        votos_recibidos: list[VotoCapa1],
        capa2_recibidas: list[EstadoCapa2],
        filas_difundidas: np.ndarray,
        gnss_difundidas: np.ndarray,
        vel_inercial: np.ndarray,
    ) -> VistaNodo:
        matriz_votos = np.array([v.sospechosos for v in votos_recibidos], dtype=bool)
        asimetria = asimetria_de_pares(filas_difundidas)
        capa3 = consenso_por_mediana(matriz_votos, UMBRAL_ASIMETRIA_M, asimetria)

        # Persistencia: solo se expulsa a quien viene marcado sostenidamente.
        if self._marcado_s is None:
            self._marcado_s = np.zeros(self.n)
        self._marcado_s = np.where(capa3.expulsados, self._marcado_s + dt, 0.0)
        capa3.expulsados = self._marcado_s >= SEGUNDOS_PARA_EXPULSAR
        capa3.quorum = int(self.n - capa3.expulsados.sum())

        confianza = fusionar(vista.voto, capa2_recibidas, capa3)

        soy_sospechoso = bool(
            vista.voto.sospechosos[self.indice] or capa3.expulsados[self.indice]
        )
        pares_confiables = int(
            np.sum(~capa3.expulsados & (np.arange(self.n) != self.indice))
        )

        estado = self.maquina.actualizar(
            dt,
            sospechoso=soy_sospechoso or vista.capa2_propia.disparada,
            anclas_caidas_global=confianza.capa2_disparada_global,
            pares_confiables=pares_confiables,
        )

        recuperada, valida = None, False
        if estado == "COLECTIVO":
            # Los companeros confiables pasan a ser los satelites de este dron.
            otros = [
                i
                for i in range(self.n)
                if i != self.indice and not capa3.expulsados[i]
            ]
            if len(otros) >= 3:
                anclas = gnss_difundidas[otros]
                # La distancia hasta cada par la mide este dron: es su propia
                # fila, que es el dato que NO depende del satelite.
                distancias = filas_difundidas[self.indice, otros]
                # Se siembra con la estimacion de navegacion vigente para no
                # caer en la solucion espejada al otro lado del plano de las
                # anclas.
                recuperada, valida = trilaterar(
                    anclas,
                    distancias,
                    altura_barometrica=self._baro_propio,
                    inicial=self.navegacion.pos,
                )

        # Con que posicion se NAVEGA a partir de esta decision. Es el paso que
        # convierte a la deteccion en algo que sirve: detectar el ataque y
        # seguir obedeciendole es lo mismo que no detectarlo.
        if estado in ("NOMINAL", "SOSPECHA"):
            modo = MODO_SATELITAL
        elif estado == "COLECTIVO" and valida:
            modo = MODO_COLECTIVO
        else:
            modo = MODO_INERCIAL

        vista.posicion_navegacion = self.navegacion.actualizar(
            dt,
            gnss_difundidas[self.indice],
            vel_inercial,
            modo,
            recuperada if valida else None,
        ).copy()
        vista.modo_navegacion = modo

        vista.capa3 = capa3
        vista.confianza = confianza
        vista.estado = estado
        vista.posicion_recuperada = recuperada
        vista.recuperacion_valida = valida
        return vista
