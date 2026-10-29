"""Punto d'ingresso di Foliant nel pacchetto per Windows (PyInstaller).

Lo stesso file produce due eseguibili nella stessa cartella:

- Foliant.exe      applicazione grafica, senza finestra di console;
- foliant-cli.exe  stessa applicazione con la console: `foliant-cli --help`, l'elaborazione in
                   blocco (`foliant-cli blocco …`, come `python -m foliant.modules.tools.cli …`),
                   `foliant-cli rimuovi-dati-utente` (preferenze e dati dell'utente, usato dalla
                   disinstallazione) e i processi ausiliari di Foliant.

Nel pacchetto non c'è un interprete Python separato: i processi ausiliari che sotto Linux
partono con `python -m modulo` o `python -I -c codice` (vedi foliant.core.platform.python_command)
partono con foliant-cli.exe, che qui accetta le stesse opzioni.
"""

import sys


def _python_options(args: list[str]) -> bool:
    """Esegue `-m modulo …` o `-c codice …` come l'interprete; False se non è il caso."""
    while args and args[0] in ("-I", "-E", "-s", "-u", "-B"):     # nel pacchetto non hanno effetto
        args = args[1:]
    if len(args) >= 2 and args[0] == "-c":
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__", "__builtins__": __builtins__})
        return True
    if len(args) >= 2 and args[0] == "-m":
        import runpy
        sys.argv = [args[1], *args[2:]]
        runpy.run_module(args[1], run_name="__main__", alter_sys=True)
        return True
    return False


def run() -> int:
    import multiprocessing
    multiprocessing.freeze_support()            # processi figli «spawn» della conversione in blocco
    args = sys.argv[1:]
    if args and args[0] == "rimuovi-dati-utente":      # chiamato dal programma di disinstallazione
        from foliant.core import platform
        for item in platform.remove_user_data():
            print(item)
        return 0
    if args and args[0] == "blocco":
        sys.argv = ["foliant-cli blocco", *args[1:]]
        from foliant.modules.tools.cli import main as batch_main
        return int(batch_main(args[1:]) or 0)
    if _python_options(args):
        return 0
    from foliant.app import main
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
