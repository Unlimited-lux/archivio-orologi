"""Test della verifica dei collegamenti (OBJ-42): logica e pannello."""

from __future__ import annotations

import csv
import io
import socket
import urllib.error
import urllib.request

import pymupdf
import pytest
from PySide6.QtGui import QGuiApplication

from foliant.modules.objects import linkcheck as LC
from foliant.modules.objects import linkpanel as LP
from foliant.modules.objects import links as L

from .conftest import new_pdf

R = pymupdf.Rect(50, 50, 200, 80)


@pytest.fixture
def no_network(monkeypatch):
    """Qualunque accesso alla rete fa fallire il test."""
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("accesso alla rete non consentito")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    return calls


def broken_doc(tmp_path) -> pymupdf.Document:
    """40 pagine: collegamento a «pagina 50» (oggetto non pagina), a [49 /Fit],
    a un file inesistente, a un file esistente, a un URL e a una pagina valida."""
    pdf = new_pdf(40)
    # pagina 50 inesistente: /Dest verso un oggetto che non è una pagina
    x1 = L.create_link(pdf, 0, R, L.LinkTarget(kind=L.PAGE, page=1, fit="Fit"))
    ghost = pdf.get_new_xref()
    pdf.update_object(ghost, "<< /Type /Page >>")
    pdf.xref_set_key(x1, "Dest", f"[{ghost} 0 R /Fit]")
    # forma non standard con numero di pagina diretto
    x2 = L.create_link(pdf, 1, R, L.LinkTarget(kind=L.PAGE, page=1, fit="Fit"))
    pdf.xref_set_key(x2, "Dest", "[49 /Fit]")
    L.create_link(pdf, 2, R, L.LinkTarget(kind=L.FILE, file="manca/assente.pdf", page=0))
    (tmp_path / "presente.txt").write_text("x")
    L.create_link(pdf, 3, R, L.LinkTarget(kind=L.LAUNCH, file="presente.txt"))
    L.create_link(pdf, 4, R, L.LinkTarget(kind=L.URI, uri="https://example.com/a"), description="Sito")
    L.create_link(pdf, 5, R, L.LinkTarget(kind=L.PAGE, page=10, fit="Fit"))
    return pdf


def by_page(rows):
    return {r.page: r for r in rows}


# ------------------------------------------------------------------ logica
def test_missing_page_and_file_are_broken_without_network(tmp_path, no_network):
    pdf = broken_doc(tmp_path)
    assert pdf.page_count == 40
    rows = by_page(LC.check_document(pdf, str(tmp_path / "doc.pdf")))
    assert rows[0].status == LC.BROKEN and rows[0].kind == LC.INTERNAL
    assert rows[1].status == LC.BROKEN
    assert rows[2].status == LC.BROKEN and rows[2].kind == LC.FILE
    assert rows[3].status == LC.OK
    assert rows[4].status == LC.UNCHECKED and rows[4].kind == LC.WEB and rows[4].description == "Sito"
    assert rows[5].status == LC.OK
    assert no_network == []


def test_named_destinations(no_network):
    pdf = new_pdf(3)
    x = L.create_link(pdf, 0, R, L.LinkTarget(kind=L.PAGE, page=1))
    pdf.xref_set_key(x, "Dest", "(manca)")
    rows = LC.check_document(pdf)
    assert rows[0].kind == LC.INTERNAL and rows[0].status == LC.BROKEN
    # destinazione con nome definita
    names = pdf.get_new_xref()
    pdf.update_object(names, f"<< /Names [(cap3) [{pdf.page_xref(2)} 0 R /Fit]] >>")
    pdf.xref_set_key(pdf.pdf_catalog(), "Names", f"<< /Dests {names} 0 R >>")
    pdf.xref_set_key(x, "Dest", "(cap3)")
    assert LC.check_document(pdf)[0].status == LC.OK


def test_uri_malformed_other_and_relative_unsaved(no_network):
    pdf = new_pdf(3)
    x = L.create_link(pdf, 0, R, L.LinkTarget(kind=L.URI, uri="https://example.com"))
    pdf.xref_set_key(x, "A", "<< /S /URI /URI (javascript:alert(1)) >>")
    x2 = L.create_link(pdf, 1, R, L.LinkTarget(kind=L.URI, uri="https://example.com"))
    pdf.xref_set_key(x2, "A", "<< /S /JavaScript /JS (app.alert(1)) >>")
    L.create_link(pdf, 2, R, L.LinkTarget(kind=L.FILE, file="rel.pdf", page=0))
    rows = by_page(LC.check_document(pdf, None))
    assert rows[0].status == LC.SUSPECT
    assert rows[1].status == LC.UNCHECKABLE and rows[1].kind == LC.OTHER
    assert rows[2].status == LC.UNCHECKED


def test_no_links_empty():
    assert LC.check_document(new_pdf(2)) == []
    assert LC.to_csv([]).splitlines() == ["pagina,tipo,destinazione,stato"]


def test_csv_one_row_per_link(tmp_path, no_network):
    pdf = broken_doc(tmp_path)
    rows = LC.check_document(pdf, str(tmp_path / "doc.pdf"))
    text = LC.to_csv(rows)
    data = list(csv.reader(io.StringIO(text)))
    assert data[0] == ["pagina", "tipo", "destinazione", "stato"]
    assert len(data) == len(rows) + 1 == 7
    assert data[1][0] == "1" and data[1][3] == "rotto"
    out = tmp_path / "r.csv"
    LC.write_csv(rows, out)
    assert out.read_bytes().decode("utf-8") == text


