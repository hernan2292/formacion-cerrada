# Montaje y activación sobre aeronave

Procedimiento para instalar Formación Cerrada en un enjambre de cinco drones y
llegar a una prueba piloto.

---

## Antes que nada: qué está probado y qué no

Esto no es una advertencia de trámite. Define cómo hay que leer todo el resto
del documento.

| Componente | Estado |
|---|---|
| Núcleo de detección — las tres capas, fusión, estados, trilateración | **Probado.** 34 pruebas automatizadas sobre escenarios simulados con ruido |
| Autenticación de la malla | **Probado.** Incluye inyección por terceros, alteración y reenvío |
| Lazo de a bordo — consolidación, modos, manejo de malla incompleta | **Probado** con fuentes simuladas |
| `TelemetriaMavlink` — lectura de la controladora | **NO probado contra hardware** |
| `DistanciasUwb` — lectura de la radio | **NO probado contra hardware** |
| `SalidaMavlink` — escritura a la controladora | **NO probado, e incompleto** |

Las tres últimas están escritas contra la documentación de sus protocolos. Nadie
las ejecutó sobre una Pixhawk ni sobre un módulo DW3000.

**Por eso el modo por defecto es `observacion`.** En ese modo el sistema mide,
detecta, registra y avisa, y no le escribe absolutamente nada a la controladora
de vuelo. Si el adaptador tiene un error, lo peor que pasa es que no detecte algo
o que registre basura: la aeronave vuela exactamente como volaría sin esto
instalado.

> **Durante el hackathón no se vuela nada.** Las bases prohíben explícitamente
> realizar pruebas sobre sistemas reales. Este documento es para después.

---

## 1. Materiales

Por aeronave:

| Pieza | Referencia | Aproximado |
|---|---|---|
| Computadora compañera | Raspberry Pi Zero 2 W (o CM4) | USD 15 – 60 |
| Radio de distancias | Módulo Qorvo DW3000 con antena | USD 25 – 40 |
| Tarjeta microSD | 16 GB clase 10 o mejor | USD 6 |
| Conversor de nivel | 5 V ↔ 3,3 V si la controladora lo requiere | USD 2 |
| Cableado | JST-GH para telemetría, hilos para SPI/serie | — |

Ya a bordo y sin costo adicional: receptor satelital, unidad inercial y
barómetro de la propia controladora.

**Total agregado por nodo: ~45 g, ~2 W, ~USD 60.**

El enjambre necesita **cinco nodos como mínimo**. Con cuatro hay geometría pero
no tolerancia a uno que mienta; con tres, ni siquiera geometría.

### Sobre la radio de distancias

Es la pieza de la que depende todo. Con banda ultraancha el error es de diez
centímetros y el método funciona holgado. Sustituyéndola por intensidad de señal
—que no cuesta nada porque ya está en cualquier radio— el error salta a metros,
el umbral tiene que subir en la misma proporción, y sólo quedan detectables los
ataques groseros.

Alcance útil en línea de vista: **100 a 300 m**. Eso le pone un techo geométrico
a la formación, y es una restricción táctica, no un detalle.

---

## 2. Conexionado

```
   ┌──────────────────┐         ┌────────────────────────┐
   │  Receptor GNSS   │         │  COMPUTADORA COMPAÑERA │
   │  Unidad inercial │         │  Raspberry Pi Zero 2 W │
   │  Barómetro       │         │                        │
   └────────┬─────────┘         │  formacion_cerrada     │
            │                   │                        │
   ┌────────▼─────────┐  UART   │                        │   SPI   ┌──────────┐
   │  CONTROLADORA    │◄───────►│                        │◄───────►│ DW3000   │
   │  DE VUELO        │ 921600  │                        │         │ distancias│
   │  Pixhawk         │         │                        │         └──────────┘
   │  firmware SIN    │         │                        │
   │  modificar       │         │                        │   UDP   ┌──────────┐
   └──────────────────┘         │                        │◄───────►│ malla    │
                                └────────────────────────┘         └──────────┘
```

