"""Elaborazione in blocco da riga di comando (BAT-22), senza interfaccia grafica.

Uso::

    python -m foliant.modules.tools.cli --operazione compress --destinazione uscita/ cartella/ *.pdf
    python -m foliant.modules.tools.cli --sequenza "Riservati" --password-file segreti.txt --rapporto r.json in/

Codici di uscita: 0 tutto completato; 1 completato con avvisi; 2 uno o più file in
errore; 3 parametri o configurazione non validi; 4 annullato.

Le password non si passano mai come argomenti: si leggono dall'input standard
(`--password-stdin`) o da un file (`--password-file`). Ogni riga è `chiave=valore`
per i parametri segreti dei passi (es. `user_password=…`) oppure una password
di apertura da provare sui file protetti.
"""

from __future__ import annotations

import argparse
import glob
import os
import signal
import sys
import threading

from foliant.api import OperationCancelled, _, atomic_write
from foliant.core import platform

from . import engine as be

EXIT_OK, EXIT_WARNINGS, EXIT_ERRORS, EXIT_INVALID, EXIT_CANCELLED = 0, 1, 2, 3, 4


def config_dir() -> str:
    """Cartella di configurazione condivisa con l'applicazione grafica."""
    if os.environ.get("FOLIANT_CONFIG_DIR"):
        return os.environ["FOLIANT_CONFIG_DIR"]
    return str(platform.settings_dir())


class CliError(Exception):
    pass


class _Progress:
    """Avanzamento su stderr con annullamento (come quello di run_task)."""

    def __init__(self, quiet: bool, stream) -> None:
        self._cancel = threading.Event()
        self.quiet = quiet
        self.stream = stream
        self.cancelled_by_user = False

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise OperationCancelled()

    def update(self, fraction=None, text=None) -> None:
        if not self.quiet and text:
            pct = f"{int((fraction or 0) * 100):3d}%" if fraction is not None else "    "
            print(f"[{pct}] {text}", file=self.stream, flush=True)
        self.check()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="foliant-batch",
        description=_("Elaborazione in blocco di PDF senza interfaccia grafica."),
        epilog=_("Codici di uscita: 0 completato, 1 avvisi, 2 errori, 3 parametri non validi, 4 annullato. "
                 "Le password si forniscono solo con --password-stdin o --password-file."))
    p.add_argument("ingressi", nargs="*", help=_("file, cartelle o schemi con caratteri jolly"))
    what = p.add_mutually_exclusive_group()
    what.add_argument("--operazione", "-o", action="append", default=[],
                      help=_("operazione da applicare (ripetibile: i passi vengono eseguiti in ordine)"))
    what.add_argument("--sequenza", "-s", help=_("nome di una sequenza salvata oppure percorso del file"))
    p.add_argument("--param", "-p", action="append", default=[], metavar="CHIAVE=VALORE",
                   help=_("parametro dell'ultima operazione indicata (per le sequenze: passo:chiave=valore)"))
    p.add_argument("--destinazione", "-d", help=_("cartella di destinazione"))
    p.add_argument("--stessa-cartella", action="store_true", help=_("scrive accanto agli originali"))
    p.add_argument("--sottocartella", help=_("sottocartella accanto a ciascun originale"))
    p.add_argument("--mantieni-struttura", action="store_true", help=_("replica le sottocartelle di ingresso"))
    p.add_argument("--nome", default=None, help=_("modello del nome (es. {nome}_{contatore:3})"))
    p.add_argument("--contatore", type=int, default=1, help=_("valore iniziale di {contatore}"))
    p.add_argument("--conflitti", choices=("rename", "overwrite", "skip"), default="rename")
    p.add_argument("--errori", choices=("continue", "stop"), default="continue")
    p.add_argument("--sottocartelle", action="store_true", help=_("include le sottocartelle"))
    p.add_argument("--sostituisci-originali", action="store_true")
    p.add_argument("--senza-bak", action="store_true", help=_("non crea la copia di sicurezza .bak"))
    p.add_argument("--rapporto", "-r", help=_("percorso del rapporto (.json o .csv)"))
    p.add_argument("--password-stdin", action="store_true", help=_("legge le password dall'input standard"))
    p.add_argument("--password-file", help=_("legge le password da un file"))
    p.add_argument("--elenco-operazioni", action="store_true", help=_("elenca le operazioni disponibili"))
    p.add_argument("--silenzioso", "-q", action="store_true")
    return p