class FakeResp:
    def __init__(self, url, status=200):
        self.url, self.status = url, status

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def close(self):
        pass


def fake_opener(log):
    def opener(req, timeout=None):
        url = req.full_url
        log.append((req.get_method(), url, timeout))
        if "ok" in url:
            return FakeResp(url)
        if "redir" in url:
            return FakeResp("https://example.com/nuovo")
        if "missing" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "nohead" in url:
            if req.get_method() == "HEAD":
                raise urllib.error.HTTPError(url, 405, "Method Not Allowed", {}, None)
            return FakeResp(url)
        if "slow" in url:
            raise urllib.error.URLError(socket.timeout("timed out"))
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)
    return opener


def web_rows():
    urls = ["https://example.com/ok", "https://example.com/redir", "https://example.com/missing",
            "https://example.com/nohead", "https://example.com/slow", "https://example.com/boom",
            "https://example.com/ok", "mailto:a@example.com"]
    return [LC.LinkRow(i, 10 + i, LC.WEB, u, "", LC.UNCHECKED, "") for i, u in enumerate(urls)]


def test_online_check_with_fake_opener(no_network):
    log = []
    rows = LC.check_urls_online(web_rows(), opener=fake_opener(log), timeout=10)
    st = [r.status for r in rows]
    assert st == [LC.OK, LC.REDIRECTED, LC.NOT_FOUND, LC.OK, LC.TIMEOUT, LC.ERROR, LC.OK, LC.UNCHECKED]
    # URL ripetuti richiesti una volta sola; timeout 10 s; nessuna richiesta per mailto
    assert sum(1 for m, u, _t in log if u.endswith("/ok")) == 1
    assert all(t == 10 for _m, _u, t in log)
    assert not any("mailto" in u for _m, u, _t in log)
    assert ("GET", "https://example.com/nohead", 10) in log
    assert no_network == []


def test_online_check_cancel(no_network):
    log = []
    rows = LC.check_urls_online(web_rows(), opener=fake_opener(log), cancel=lambda: True)
    assert log == [] and all(r.status == LC.UNCHECKED for r in rows)


# ------------------------------------------------------------------ pannello
@pytest.fixture
def ui(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(window.ctx, "error", lambda *a: None)
    return window


def open_doc(window, pdf, path):
    pdf.save(str(path))
    pdf.close()
    view = window.open_path(str(path))
    QGuiApplication.processEvents()
    return view


def test_panel_rows_and_activation(ui, tmp_path, no_network, monkeypatch):
    view = open_doc(ui, broken_doc(tmp_path), tmp_path / "doc.pdf")
    panel = LP.LinkCheckPanel()
    pages = []
    monkeypatch.setattr(view, "go_to_page", lambda p, *a: pages.append(p))
    activated = []
    panel.on_activate = lambda p, x: activated.append((p, x))
    panel.set_view(view)
    assert panel.table.rowCount() == 6
    assert panel.stack.currentWidget() is panel.table
    statuses = [panel.table.item(r, 4).text() for r in range(6)]
    assert statuses.count("rotto") == 3
    # filtro «solo problemi»
    panel.filter.setCurrentIndex(1)
    assert panel.table.rowCount() == 3
    panel.filter.setCurrentIndex(0)
    # doppio clic sulla riga della pagina 3
    row = next(r for r in range(6) if panel.table.item(r, 0).text() == "3")
    panel.table.cellDoubleClicked.emit(row, 2)
    assert pages == [2] and activated and activated[0][0] == 2
    # nessuna verifica online senza consenso
    monkeypatch.setattr(LP, "ask_online_consent", lambda parent: False)
    assert panel.check_online() is False
    assert no_network == []
    panel.set_view(None)
    assert panel.table.rowCount() == 0
    panel.deleteLater()


def test_panel_empty_message(ui, tmp_path):
    view = open_doc(ui, new_pdf(2), tmp_path / "vuoto.pdf")
    panel = LP.LinkCheckPanel(ui.ctx)
    panel.set_view(view)
    assert panel.table.rowCount() == 0
    assert panel.stack.currentWidget() is panel.empty
    assert panel.empty.text() == "Nessun collegamento nel documento"
    assert not panel.export_btn.isEnabled()
    panel.deleteLater()


def test_panel_online_and_export(ui, tmp_path, no_network, monkeypatch):
    pdf = new_pdf(2)
    L.create_link(pdf, 0, R, L.LinkTarget(kind=L.URI, uri="https://example.com/ok"))
    L.create_link(pdf, 1, R, L.LinkTarget(kind=L.URI, uri="https://example.com/missing"))
    view = open_doc(ui, pdf, tmp_path / "web.pdf")
    panel = LP.LinkCheckPanel(ui.ctx)
    panel.set_view(view)
    log = []
    real = LC.check_urls_online
    monkeypatch.setattr(LC, "check_urls_online",
                        lambda rows, **kw: real(rows, **{**kw, "opener": fake_opener(log)}))
    monkeypatch.setattr(LP, "ask_online_consent", lambda parent: True)
    monkeypatch.setenv("FOLIANT_SYNC_TASKS", "1")
    assert panel.check_online() is True
    assert len(log) == 2
    assert sorted(r.status for r in panel.rows) == [LC.OK, LC.NOT_FOUND]
    texts = sorted(panel.table.item(r, 4).text() for r in range(2))
    assert texts == ["OK", "non trovato"]
    out = tmp_path / "rapporto.csv"
    monkeypatch.setattr(LP, "ask_csv_path", lambda parent: str(out))
    assert panel.export_csv() == str(out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and lines[0] == "pagina,tipo,destinazione,stato"
    panel.deleteLater()