**Controladora ↔ compañera.** Puerto TELEM2 de la Pixhawk al UART de la Pi.
Cuatro hilos: TX, RX, GND y —sólo si la Pi no tiene alimentación propia— 5 V.
Cruzar TX con RX. Verificar el nivel lógico: la mayoría de las Pixhawk trabajan
a 3,3 V y la Pi también, pero conviene confirmarlo antes de conectar.

**Compañera ↔ radio de distancias.** Por SPI o por serie según el módulo. El
lector incluido espera líneas de texto `indice,distancia_en_metros`.

**Malla.** La carga útil del propio enlace de banda ultraancha, o una radio
dedicada. El código habla UDP multidifusión: le da lo mismo qué hay debajo.

### Ubicación física de la antena

Importa más de lo que parece. La antena de la radio de distancias necesita línea
de vista hacia los otros drones: si queda tapada por la batería o por un brazo,
las mediciones se alargan y el detector ve deformaciones que no existen. Lo mejor
suele ser un mástil corto por encima del fuselaje.

---

## 3. Preparar la computadora compañera

Sobre Raspberry Pi OS Lite de 64 bits:

```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git
sudo raspi-config        # Interface Options → Serial Port
                         #   consola de acceso: NO
                         #   hardware serie:    SÍ
```

Ese paso del `raspi-config` es el que más se olvida: si la consola de acceso
queda habilitada sobre el puerto serie, Linux escribe mensajes de arranque por el
mismo cable que usa MAVLink y el enlace no levanta nunca.

```bash
sudo mkdir -p /opt/formacion-cerrada && sudo chown "$USER" /opt/formacion-cerrada
git clone https://github.com/hernan2292/formacion-cerrada.git /opt/formacion-cerrada
cd /opt/formacion-cerrada
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-aeronave.txt
python -m pytest -q        # las 34 pruebas tienen que pasar acá también
```

Que las pruebas pasen **en la Pi** y no sólo en la máquina de desarrollo no es
ceremonia: valida que numpy está compilado para esa arquitectura y que el
rendimiento alcanza.

---

## 4. Clave de la formación

Sin esto, la capa 3 es decorativa: tolera que un integrante mienta, pero
cualquiera que alcance la red puede hacerse pasar por un integrante.

**En tierra, una sola vez por formación:**

```bash
python -c "from formacion_cerrada.malla.autenticacion import generar_clave; \
           import sys; sys.stdout.buffer.write(generar_clave())" > clave
```

**En cada aeronave:**

```bash
sudo mkdir -p /etc/formacion-cerrada
sudo cp clave /etc/formacion-cerrada/clave
sudo chmod 600 /etc/formacion-cerrada/clave
sudo chown root:root /etc/formacion-cerrada/clave
```

La misma clave en los cinco drones, y en ningún lado más. El sistema se niega a
arrancar si el archivo es legible por otros usuarios.

> La clave está en `.gitignore`. Si alguna vez termina en un commit, hay que
> generar una nueva: rotarla cuesta cinco minutos, y una clave filtrada anula
> toda la autenticación.

---

## 5. Configuración

`/etc/formacion-cerrada/aeronave.yaml`, distinto en cada dron sólo por `indice`
e `identificador`:

```yaml
indice: 0                 # 0 a 4, ÚNICO en la formación
total_nodos: 5
identificador: "D-01"

modo: observacion         # no cambiar sin completar la etapa 4 de validación

mavlink_dispositivo: /dev/serial0
mavlink_baudios: 921600

uwb_dispositivo: /dev/ttyUSB0
uwb_baudios: 115200

malla_grupo: "239.7.7.7"
malla_puerto: 47700
malla_interfaz: "0.0.0.0"
clave_malla: /etc/formacion-cerrada/clave

hercios: 10.0
directorio_registro: /var/log/formacion-cerrada
registrar_crudos: true
```