def _parse_value(spec: be.Param | None, raw: str):
    if spec is None:
        return raw
    if spec.type == "bool":
        return raw.strip().lower() in ("1", "si", "sì", "true", "vero", "yes", "on")
    if spec.type == "int":
        return int(raw)
    if spec.type == "float":
        return float(raw.replace(",", "."))
    return raw


def _secret_keys() -> set[str]:
    return {p.key for spec in be.ACTIONS.values() for p in spec.params if p.secret}


def _read_secrets(args, stdin) -> tuple[dict[str, str], list[str]]:
    lines: list[str] = []
    if args.password_file:
        try:
            with open(args.password_file, encoding="utf-8") as f:
                lines += f.read().splitlines()
        except OSError as e:
            raise CliError(_("File delle password non leggibile: {e}").format(e=e.strerror or e))
    if args.password_stdin:
        lines += stdin.read().splitlines()
    named: dict[str, str] = {}
    plain: list[str] = []
    secret = _secret_keys()
    for line in lines:
        if not line:
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip() in secret:
            k = key.strip()
            named[k] = (named[k] + "\n" + value) if k == "passwords" and k in named else value
        else:
            plain.append(line)
    return named, plain


def _steps(args) -> list[be.Step]:
    if args.sequenza:
        path = args.sequenza
        if not os.path.exists(path):
            matches = [p for name, p in be.list_sequences(os.path.join(config_dir(), "foliant-sequenze"))
                       if name == args.sequenza]
            if not matches:
                raise CliError(_("Sequenza non trovata: {s}").format(s=args.sequenza))
            path = matches[0]
        try:
            _name, steps = be.load_sequence(path)
        except Exception as e:
            raise CliError(str(e))
    else:
        if not args.operazione:
            raise CliError(_("Indica un'operazione (--operazione) o una sequenza (--sequenza)."))
        steps = []
        for op in args.operazione:
            if op not in be.ACTIONS:
                raise CliError(_("Operazione sconosciuta: {a}").format(a=op))
            steps.append(be.Step(op, {}))
    secret = _secret_keys()
    for item in args.param:
        target = steps[-1] if steps else None
        key, sep, value = item.partition("=")
        if not sep:
            raise CliError(_("Parametro non valido: {p} (atteso CHIAVE=VALORE)").format(p=item))
        if ":" in key:
            idx, key = key.split(":", 1)
            try:
                target = steps[int(idx) - 1]
            except (ValueError, IndexError):
                raise CliError(_("Passo inesistente nel parametro {p}").format(p=item))
        key = key.strip()
        if key in secret:
            raise CliError(_("Le password non si possono passare come argomenti: usa --password-stdin oppure "
                             "--password-file."))
        spec = next((p for p in be.ACTIONS[target.action].params if p.key == key), None)
        if spec is None:
            raise CliError(_("Parametro sconosciuto per «{a}»: {k}").format(a=target.action, k=key))
        try:
            target.params[key] = _parse_value(spec, value)
        except ValueError:
            raise CliError(_("Valore non valido per {k}: {v}").format(k=key, v=value))
    return steps


def _inputs(args) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    paths: list[str] = []
    roots: list[str] = []
    for raw in args.ingressi:
        matches = glob.glob(raw) if any(c in raw for c in "*?[") else [raw]
        if not matches:
            raise CliError(_("Nessun file corrisponde a {p}").format(p=raw))
        for m in matches:
            if os.path.isdir(m):
                roots.append(os.path.realpath(m))
            paths.append(m)
    return paths, roots, []


