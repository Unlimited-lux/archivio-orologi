"""Istanza singola (NFR-31, VIS-53): un secondo avvio consegna file e comandi a Foliant già aperto.

Il primo processo ascolta su un `QLocalServer` con un nome per utente; un avvio successivo si
collega, invia la richiesta (JSON su una riga) e termina appena riceve «ok». Richiesta:
`{"files": [[percorso assoluto, pagina da 1 o null], …], "new_window": bool, "command": str | null,
"activate": str | null}`.

Ogni processo ascolta anche su un canale proprio (`process_channel(pid)`): un altro processo che
trova un file già aperto qui (blocco di core/openlock.py, che dice il PID) chiede di mostrarne la
scheda con `{"activate": nome del blocco}`, senza il percorso (`show_in_process`, ui/open_files.py).
"""

from __future__ import annotations

import json
import os
import re

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..core import platform

TIMEOUT_MS = 3000
SHOW_TIMEOUT_MS = 1000         # richiesta di mostrare un file: se il processo è occupato arriva dopo
MAX_REQUEST = 1 << 20          # byte: una richiesta più lunga viene scartata
COMMANDS = ("create-pdf", "merge")
LOCK_NAME = re.compile(r"[0-9a-f]{64}")      # nome di un blocco (HMAC-SHA256 in esadecimale)
PROCESS_PREFIX = "foliant-"                  # canale di un processo: «foliant-<pid>»


def _private_dir(path: str) -> bool:
    """Cartella dell'utente corrente che nessun altro può leggere o scrivere (0700)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return os.path.isdir(path) and platform.owned_by_user(st) and not st.st_mode & 0o077


def _channel(name: str) -> str:
    """Percorso del canale `name` (socket Unix), sempre in una cartella privata: XDG_RUNTIME_DIR o,
    se manca, la cartella di runtime che Qt crea con permessi 0700. Un nome semplice finirebbe nella
    /tmp condivisa, dove un altro utente potrebbe occuparlo prima e ricevere i percorsi dei file."""
    from PySide6.QtCore import QStandardPaths
    candidates = [os.environ.get("XDG_RUNTIME_DIR") or "",
                  QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)]
    for folder in candidates:
        path = os.path.join(folder, name) if folder else ""
        if path and _private_dir(folder) and len(os.fsencode(path)) < 100:   # limite dei socket Unix
            return path
    return ""                                       # nessun canale sicuro


def _windows_pipe(kind: str, *extra) -> str:
    """Named pipe: lo spazio dei nomi è unico per la macchina, quindi nel nome vanno l'utente (SID)
    e la sessione di accesso; chi si collega verifica anche il proprietario del server."""
    sid = platform.windows_user_sid()
    if not sid:
        return ""
    return "-".join([f"foliant-{kind}", sid, str(platform.windows_session_id() or 0), *map(str, extra)])


def instance_name() -> str:
    """Canale dell'istanza singola dell'utente corrente ("" senza un canale sicuro: niente istanza
    singola, ogni avvio fa da sé)."""
    if platform.IS_WINDOWS:
        return _windows_pipe("instance")
    return _channel("foliant-instance")


def process_channel(pid: int) -> str:
    """Canale proprio del processo `pid` (richieste di mostrare un file già aperto); "" se manca."""
    if platform.IS_WINDOWS:
        return _windows_pipe("process", int(pid))
    return _channel(f"{PROCESS_PREFIX}{int(pid)}")


def remove_stale_channels() -> None:
    """Toglie i canali rimasti da processi terminati male (socket Unix: su Windows le named pipe
    spariscono con il processo)."""
    if platform.IS_WINDOWS:
        return
    folder = os.path.dirname(process_channel(os.getpid()))
    if not folder:
        return
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        m = re.fullmatch(re.escape(PROCESS_PREFIX) + r"(\d+)", name)
        if m and not platform.pid_alive(int(m.group(1))):
            QLocalServer.removeServer(os.path.join(folder, name))


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
    activate = data.get("activate")
    if activate is not None and not (isinstance(activate, str) and LOCK_NAME.fullmatch(activate)):
        raise ValueError("blocco non valido")
    return {"files": files, "new_window": bool(data.get("new_window")), "command": command, "activate": activate}


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

    def connected() -> None:
        if not platform.pipe_server_is_current_user(int(sock.socketDescriptor())):
            done()                                  # pipe di un altro utente: niente percorsi
            return
        sock.write(json.dumps(request).encode("utf-8") + b"\n")

    sock.connected.connect(connected)
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


def show_in_process(pid: int, name: str, timeout_ms: int = SHOW_TIMEOUT_MS) -> bool:
    """Chiede al processo Foliant `pid` di mostrare la scheda del file con il blocco `name` (solo il
    nome del blocco, mai il percorso). Su Windows gli si cede prima il diritto di andare in primo
    piano. False se non risponde entro `timeout_ms` (occupato: la richiesta arriva quando è libero)."""
    channel = process_channel(pid)
    if not channel:
        return False
    platform.allow_foreground(pid)
    return send_request(channel, {"files": [], "new_window": False, "command": None, "activate": name},
                        timeout_ms)


def handle_request(request: dict, settings):
    """Richiesta ricevuta su un canale: mostrare un file già aperto qui, oppure aprire file e comandi."""
    if request.get("activate"):
        from . import open_files
        open_files.show_name(request["activate"])
        return None
    return open_request(request, settings)


def open_request(request: dict, settings, window=None):
    """Esegue una richiesta: i file vanno nella finestra attiva (una nuova con «new_window» o se
    non ce n'è nessuna); un file già aperto ne attiva la scheda, anche in un'altra finestra, e va
    in primo piano quella. Restituisce la finestra portata in primo piano (quella attiva se tutti
    i file sono aperti in un altro processo Foliant: lì vanno in primo piano, non qui)."""
    from . import open_files
    from .main_window import active_window, open_window
    if window is None and not request.get("new_window"):
        window = active_window()
    if window is None:
        window = open_window(settings)
    files = request.get("files") or []
    elsewhere = [p for p, _n in files if (b := open_files.busy(p)) is not None and b.holder is not None]
    views = window.open_files(files)
    command = request.get("command")
    if command:
        QTimer.singleShot(0, window, lambda: window.run_command(command))   # dopo la risposta e la finestra
    target = window
    if views and not command:
        owner = views[-1].window()          # l'ultimo file può essere già aperto in un'altra finestra
        target = owner if owner is not None and owner is not views[-1] else window
    elif files and len(elsewhere) == len(files) and not command and not request.get("new_window"):
        return window                       # li mostra l'altro processo: questa resta dietro
    open_files.raise_window(target)
    return target