**Dos índices repetidos rompen la formación en silencio**: los dos nodos se
pisan en la matriz de distancias y la geometría deja de cerrar sin que nada avise
con claridad. Verificar los cinco antes de volar.

---

## 6. Validación por etapas

No saltear etapas. Cada una supone que la anterior pasó.

### Etapa 1 — En escritorio, sin hardware

```bash
python -m pytest -q                              # 34 pruebas
python -m formacion_cerrada.puente.servidor      # el banco, con el tablero
```

Inyectar los cuatro ataques y verificar el antes y después. **Criterio:** la
traslación coherente nunca dispara la capa 1 y siempre dispara la capa 2.

### Etapa 2 — Interfaces contra hardware, en banco

Con la controladora alimentada por USB y **sin hélices**:

```bash
python - <<'EOF'
from formacion_cerrada.puente.aeronave import TelemetriaMavlink
t = TelemetriaMavlink("/dev/serial0", 921600)
for _ in range(50):
    print(t.leer())
EOF
```

**Criterio:** posición y velocidad coherentes, y —lo que más se equivoca— que
**la altura suba cuando levantás la controladora con la mano**. MAVLink informa
en norte/este/abajo y el sistema trabaja en este/norte/arriba; si el signo quedó
mal, la altura baja al subir. Es el error clásico y se detecta en diez segundos.

Lo mismo con la radio:

```bash
python - <<'EOF'
from formacion_cerrada.puente.aeronave import DistanciasUwb
d = DistanciasUwb("/dev/ttyUSB0", 115200)
for _ in range(50):
    print(d.leer())
EOF
```

**Criterio:** con dos módulos separados tres metros medidos con cinta, la lectura
tiene que dar 3 m ± 20 cm.

### Etapa 3 — Enjambre en tierra

Cinco nodos encendidos, sin hélices, dispuestos en el patio con la geometría de
la formación. Correr el lazo completo en modo `observacion` durante veinte
minutos.

**Criterios:**
- Los cinco residuos por debajo de 2 m de forma sostenida.
- Postura `NOMINAL` en más del 95 % de los ciclos.
- `rechazados_malla` en cero. Si sube, hay alguien más transmitiendo, o las
  claves no coinciden.
- Los cinco nodos se ven entre sí: `visibles` igual a 5.

Mover un nodo dos metros a mano y verificar que su residuo sube y que los otros
cuatro no. Ése es el sistema funcionando.

### Etapa 4 — Vuelo en observación

Modo `observacion`, formación real, sin inyectar ningún ataque. Escalonar las
alturas: **la formación no puede ser coplanar**, o la reconstrucción geométrica
queda mal condicionada. Dos o tres metros entre escalones alcanzan, y el
escalonamiento tiene que ser independiente del desplazamiento lateral — una
altura proporcional a la posición lateral no rompe la coplanaridad, sólo inclina
el plano.

**Criterios:** los mismos de la etapa 3, en vuelo, sin falsos positivos
sostenidos.

Recién superada esta etapa tiene sentido discutir un modo que actúe. Y antes de
habilitarlo hay que construir dos cosas que **no están implementadas**: el
enclavamiento de autorización del operador de tierra para el modo asistido, y el
acoplamiento con el script Lua de la controladora para el cambio de fuente.

### Etapa 5 — Inyección controlada

Sólo en espacio aéreo segregado, con autorización, y con un operador capaz de
tomar control manual en cualquier momento.

Generar el engaño con un simulador de señal en recinto blindado o en banda
autorizada. **Emitir señales satelitales falsas al aire libre es ilegal en
prácticamente toda jurisdicción** y afecta a todo receptor en varios kilómetros a
la redonda, incluida la aviación tripulada. Esto se hace con permiso y en un
entorno controlado, o no se hace.

