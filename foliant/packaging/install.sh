#!/usr/bin/env bash
# Installa Foliant per l'utente corrente su Ubuntu/Debian.
#   ./packaging/install.sh                 installazione
#   ./packaging/install.sh --default-pdf   e imposta Foliant come lettore PDF predefinito
#   ./packaging/install.sh --no-apt        salta l'installazione dei pacchetti di sistema
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${HOME}/.local/share/foliant"
BIN_DIR="${HOME}/.local/bin"
APPS_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
DEFAULT_PDF=0
USE_APT=1
for arg in "$@"; do
  case "$arg" in
    --default-pdf) DEFAULT_PDF=1 ;;
    --no-apt) USE_APT=0 ;;
    *) echo "Opzione sconosciuta: $arg" >&2; exit 2 ;;
  esac
done

if [ "$USE_APT" -eq 1 ] && command -v apt-get >/dev/null 2>&1; then
  echo "==> Pacchetti di sistema (potrebbe essere richiesta la password di amministratore)"
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip \
    tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng \
    libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libfontconfig1 libdbus-1-3
  if ! command -v soffice >/dev/null 2>&1; then
    echo "    Suggerimento: per creare PDF da documenti Office installa LibreOffice:"
    echo "    sudo apt-get install -y libreoffice-writer libreoffice-calc libreoffice-impress"
  fi
fi

echo "==> Ambiente Python in ${PREFIX}"
mkdir -p "$PREFIX"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip >/dev/null
"$PREFIX/venv/bin/pip" install "$APP_DIR"

echo "==> Comando, icona e voce nel menu delle applicazioni"
mkdir -p "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
ln -sf "$PREFIX/venv/bin/foliant" "$BIN_DIR/foliant"
install -m 644 "$APP_DIR/foliant/resources/foliant.svg" "$ICON_DIR/foliant.svg"
sed "s|@EXEC@|$PREFIX/venv/bin/foliant|" "$APP_DIR/packaging/foliant.desktop" > "$APPS_DIR/foliant.desktop"
chmod 644 "$APPS_DIR/foliant.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "${HOME}/.local/share/icons/hicolor" || true

if [ "$DEFAULT_PDF" -eq 1 ] && command -v xdg-mime >/dev/null 2>&1; then
  xdg-mime default foliant.desktop application/pdf
  echo "    Foliant è ora il lettore PDF predefinito."
fi

echo
echo "Foliant è installato. Avvialo dal menu delle applicazioni oppure con: foliant"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Nota: aggiungi $BIN_DIR al PATH per usare il comando 'foliant' dal terminale." ;;
esac
