"""API pubblica per i moduli: importare da qui, non dagli internals.

    from foliant.api import Context, Document, Tool, PageEvent, ENGINE_LOCK, run_task, icon, _
"""

from .core import placement
from .core.render import annotations_hidden, page_pixmap
from .core.document import (
    ACCESS, CHANGE_KINDS, CLOSED, CONTENT, ENCRYPT_METADATA_SUPPORTED, LOCK, SAVED, Document, atomic_write,
)
from .core.incremental import IncrementalSaveError
from .core.engine import ENGINE_LOCK, try_engine
from .core.errors import DocumentError, FoliantError, OperationCancelled, PasswordRequired, WrongPassword
from .i18n import _, add_translations, format_date, format_number, language
from .ui import theme
from .ui.banners import Banner
from .ui.context import GUARD_EVENTS, PDF_FILTER, Context, Mode
from .ui.document_view import (
    FIT_PAGE, FIT_WIDTH, GUIDE_HORIZONTAL, GUIDE_VERTICAL, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE,
    DocumentView, page_geometry,
)
from .ui.rulers import RULER_UNITS
from .ui.icons import bind_icon, icon
from .ui.tasks import BackgroundTask, Progress, run_task
from .ui.tools import (
    HandTool, PageEvent, SelectTextTool, TextSelection, Tool, copy_text_to_clipboard, follow_link,
    selection_from_words, words_to_text,
)

__all__ = [
    "page_pixmap", "annotations_hidden", "GUIDE_HORIZONTAL", "GUIDE_VERTICAL", "RULER_UNITS",
    "placement", "CLOSED", "CONTENT", "SAVED", "ACCESS", "LOCK", "CHANGE_KINDS", "ENCRYPT_METADATA_SUPPORTED",
    "Document", "atomic_write", "IncrementalSaveError", "ENGINE_LOCK", "try_engine",
    "DocumentError", "FoliantError", "OperationCancelled", "PasswordRequired", "WrongPassword",
    "_", "add_translations", "format_date", "format_number", "language", "theme", "PDF_FILTER", "Context", "Mode", "GUARD_EVENTS", "Banner",
    "FIT_PAGE", "FIT_WIDTH", "VIEW_CONTINUOUS", "VIEW_COVER", "VIEW_FACING", "VIEW_SINGLE", "DocumentView",
    "page_geometry", "bind_icon", "icon", "BackgroundTask", "Progress", "run_task", "HandTool", "PageEvent",
    "SelectTextTool", "TextSelection", "Tool", "copy_text_to_clipboard", "follow_link", "selection_from_words",
    "words_to_text",
]
