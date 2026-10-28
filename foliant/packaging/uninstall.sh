#!/usr/bin/env bash
# Rimuove Foliant installato con install.sh (le impostazioni dell'utente restano).
set -euo pipefail
rm -rf "${HOME}/.local/share/foliant"
rm -f "${HOME}/.local/bin/foliant" \
      "${HOME}/.local/share/applications/foliant.desktop" \
      "${HOME}/.local/share/icons/hicolor/scalable/apps/foliant.svg"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "${HOME}/.local/share/applications" || true
echo "Foliant è stato rimosso."
