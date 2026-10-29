"""Voci del viewer nei gruppi delle schede Leggi e Visualizza (barra in stile Excel).

Le parti del viewer si installano nell'ordine di `PARTS`, che non è quello in cui le voci devono
comparire nei gruppi: in «Mostra», per esempio, «Rileva URL nel testo» arriva per prima con la
navigazione ma va dopo Miniature e Coordinate e griglia. `place` mette ogni voce dopo quelle del
nucleo e prima delle voci del viewer dello stesso gruppo con un `order` più alto.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QToolButton

GROUP_PROPERTY = "viewerGroup"
ORDER_PROPERTY = "viewerGroupOrder"


def _mark(action: QAction, group: str, order: int) -> None:
    action.setProperty(GROUP_PROPERTY, group)
    action.setProperty(ORDER_PROPERTY, int(order))


def _anchor(toolbar, group: str, order: int) -> QAction | None:
    """La prima voce del viewer nel gruppo con un ordine più alto (None = in fondo al gruppo)."""
    for act in toolbar.actions():
        if act.property(GROUP_PROPERTY) != group:
            continue
        other = act.property(ORDER_PROPERTY)
        if other is not None and int(other) > order:
            return act
    return None


def place(ctx, mode_id: str, action: QAction, group: str, order: int, *, compact: bool = False,
          icon_text: str | None = None) -> QToolButton | None:
    """Aggiunge `action` al gruppo `group` (id del nucleo, es. "vai") della scheda `mode_id`.

    `compact=True` mostra solo l'icona; `icon_text` è l'etichetta più corta nella barra (menu,
    scheda File e ricerca dei comandi usano `text()`). Un'azione con menu diventa un pulsante ▾
    che apre il menu al clic. Restituisce il pulsante (None se la scheda non c'è)."""
    mode = ctx.mode(mode_id)
    if mode is None:
        return None
    if icon_text:
        action.setIconText(icon_text)
    anchor = _anchor(mode.toolbar, group, order)
    _mark(action, group, order)
    if anchor is None:
        mode.add_action(action, group=group, compact=compact)
    else:
        mode.toolbar.insertAction(anchor, action)
    button = mode.toolbar.widgetForAction(action)
    if isinstance(button, QToolButton):
        if compact:
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        if action.menu() is not None:
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    return button


def place_menu_button(ctx, mode_id: str, text: str, icon_name: str, actions: list, group: str, order: int, *,
                      compact: bool = False, requires_document: bool = True) -> QToolButton | None:
    """Pulsante ▾ con un menu (`Mode.add_menu_button`) in fondo al gruppo `group`; le voci aggiunte
    dopo con `place` e un ordine più basso finiscono prima. Restituisce il pulsante."""
    mode = ctx.mode(mode_id)
    if mode is None:
        return None
    button = mode.add_menu_button(text, icon_name, actions, group=group, compact=compact,
                                  requires_document=requires_document)
    act = toolbar_action(mode, button)
    if act is not None:
        _mark(act, group, order)
    return button


def toolbar_action(mode, widget) -> QAction | None:
    """L'azione della barra che contiene `widget` (per `group_title` e simili)."""
    for act in mode.toolbar.actions():
        if mode.toolbar.widgetForAction(act) is widget:
            return act
    return None
