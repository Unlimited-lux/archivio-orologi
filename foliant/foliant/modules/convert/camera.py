"""Acquisizione dalla webcam (CNV-26) tramite OpenCV (V4L2 su Linux), senza QtMultimedia.

Il dispositivo è astratto da un «backend» con la stessa interfaccia del backend simulato dei test:

    list_devices() -> list[tuple[int, str]]      # (indice, descrizione)
    open(index) -> None                          # solleva CameraError se non si apre
    read() -> numpy RGB (h, w, 3) | None
    close() -> None
"""

from __future__ import annotations

import glob
import os

from foliant.api import FoliantError, _


class CameraError(FoliantError):
    """Fotocamera assente, occupata o accesso negato."""


class CvCamera:
    """Webcam tramite `cv2.VideoCapture`."""

    def __init__(self):
        self._cap = None

    def list_devices(self) -> list[tuple[int, str]]:
        out = []
        for path in sorted(glob.glob("/dev/video*"), key=lambda p: int("".join(c for c in p if c.isdigit()) or 0)):
            digits = "".join(c for c in os.path.basename(path) if c.isdigit())
            if not digits:
                continue
            index = int(digits)
            name = ""
            try:
                with open(f"/sys/class/video4linux/video{index}/name", encoding="utf-8") as f:
                    name = f.read().strip()
            except OSError:
                pass
            # molte webcam espongono un secondo nodo solo per i metadati: si tiene il primo per nome
            if name and any(label == name for _i, label in out):
                continue
            out.append((index, name or _("Fotocamera {n}").format(n=index)))
        return out

    def open(self, index: int) -> None:
        import cv2
        self.close()
        cap = cv2.VideoCapture(index, cv2.CAP_V4L2) if os.name == "posix" else cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            raise CameraError(_("Impossibile aprire la fotocamera: è usata da un altro programma oppure "
                                "l'accesso è negato (permessi del dispositivo)."))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        self._cap = cap

    def read(self):
        import cv2
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
