"""Errori con messaggi pronti da mostrare all'utente."""


class FoliantError(Exception):
    """Errore previsto: il messaggio è già comprensibile per l'utente."""


class DocumentError(FoliantError):
    pass


class PasswordRequired(DocumentError):
    def __init__(self, message: str = "Il documento è protetto da password."):
        super().__init__(message)


class WrongPassword(DocumentError):
    def __init__(self, message: str = "Password errata."):
        super().__init__(message)


class OperationCancelled(FoliantError):
    def __init__(self, message: str = "Operazione annullata."):
        super().__init__(message)
