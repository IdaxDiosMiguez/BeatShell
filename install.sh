#!/usr/bin/env bash
# Instalador de BeatShell para macOS y Linux.
# Crea un entorno virtual dentro del proyecto y un comando `beatshell` en ~/.local/bin.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "✖ Necesitas Python 3.10 o superior (https://www.python.org/downloads/)."
  exit 1
fi
echo "▶ Usando $($PY --version)"

"$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
# Modo editable: el comando usa el código de esta carpeta, así un `git pull` basta para actualizar.
.venv/bin/python -m pip install --quiet -e .
echo "✔ Dependencias instaladas"

mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/.venv/bin/beatshell" "$HOME/.local/bin/beatshell"
echo "✔ Comando creado: ~/.local/bin/beatshell"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "  ⚠ Añade ~/.local/bin a tu PATH:  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
esac

# Comprobaciones de dependencias del sistema
if ! .venv/bin/python -c "import sounddevice" >/dev/null 2>&1; then
  echo "  ⚠ Falta PortAudio (necesario para el sonido):"
  echo "      Debian/Ubuntu:  sudo apt install libportaudio2"
  echo "      Fedora:         sudo dnf install portaudio"
  echo "      Arch:           sudo pacman -S portaudio"
fi
if ! command -v deno >/dev/null 2>&1 && ! command -v node >/dev/null 2>&1 && ! command -v bun >/dev/null 2>&1; then
  echo "  ⚠ YouTube necesita un runtime de JavaScript. Instala Deno:"
  echo "      macOS:  brew install deno"
  echo "      Linux:  curl -fsSL https://deno.land/install.sh | sh"
fi

echo
echo "Listo. Ejecuta:  beatshell"