---

## 7. Arranque automático

`/etc/systemd/system/formacion-cerrada.service`:

```ini
[Unit]
Description=Formacion Cerrada - deteccion de falsificacion satelital
After=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/formacion-cerrada
ExecStart=/opt/formacion-cerrada/.venv/bin/python -m formacion_cerrada.puente.aeronave \
          --config /etc/formacion-cerrada/aeronave.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now formacion-cerrada
journalctl -u formacion-cerrada -f
```

`Restart=on-failure` y no `always` es deliberado: si el servicio se cae por una
configuración inválida, tiene que quedarse caído y visible, no reintentar en
bucle escondiendo el problema.

---

## 8. Qué mirar en vuelo

Cada ciclo se registra en `/var/log/formacion-cerrada/vuelo-*.jsonl`. Los campos
que importan:

| Campo | Qué dice | Cuándo preocuparse |
|---|---|---|
| `estado` | Postura de este nodo | Cualquier cosa distinta de NOMINAL sostenida |
| `residuos` | Residuo de cada dron | Por encima de 2 m sin ataque conocido |
| `capa2_global` | Anclas caídas en todo el enjambre | **Verdadero = traslación coherente** |
| `anclas_caidas` | Cuántos nodos discrepan a la vez | 3 o más sobre 5 |
| `rechazados_malla` | Mensajes que no pasaron la firma | **Si sube, alguien está transmitiendo** |
| `visibles` | Nodos que llegaron por la malla | Menos de 4 deja al sistema ciego |

`rechazados_malla` merece atención especial. En una formación sana con las claves
bien cargadas ese contador se queda en cero toda la salida. Que empiece a subir
significa una de dos cosas: una clave desincronizada, o alguien inyectando
tráfico en la banda de la malla. Las dos hay que investigarlas en tierra.

---

## 9. Problemas frecuentes

**No levanta el enlace con la controladora.** Revisar que la consola de acceso
esté deshabilitada sobre el puerto serie, que TX y RX estén cruzados, y que la
velocidad coincida con `SERIAL2_BAUD` en la controladora.

**`visibles` se queda en 1.** Los nodos no se ven. Verificar que estén en la
misma red, que el multicast no esté bloqueado, y —lo más probable— que la clave
sea idéntica en los cinco. Una clave distinta produce silencio, no error:
los mensajes llegan y se descartan, y eso aparece como `rechazados_malla`
subiendo.

**Residuos altos sin ataque.** Casi siempre es la geometría: formación coplanar,
o un nodo muy separado de los demás. Verificar el escalonamiento en altura y que
ningún par supere el alcance útil de la radio.

**El sistema se niega a arrancar.** Lee lo que dice: valida el índice, el número
de nodos, la existencia de la clave y sus permisos. Todos esos rechazos son
deliberados — arrancar con una configuración insegura es peor que no arrancar.

---

## 10. Lo que falta para uso operativo

Con honestidad, y en orden de importancia:

1. **Cohesión de formación en repliegue.** Cinco estimaciones inerciales
   independientes no sostienen la geometría más allá de unos veinte segundos.
   Es el problema que hay que resolver primero.
2. **Validar las tres interfaces de hardware** contra el equipamiento real. Nada
   de lo que está en la etapa 2 se ejecutó nunca.
3. **Enclavamiento de autorización** para el modo asistido, y acoplamiento con
   el script Lua de la controladora. Sin esto no hay modo que actúe.
4. **Ancla de sesgo de reloj del receptor.** Cierra el hueco del plano
   horizontal, donde hoy sólo muerde la inercial.
5. **Referencia temporal propia del enjambre**, disciplinada con los
   intercambios de la radio en vez de con el tiempo satelital.
6. **Banco de 500 corridas con curvas** de detección contra falsos positivos.

---

*Formación Cerrada · GPL-3.0-or-later · Eje 1 CYBER.AR 2026*
