#!/usr/bin/env bash
# Levanta el nucleo de deteccion para la demostracion.
#
# Es un solo comando a proposito: en una presentacion cronometrada, dos
# terminales y un orden que recordar son un punto de falla evitable.
set -euo pipefail
cd "$(dirname "$0")"

if ! python -c "import numpy, websockets" 2>/dev/null; then
  echo "Faltan dependencias. Instalando..."
  python -m pip install -r requirements.txt
fi

echo "Nucleo de deteccion -> ws://localhost:8765"
echo "Tablero: levantarlo aparte con 'npm run dev' en el repositorio del tablero."
exec python -m formacion_cerrada.puente.servidor