def main(argv: list[str] | None = None, stdin=None, stdout=None, stderr=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:   # --help o parametri non riconosciuti
        return EXIT_OK if e.code == 0 else EXIT_INVALID
    if args.elenco_operazioni:
        for spec in be.available_actions():
            ok, msg = be.action_available(spec.id)
            print(f"{spec.id:18s} {_(spec.label)}" + ("" if ok else f"  ({msg})"), file=stdout)
            for p in spec.params:
                tag = " [segreto]" if p.secret else ""
                print(f"    {p.key}={p.default!r}  {_(p.label)}{tag}", file=stdout)
        return EXIT_OK
    try:
        steps = _steps(args)
        named, plain = _read_secrets(args, stdin)
        for step in steps:
            for p in be.ACTIONS[step.action].params:
                if p.secret and p.key in named:
                    step.params[p.key] = named[p.key]
        paths, roots, _x = _inputs(args)
        exts = be.INPUT_EXTENSIONS + (be.CREATE_EXTENSIONS if any(s.action == "create_pdf" for s in steps) else ())
        found, ignored = be.collect_inputs(paths, args.sottocartelle, exts)
        for p, why in ignored:
            print(_("Ignorato: {p} ({w})").format(p=p, w=why), file=stderr)
        items = [be.analyze_file(p) for p in found]
        mode = "same" if args.stessa_cartella else ("subfolder" if args.sottocartella else "folder")
        settings = be.JobSettings(
            steps=steps, destination_mode=mode, destination=os.path.abspath(args.destinazione or ""),
            subfolder=args.sottocartella or "elaborati", keep_structure=args.mantieni_struttura,
            input_root=(os.path.commonpath(roots) if roots else None), counter_start=args.contatore,
            conflict=args.conflitti, replace_originals=args.sostituisci_originali, backup=not args.senza_bak,
            error_policy=args.errori, passwords=plain)
        settings.pattern = args.nome if args.nome is not None else be.default_pattern(settings)
        if mode == "folder" and not args.destinazione and not args.sostituisci_originali:
            raise CliError(_("Indica la destinazione (--destinazione, --stessa-cartella o --sottocartella)."))
        errors, warnings = be.validate_settings(items, settings)
        locked = [it for it in items if it.needs_password and not it.password]
        if locked and not settings.passwords and not any(s.action == "remove_password" for s in steps):
            errors.append(_("{n} file richiedono una password: forniscila con --password-stdin o "
                            "--password-file.").format(n=len(locked)))
        if errors:
            raise CliError("\n".join(errors))
        for w in warnings:
            print(_("Avviso: {w}").format(w=w), file=stderr)
        if mode == "folder" and not args.sostituisci_originali:
            os.makedirs(settings.destination, exist_ok=True)
    except CliError as e:
        print(_("Errore: {e}").format(e=e), file=stderr)
        return EXIT_INVALID

    progress = _Progress(args.silenzioso, stderr)
    box: dict = {}

    def work() -> None:
        try:
            box["report"] = be.run_job(progress, items, settings)
        except BaseException as e:   # riportato al thread principale
            box["error"] = e

    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, lambda *_a: progress.cancel())
    thread = threading.Thread(target=work, name="foliant-cli", daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(0.2)
    except KeyboardInterrupt:
        progress.cancel()
        thread.join()
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if "error" in box:
        print(_("Errore imprevisto: {e}").format(e=box["error"]), file=stderr)
        return EXIT_ERRORS
    report: be.JobReport = box["report"]
    if args.rapporto:
        data = report.to_json() if args.rapporto.lower().endswith(".json") else report.to_csv()
        try:
            atomic_write(args.rapporto, data.encode("utf-8"))
        except Exception as e:
            print(_("Rapporto non scritto: {e}").format(e=e), file=stderr)
    if not args.silenzioso:
        print(report.summary(), file=stderr)
    counts = report.counts()
    if report.cancelled or progress.cancelled:
        return EXIT_CANCELLED
    if counts.get("error"):
        return EXIT_ERRORS
    if counts.get("warning") or counts.get("skipped"):
        return EXIT_WARNINGS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
