"""Modulo Proteggi: password e permessi, redazione, sanificazione, metadati, avvisi."""

from foliant.api import Context, _, add_translations

ORDER = 80


def register(ctx: Context) -> None:
    add_translations("en", {"Proteggi": "Protect"})
    ctx.add_mode("proteggi", _("Proteggi"), "shield", 80)
