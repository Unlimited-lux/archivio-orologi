"""Istanza singola (NFR-31, VIS-53): un secondo avvio consegna file e comandi a Foliant già aperto.

Il primo processo ascolta su un `QLocalServer` con un nome per utente; un avvio successivo si
collega, invia la richiesta (JSON su una riga) e termina appena riceve «ok». Richiesta:
`{"files": [[percorso assoluto, pagina da 1 o null], …], "new_window": bool, "command": str | null}`.
"""

from __future__ import annotations

import json
import os

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

TIMEOUT_MS = 3000
MAX_REQUEST = 1 << 20          # byte: una richiesta più lunga viene scartata
COMMANDS = ("create-pdf", "merge")


def _private_dir(path: str) -> bool:
    """Cartella dell'utente corrente che nessun altro può leggere o scrivere (0700)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return os.path.isdir(path) and st.st_uid == os.getuid() and not st.st_mode & 0o077


def instance_name() -> str:
    """Canale dell'utente corrente, sempre in una cartella privata: XDG_RUNTIME_DIR o, se manca,
    la cartella di runtime che Qt crea con permessi 0700. Un nome semplice finirebbe nella /tmp
    condivisa, dove un altro utente potrebbe occuparlo prima e ricevere i percorsi dei file."""
    if not hasattr(os, "getuid"):                   # Windows: le named pipe sono già per sessione
        return f"foliant-instance-{os.environ.get('USERNAME', 'utente')}"
    from PySide6.QtCore import QStandardPaths
    candidates = [os.environ.get("XDG_RUNTIME_DIR") or "",
                  QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)]
    for folder in candidates:
        path = os.path.join(folder, "foliant-instance") if folder else ""
        if path and _private_dir(folder) and len(os.fsencode(path)) < 100:   # limite dei socket Unix
            return path
    return ""                                       # nessun canale sicuro: niente istanza singola


def clean_request(data) -> dict:
    """Richiesta ricevuta → forma attesa; ValueError se non è valida."""
    if not isinstance(data, dict):
        raise ValueError("richiesta non valida")
    files = []
    for item in data.get("files") or []:
        if not (isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str)):
            raise ValueError("file non valido")
        page = item[1]
        if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 1):
            raise ValueError("pagina non valida")
        files.append((item[0], page))
    command = data.get("command")
    if command is not None and command not in COMMANDS:
        raise ValueError("comando sconosciuto")
    return {"files": files, "new_window": bool(data.get("new_window")), "command": command}


def send_request(name: str, request: dict, timeout_ms: int = TIMEOUT_MS) -> bool:
    """Consegna `request` all'istanza in ascolto su `name`; False se non c'è o non risponde.

    Attende la risposta con un ciclo di eventi proprio e non con i `waitFor…` bloccanti: così
    funziona anche con il server nello stesso processo (test) e non blocca altri thread."""
    sock = QLocalSocket()
    loop = QEventLoop()
    reply = bytearray()
    finished = []

    def done(*_a) -> None:
        finished.append(True)
        loop.quit()

    def read() -> None:
        reply.extend(bytes(sock.readAll()))
        if b"\n" in reply:
            done()

    sock.connected.connect(lambda: sock.write(json.dumps(request).encode("utf-8") + b"\n"))
    sock.readyRead.connect(read)
    sock.errorOccurred.connect(done)
    sock.disconnected.connect(done)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(done)
    timer.start(timeout_ms)
    sock.connectToServer(name)
    if not finished and sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        loop.exec()
    timer.stop()
    if sock.isOpen():
        reply.extend(bytes(sock.readAll()))
    sock.abort()
    return bytes(reply).split(b"\n", 1)[0].strip() == b"ok"


class InstanceServer(QObject):
    """Riceve le richieste degli avvii successivi: `requestReceived(dict)` per ognuna."""

    requestReceived = Signal(object)

    def __init__(self, name: str, parent: QObject | None = None):
        super().__init__(parent)
        self.name = name
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)

    def listen(self) -> bool:
        """Inizia ad ascoltare; False se un'altra istanza attiva usa già lo stesso nome.

        Con i permessi solo per l'utente Qt crea il socket altrove e lo rinomina sul nome
        chiesto, sostituendo quello di un'istanza viva: per questo prima si prova a collegarsi."""
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(500):
            probe.abort()
            return False
        QLocalServer.removeServer(self.name)       # resto di un'istanza terminata male
        return self._server.listen(self.name)

    def is_listening(self) -> bool:
        return self._server.isListening()

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            buffer = bytearray()
            sock.readyRead.connect(lambda s=sock, b=buffer: self._read(s, b))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock: QLocalSocket, buffer: bytearray) -> None:
        buffer += bytes(sock.readAll())
        if len(buffer) > MAX_REQUEST:
            sock.abort()
            return
        if b"\n" not in buffer:
            return
        line = bytes(buffer).split(b"\n", 1)[0]
        buffer.clear()
        try:
            request = clean_request(json.loads(line.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            sock.disconnectFromServer()
            return
        sock.write(b"ok\n")
        sock.flush()
        sock.disconnectFromServer()
        self.requestReceived.emit(request)


def open_request(request: dict, settings, window=None):
    """Esegue una richiesta: i file vanno nella finestra attiva (una nuova con «new_window» o se
    non ce n'è nessuna); un file già aperto ne attiva la scheda. Restituisce la finestra."""
    from .main_window import active_window, open_window
    if window is None and not request.get("new_window"):
        window = active_window()
    if window is None:
        window = open_window(settings)
    window.open_files(request.get("files") or [])
    command = request.get("command")
    if command:
        QTimer.singleShot(0, window, lambda: window.run_command(command))   # dopo la risposta e la finestra
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
    return window
