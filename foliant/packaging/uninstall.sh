#!/usr/bin/env bash
# Rimuove Foliant installato con install.sh (le impostazioni dell'utente restano).
set -euo pipefail
rm -rf "${HOME}/.local/share/foliant"
rm -f "${HOME}/.local/bin/foliant" \
      "${HOME}/.local/share/applications/foliant.desktop" \
      "${HOME}/.local/share/icons/hicolor/scalable/apps/foliant.svg" \
      "${HOME}/.local/share/icons/hicolor/symbolic/apps/foliant-symbolic.svg"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "${HOME}/.local/share/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "${HOME}/.local/share/icons/hicolor" || true
echo "Foliant è stato rimosso."
