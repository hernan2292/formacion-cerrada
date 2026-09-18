# Levanta el nucleo de deteccion para la demostracion.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

python -c "import numpy, websockets" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Faltan dependencias. Instalando..."
  python -m pip install -r requirements.txt
}

Write-Host "Nucleo de deteccion -> ws://localhost:8765"
Write-Host "Tablero: levantarlo aparte con 'npm run dev' en el repositorio del tablero."
python -m formacion_cerrada.puente.servidor
