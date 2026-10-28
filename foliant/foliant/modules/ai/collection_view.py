"""Finestra «Più documenti» (AI-13)."""

from __future__ import annotations

import html

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from foliant.api import FoliantError, _, bind_icon, theme

from . import collection, render
from .collection import Collection, CollectionStore
from .engine import ChatMessage, Conversation


class CollectionDialog(QDialog):
    """`run_chat(conv, prepared, question) -> Answer | None`; `open_ref(path, page)`; `ask_paths() -> list`."""

    def __init__(self, ctx, run_chat, open_ref, ask_paths, open_documents: dict, parent=None, *, cache=None,
                 store: CollectionStore | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.run_chat = run_chat
        self.open_ref = open_ref
        self.ask_paths = ask_paths
        self.open_documents = open_documents
        self.cache = cache
        self.store = store or CollectionStore()
        self.coll = Collection(name=_("Raccolta"))
        self.conv = Conversation(function="chat-collection")
        self.coll.conversations.append(self.conv)
        self._spaces = []
        self.setWindowTitle(_("Chat su più documenti"))
        self.resize(720, 640)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.name = QLineEdit(self.coll.name)
        self.saved = QComboBox()
        self.saved.setToolTip(_("Raccolte di questa sessione: non vengono salvate su disco."))
        self._fill_saved()
        self.saved.currentIndexChanged.connect(self._open_saved)
        save = QPushButton(_("Conserva raccolta"))
        save.setToolTip(_("Conserva la raccolta fino alla chiusura di Foliant (nulla viene salvato su disco)."))
        bind_icon(save, "save")
        save.clicked.connect(self.save)
        row.addWidget(QLabel(_("Nome")))
        row.addWidget(self.name, 1)
        row.addWidget(self.saved)
        row.addWidget(save)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.setMaximumHeight(140)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        add_open = QPushButton(_("Aggiungi le schede aperte"))
        add_files = QPushButton(_("Aggiungi file…"))
        remove = QPushButton(_("Rimuovi"))
        add_open.clicked.connect(self.add_open)
        add_files.clicked.connect(lambda: self.add_paths(self.ask_paths()))
        remove.clicked.connect(self.remove_selected)
        for b in (add_open, add_files, remove):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"color: {theme.hex_color('warning')};")
        lay.addWidget(self.notice)
        from .panel import SafeBrowser
        self.browser = SafeBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.on_link)
        lay.addWidget(self.browser, 1)
        row = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setMaximumHeight(64)
        self.input.setPlaceholderText(_("Domanda sulla raccolta…"))
        col = QVBoxLayout()
        send = QPushButton(_("Invia"))
        bind_icon(send, "send")
        send.clicked.connect(lambda: self.ask())
        summary = QPushButton(_("Riassumi la raccolta"))
        summary.clicked.connect(lambda: self.ask(_("Riassumi la raccolta nel suo insieme.")))
        compare = QPushButton(_("Confronta"))
        compare.clicked.connect(lambda: self.ask(_("Confronta i documenti: somiglianze e differenze principali.")))
        for b in (send, summary, compare):
            col.addWidget(b)
        row.addWidget(self.input, 1)
        row.addLayout(col)
        lay.addLayout(row)
        self.refresh()

    # ----------------------------------------------------------------- documenti
    def refresh(self) -> None:
        self.list.clear()
        names = collection.short_names(self.coll.members)
        for m in self.coll.members:
            state = _("escluso: {r}").format(r=m.excluded) if m.excluded else (
                _("modificato") if m.changed else _("pronto"))
            item = QListWidgetItem(f"{names.get(m.path, '?')} · {m.title} — {state}")
            self.list.addItem(item)
        self.render()

    def add_open(self) -> None:
        for path, doc in self.open_documents.items():
            self._add(path, doc)
        self.refresh()

    def add_paths(self, paths) -> None:
        for p in paths or []:
            self._add(p, self.open_documents.get(p))
        self.refresh()

    def _add(self, path, doc=None) -> None:
        try:
            self.coll.add(path, doc)
        except FoliantError as e:
            self.notice.setText(str(e))

    def remove_selected(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.coll.remove(row)
            self.refresh()

    # ----------------------------------------------------------------- domande
    def ask(self, question: str | None = None) -> None:
        q = (question or self.input.toPlainText()).strip()
        if not q:
            return
        if not self.coll.usable():
            self.notice.setText(_("Aggiungi almeno un documento utilizzabile."))
            return
        prepared = collection.prepare(self.coll.members, self.open_documents, self.cache)
        self._spaces = [space for _m, _p, space in prepared]
        self.refresh()
        answer = self.run_chat(self.conv, prepared, q)
        if answer is None:
            return
        self.conv.messages.append(ChatMessage("user", q, function="chat-collection"))
        self.conv.messages.append(ChatMessage("assistant", answer.text, status=answer.status, refs=answer.refs,
                                              flags=answer.flags, function="chat-collection"))
        self.input.clear()
        self.render()

    def render(self) -> None:
        colors = render._colors()
        parts = []
        for i, m in enumerate(self.conv.messages):
            if m.role == "user":
                parts.append(f"<p><b>{html.escape(_('Tu'))}</b><br>{html.escape(m.text)}</p>")
            else:
                body = render.render_markdown(m.text, self._spaces, known_refs=m.refs or None, prefix=f"{i}:").html
                flags = (f"<p style='color:{colors['warning']}'>" + html.escape(" · ".join(m.flags)) + "</p>"
                         if m.flags else "")
                parts.append(f"<div><b>{html.escape(_('Assistente'))}</b>{body}{flags}</div>")
        self.browser.setHtml("".join(parts))

    def on_link(self, url: QUrl) -> None:
        s = url.toString()
        if not s.startswith("page:"):
            return
        try:
            mi, ri = (int(x) for x in s[5:].split(":"))
            ref = self.conv.messages[mi].refs[ri]
        except (ValueError, IndexError):
            return
        if ref.get("verified") and ref.get("doc_key"):
            self.open_ref(ref["doc_key"], int(ref["page"]))

    # ----------------------------------------------------------------- raccolte della sessione
    def _fill_saved(self) -> None:
        self.saved.blockSignals(True)
        self.saved.clear()
        self.saved.addItem(_("Apri una raccolta di questa sessione…"), "")
        for c in self.store.load_all():
            self.saved.addItem(c.name or c.id, c.id)
        self.saved.blockSignals(False)

    def save(self) -> None:
        self.coll.name = self.name.text().strip() or self.coll.name
        self.store.save(self.coll)
        self.notice.setText(_("Raccolta conservata fino alla chiusura di Foliant."))
        self._fill_saved()

    def load(self, coll: Collection) -> None:
        self.coll = coll
        self.name.setText(coll.name)
        notes = coll.check(self.open_documents)
        if coll.conversations:
            self.conv = coll.conversations[-1]
            refs_spaces = []
            names = collection.short_names(coll.members)
            for m in coll.members:
                refs_spaces.append(render.PageSpace({}, set(), 0, m.path, names[m.path]))
            self._spaces = refs_spaces
        else:
            self.conv = Conversation(function="chat-collection")
            coll.conversations.append(self.conv)
        self.notice.setText(" ".join(notes))
        self.refresh()

    def _open_saved(self) -> None:
        cid = self.saved.currentData()
        if cid:
            c = self.store.get(cid)
            if c is not None:
                self.load(c)
