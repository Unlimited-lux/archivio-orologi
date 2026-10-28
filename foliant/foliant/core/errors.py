"""Errori con messaggi pronti da mostrare all'utente."""


class FoliantError(Exception):
    """Errore previsto: il messaggio è già comprensibile per l'utente."""


class DocumentError(FoliantError):
    pass


class DocumentWriteError(DocumentError):
    """Il file non si può scrivere (disco pieno, cartella non scrivibile, file in uso…).

    Il messaggio nomina il file, la causa e l'azione possibile; `errno` e `filename`
    permettono all'interfaccia di proporre «Salva con nome» (NFR-08, NFR-15)."""

    def __init__(self, message: str, errno: int | None = None, filename: str | None = None):
        super().__init__(message)
        self.errno = errno
        self.filename = filename


class PasswordRequired(DocumentError):
    def __init__(self, message: str = "Il documento è protetto da password."):
        super().__init__(message)


class WrongPassword(DocumentError):
    def __init__(self, message: str = "Password errata."):
        super().__init__(message)


class OperationCancelled(FoliantError):
    def __init__(self, message: str = "Operazione annullata."):
        super().__init__(message)
