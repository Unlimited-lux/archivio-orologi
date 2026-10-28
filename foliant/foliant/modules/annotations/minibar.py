"""Mini-barra contestuale sopra l'annotazione selezionata (ANN-03): colore, commento,
proprietà, elimina."""

from __future__ import annotations

from typing import TYPE_CHECKING

import shiboken6

from PySide6.QtCore import QPoint, QSize
from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton

from foliant.api import _, bind_icon, theme

from .panels import ColorButton

if TYPE_CHECKING:
    from .controller import Controller


class MiniBar(QFrame):
    def __init__(self, ctrl: "Controller", parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.view = None
        self.setObjectName("annotationMiniBar")
        self.setStyleSheet(f"#annotationMiniBar {{ background: {theme.hex_color('surface')};"
                           f" border: 1px solid {theme.hex_color('border')}; border-radius: 6px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)
        self.color = ColorButton()
        self.color.setFlat(True)
        self.color.callback = lambda c: self.ctrl.set_style(color=c)
        lay.addWidget(self.color)
        for icon_name, tip, slot in (("message-square-text", _("Commento"), self._comment),
                                     ("sliders-horizontal", _("Proprietà"), self._properties),
                                     ("trash-2", _("Elimina"), self._delete)):
            b = QToolButton(self)
            b.setAutoRaise(True)
            b.setIconSize(QSize(16, 16))
            bind_icon(b, icon_name)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            lay.addWidget(b)
        self.hide()
        ctrl.selectionChanged.connect(self.reposition)

    def attach(self, view) -> None:
        if self.view is view:
            return
        self.detach()
        self.view = view
        self.setParent(view.viewport())
        for sig in self._signals(view):
            sig.connect(self.reposition)
        self.hide()

    def detach(self) -> None:
        """Si stacca dalla vista corrente (prima che venga chiusa e distrutta con i suoi figli)."""
        view, self.view = self.view, None
        self.hide()
        if view is None:
            return
        if shiboken6.isValid(view):
            for sig in self._signals(view):
                try:
                    sig.disconnect(self.reposition)
                except (RuntimeError, TypeError):
                    pass
        self.setParent(None)

    @staticmethod
    def _signals(view):
        return (view.verticalScrollBar().valueChanged, view.horizontalScrollBar().valueChanged,
                view.layoutChanged, view.contentChanged)

    def reposition(self, *_a) -> None:
        view = self.view
        sel = self.ctrl.selection
        if view is None or self.ctrl.selection_view() is not view or len(sel) != 1:
            self.hide()
            return
        page, xref = sel[0]
        info = self.ctrl.info(page, xref, view=view)
        if info is None or page >= view.page_count:
            self.hide()
            return
        r = view.page_to_view(page, info.rect)
        self.color.set_color(info.color)
        self.adjustSize()
        x = int(r.center().x() - self.width() / 2)
        y = int(r.top() - self.height() - 8)
        if y < 4:
            y = int(r.bottom() + 8)
        vp = view.viewport()
        x = max(4, min(x, vp.width() - self.width() - 4))
        y = max(4, min(y, vp.height() - self.height() - 4))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()

    def _comment(self) -> None:
        if len(self.ctrl.selection) == 1:
            self.ctrl.edit_text(*self.ctrl.selection[0], view=self.view)

    def _properties(self) -> None:
        self.ctrl.ctx.show_properties("annotations")

    def _delete(self) -> None:
        self.ctrl.delete()
        self.reposition()
