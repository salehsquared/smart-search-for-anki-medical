"""Reusable Qt widgets for the smart-search dialog.

Qt imports go through a small shim: inside Anki we use ``aqt.qt``; for
standalone development and testing we fall back to a plain PyQt6 install.
Only palette-derived colors are used, and styling is recomputed on palette
changes so dark mode and custom themes keep working.
"""

from __future__ import annotations

from html import escape
import sys
from typing import Optional, Sequence

try:  # Anki runtime (Anki re-exports Qt, signals, and widget classes)
    from aqt.qt import (  # type: ignore[import-not-found]  # noqa: F401
        QAbstractListModel,
        QAction,
        QApplication,
        QButtonGroup,
        QCheckBox,
        QColor,
        QComboBox,
        QDesktopServices,
        QDialog,
        QDialogButtonBox,
        QEvent,
        QFont,
        QFontMetrics,
        QFormLayout,
        QFrame,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListView,
        QMenu,
        QMessageBox,
        QModelIndex,
        QObject,
        QPainter,
        QPalette,
        QPixmap,
        QProgressBar,
        QPushButton,
        QRect,
        QShortcut,
        QSize,
        QSpinBox,
        QStackedWidget,
        QStyle,
        QStyleOptionButton,
        QStyledItemDelegate,
        QStyleOptionViewItem,
        QTabWidget,
        QTextDocument,
        QTimer,
        QToolButton,
        Qt,
        QUrl,
        QVBoxLayout,
        QWidget,
        pyqtSignal,
        pyqtSlot,
    )
    from aqt.qt import QKeySequence  # type: ignore[import-not-found]  # noqa: F401
except ImportError:  # standalone development / testing
    from PyQt6.QtCore import (  # noqa: F401
        QAbstractListModel,
        QEvent,
        QModelIndex,
        QObject,
        QRect,
        QSize,
        Qt,
        QTimer,
        QUrl,
        pyqtSignal,
        pyqtSlot,
    )
    from PyQt6.QtGui import (  # noqa: F401
        QColor,
        QDesktopServices,
        QFont,
        QFontMetrics,
        QAction,
        QKeySequence,
        QPainter,
        QPalette,
        QPixmap,
        QShortcut,
        QTextDocument,
    )
    from PyQt6.QtWidgets import (  # noqa: F401
        QApplication,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QFrame,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListView,
        QMenu,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QSpinBox,
        QStackedWidget,
        QStyle,
        QStyleOptionButton,
        QStyledItemDelegate,
        QStyleOptionViewItem,
        QTabWidget,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

from .contracts import (
    AboutInfo,
    Correction,
    FilterChip,
    IndexState,
    IndexStatus,
    SearchMode,
)

_PRIMARY_KEY = "⌘" if sys.platform == "darwin" else "Ctrl"


# ---------------------------------------------------------------------------
# Palette helpers
# ---------------------------------------------------------------------------

def _hex(color) -> str:
    return color.name()


def blend_colors(base, overlay, alpha: float):
    """Return ``overlay`` at ``alpha`` over ``base`` (both QColor)."""
    out = QColor(base)
    out.setRed(round(base.red() * (1 - alpha) + overlay.red() * alpha))
    out.setGreen(round(base.green() * (1 - alpha) + overlay.green() * alpha))
    out.setBlue(round(base.blue() * (1 - alpha) + overlay.blue() * alpha))
    return out


class PaletteMixin:
    """Computes palette-aware colors; call :meth:`refresh_palette` on change."""

    def refresh_palette(self) -> None:  # pragma: no cover - trivial wiring
        pass

    def _palette_colors(self: QWidget) -> dict[str, str]:
        pal = self.palette()
        base = pal.color(QPalette.ColorRole.Base)
        text = pal.color(QPalette.ColorRole.Text)
        accent = pal.color(QPalette.ColorRole.Highlight)
        window = pal.color(QPalette.ColorRole.Window)
        is_dark = base.lightness() < 128
        return {
            "base": _hex(base),
            "text": _hex(text),
            "accent": _hex(accent),
            "accent_soft": _hex(blend_colors(base, accent, 0.18)),
            "accent_mid": _hex(blend_colors(base, accent, 0.35)),
            "chip_bg": _hex(blend_colors(window, text, 0.07)),
            "chip_border": _hex(blend_colors(window, text, 0.22)),
            "window": _hex(window),
            # Semantic status colors chosen per-theme for contrast; the
            # accompanying text label always carries the meaning too.
            "ok": "#3d9a50" if is_dark else "#1d7a34",
            "warn": "#d9a13b" if is_dark else "#9a6a00",
            "err": "#e06c60" if is_dark else "#b3261e",
        }


class SearchField(QLineEdit):
    """The dominant query input with a native clear affordance."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Search notes, tags, decks…")
        self.setClearButtonEnabled(True)
        self.setMinimumHeight(38)
        font = self.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        self.setFont(font)
        self.setAccessibleName("Search query")
        self.setAccessibleDescription(
            "Type to search notes. Press Down to move to results, "
            f"{_PRIMARY_KEY}+1, 2 or 3 switches search mode."
        )


class SegmentedModeControl(QWidget, PaletteMixin):
    """Compact Smart / Exact / Semantic segmented control."""

    modeChanged = pyqtSignal(object)  # SearchMode

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._buttons: dict[SearchMode, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hints = {
            SearchMode.SMART: f"Smart search: typo tolerance and aliases ({_PRIMARY_KEY}+1)",
            SearchMode.EXACT: f"Exact search: literal text match ({_PRIMARY_KEY}+2)",
            SearchMode.SEMANTIC: f"Semantic search: meaning-based match ({_PRIMARY_KEY}+3)",
        }
        for mode in (SearchMode.SMART, SearchMode.EXACT, SearchMode.SEMANTIC):
            btn = QToolButton(self)
            btn.setText(mode.label)
            btn.setCheckable(True)
            btn.setToolTip(hints[mode])
            btn.setAccessibleName(f"{mode.label} search mode")
            btn.setAccessibleDescription(hints[mode])
            btn.clicked.connect(lambda _checked=False, m=mode: self.modeChanged.emit(m))
            self._group.addButton(btn)
            self._buttons[mode] = btn
            layout.addWidget(btn)

        self.setAccessibleName("Search mode")
        self.setAccessibleDescription("Choose Smart, Exact, or Semantic matching.")
        self._buttons[SearchMode.SMART].setChecked(True)
        self.refresh_palette()

    def mode(self) -> SearchMode:
        for mode, btn in self._buttons.items():
            if btn.isChecked():
                return mode
        return SearchMode.SMART

    def setMode(self, mode: SearchMode) -> None:
        btn = self._buttons[mode]
        if not btn.isChecked():
            btn.setChecked(True)
            self.modeChanged.emit(mode)

    def refresh_palette(self) -> None:
        c = self._palette_colors()
        self.setStyleSheet(
            "QToolButton {"
            f"  border: 1px solid {c['chip_border']};"
            f"  background: {c['window']}; color: {c['text']};"
            "  padding: 6px 14px; font-weight: 500;"
            "}"
            "QToolButton:first { border-top-left-radius: 8px; border-bottom-left-radius: 8px; }"
            "QToolButton:last { border-top-right-radius: 8px; border-bottom-right-radius: 8px; }"
            "QToolButton:middle { border-left: none; border-right: none; }"
            f"QToolButton:checked {{ background: {c['accent_soft']}; color: {c['text']};"
            f"  border-color: {c['accent_mid']}; font-weight: 600; }}"
            f"QToolButton:focus {{ border-color: {c['accent']}; }}"
            f"QToolButton:hover:!checked {{ background: {c['chip_bg']}; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type() == QEvent.Type.PaletteChange
            and not getattr(self, "_refreshing_palette", False)
        ):
            self._refreshing_palette = True
            try:
                self.refresh_palette()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)


class Chip(QFrame, PaletteMixin):
    """A single removable chip. Corrections also offer a "literal" action."""

    removeClicked = pyqtSignal()
    literalClicked = pyqtSignal()

    def __init__(
        self,
        text: str,
        explanation: str = "",
        *,
        accent: bool = False,
        literal: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._accent = accent
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(4)

        label = QLabel(text, self)
        label.setAccessibleName(text)
        layout.addWidget(label)

        if literal:
            literal_btn = QToolButton(self)
            literal_btn.setText("Literal")
            literal_btn.setToolTip("Search for the exact text you typed")
            literal_btn.setAccessibleName(f"Search literally for {text}")
            literal_btn.clicked.connect(self.literalClicked)
            layout.addWidget(literal_btn)
            self._literal_btn: Optional[QToolButton] = literal_btn
        else:
            self._literal_btn = None

        remove_btn = QToolButton(self)
        remove_btn.setText("×")
        remove_btn.setToolTip("Remove")
        remove_btn.setAccessibleName(f"Remove {text}")
        remove_btn.clicked.connect(self.removeClicked)
        layout.addWidget(remove_btn)
        self._remove_btn = remove_btn

        if explanation:
            self.setToolTip(explanation)
            self.setAccessibleDescription(explanation)
        self.setAccessibleName(f"Chip: {text}")
        self.refresh_palette()

    def refresh_palette(self) -> None:
        c = self._palette_colors()
        border = c["accent_mid"] if self._accent else c["chip_border"]
        bg = c["accent_soft"] if self._accent else c["chip_bg"]
        self.setStyleSheet(
            f"Chip {{ background: {bg}; border: 1px solid {border}; border-radius: 9px; }}"
            "QLabel { background: transparent; border: none; }"
            "QToolButton { background: transparent; border: none; padding: 0px 4px;"
            f"  color: {c['text']}; font-weight: 600; }}"
            f"QToolButton:hover {{ color: {c['accent']}; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type() == QEvent.Type.PaletteChange
            and not getattr(self, "_refreshing_palette", False)
        ):
            self._refreshing_palette = True
            try:
                self.refresh_palette()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)


class ChipBar(QWidget):
    """Always-visible row of active filter chips and correction chips."""

    filterRemoveRequested = pyqtSignal(object)      # FilterChip
    correctionDismissRequested = pyqtSignal(object)  # Correction
    correctionLiteralRequested = pyqtSignal(object)  # Correction

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self.setAccessibleName("Active filters and corrections")
        self.setAccessibleDescription(
            "Removable chips for structured filters and spelling or alias expansions."
        )
        self.setVisible(False)

    def _clear(self) -> None:
        while self._layout.count() > 1:  # keep the trailing stretch
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_chips(
        self,
        filters: Sequence[FilterChip],
        corrections: Sequence[Correction],
    ) -> None:
        self._clear()
        for chip_data in filters:
            chip = Chip(chip_data.display, explanation=f"Filter: {chip_data.token}")
            chip.removeClicked.connect(
                lambda c=chip_data: self.filterRemoveRequested.emit(c)
            )
            self._layout.insertWidget(self._layout.count() - 1, chip)
        for correction in corrections:
            explanation = (
                correction.explanation
                or f"{correction.kind.title()} expansion applied to your query."
            )
            chip = Chip(correction.display, explanation=explanation, accent=True, literal=True)
            chip.removeClicked.connect(
                lambda c=correction: self.correctionDismissRequested.emit(c)
            )
            chip.literalClicked.connect(
                lambda c=correction: self.correctionLiteralRequested.emit(c)
            )
            self._layout.insertWidget(self._layout.count() - 1, chip)
        self.setVisible(bool(filters or corrections))


class IndexStatusWidget(QFrame, PaletteMixin):
    """Compact search status: colored dot plus always-present text."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)
        self._dot = QFrame(self)
        self._dot.setFixedSize(10, 10)
        self._label = QLabel(self)
        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        self.setAccessibleName("Search status")
        self._status = IndexStatus(IndexState.UNAVAILABLE, detail="Not queried yet")
        self._apply()

    def set_status(self, status: IndexStatus) -> None:
        self._status = status
        self._apply()

    def status(self) -> IndexStatus:
        return self._status

    def _apply(self) -> None:
        c = self._palette_colors()
        color = {
            IndexState.READY: c["ok"],
            IndexState.BUILDING: c["warn"],
            IndexState.UNAVAILABLE: c["err"],
            IndexState.ERROR: c["err"],
        }[self._status.state]
        text = self._status.summary
        if self._status.detail:
            text = f"{text} — {self._status.detail}"
        self._dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        self._label.setText(text)
        self.setToolTip(text)
        self.setAccessibleDescription(text)
        self.setStyleSheet(
            f"IndexStatusWidget {{ background: {c['chip_bg']};"
            f" border: 1px solid {c['chip_border']}; border-radius: 9px; }}"
            "QLabel { background: transparent; border: none; }"
            "QFrame { border: none; }"
        )

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type() == QEvent.Type.PaletteChange
            and not getattr(self, "_refreshing_palette", False)
        ):
            self._refreshing_palette = True
            try:
                self._apply()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)


_ABOUT_TAGLINE = (
    "Search your collection with exact terms, typo-tolerant matching, "
    "or meaning."
)
_ABOUT_PRIVACY = (
    "Searches, card contents, and indexes remain on this computer. "
    "The add-on connects to the internet only when you explicitly set up "
    "or repair optional Semantic Search."
)
_ABOUT_INDEPENDENCE = (
    "Independent add-on; not affiliated with or endorsed by Anki."
)
_ABOUT_LOGO_SIZE = 72


class AboutPanel(QWidget):
    """The quiet About tab: logo, identity, attribution, privacy, links.

    All content comes from :class:`AboutInfo` plus fixed truthful copy, so
    the panel stays a dumb renderer.  Links open only on explicit activation
    and carry no card or search data.
    """

    def __init__(
        self,
        about: AboutInfo,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._about = about
        self.setAccessibleName(f"About {about.product_name}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 12)
        outer.setSpacing(5)
        outer.addStretch(1)

        self.logo_label = QLabel(self)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setAccessibleName("MedBrevia logo")
        self.logo_label.setAccessibleDescription(
            "MedBrevia logo: a white circle with the MedBrevia mark."
        )
        if about.logo_path:
            pixmap = QPixmap(about.logo_path)
            if not pixmap.isNull():
                self.logo_label.setPixmap(
                    pixmap.scaled(
                        _ABOUT_LOGO_SIZE,
                        _ABOUT_LOGO_SIZE,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        self.logo_label.setVisible(not self.logo_label.pixmap().isNull())
        outer.addWidget(self.logo_label)

        self.name_label = QLabel(about.product_name, self)
        name_font = self.name_label.font()
        name_font.setBold(True)
        name_font.setPointSizeF(name_font.pointSizeF() * 1.15)
        self.name_label.setFont(name_font)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setAccessibleName(about.product_name)
        outer.addWidget(self.name_label)

        self.version_label = QLabel(f"Version {about.version}", self)
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version_label.setAccessibleName(f"Version {about.version}")
        self.version_label.setVisible(bool(about.version))
        outer.addWidget(self.version_label)

        outer.addSpacing(4)

        self.tagline_label = QLabel(_ABOUT_TAGLINE, self)
        self.tagline_label.setWordWrap(True)
        self.tagline_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tagline_label.setAccessibleName("What Smart Search does")
        self.tagline_label.setAccessibleDescription(_ABOUT_TAGLINE)
        outer.addWidget(self.tagline_label)

        outer.addSpacing(4)

        self.attribution_label = QLabel(
            f"Built by MedBrevia<br>Created by {escape(about.creator)}",
            self,
        )
        self.attribution_label.setTextFormat(Qt.TextFormat.RichText)
        self.attribution_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.attribution_label.setAccessibleName("Attribution")
        self.attribution_label.setAccessibleDescription(
            f"Built by MedBrevia. Created by {about.creator}."
        )
        outer.addWidget(self.attribution_label)

        outer.addSpacing(4)

        self.privacy_label = QLabel(_ABOUT_PRIVACY, self)
        self.privacy_label.setWordWrap(True)
        self.privacy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.privacy_label.setAccessibleName("Privacy statement")
        self.privacy_label.setAccessibleDescription(_ABOUT_PRIVACY)
        outer.addWidget(self.privacy_label)

        self.independence_label = QLabel(_ABOUT_INDEPENDENCE, self)
        self.independence_label.setWordWrap(True)
        self.independence_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.independence_label.setAccessibleName("Anki affiliation notice")
        self.independence_label.setAccessibleDescription(_ABOUT_INDEPENDENCE)
        outer.addWidget(self.independence_label)

        outer.addSpacing(4)

        self.links_label = QLabel(self)
        self.links_label.setTextFormat(Qt.TextFormat.RichText)
        self.links_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.links_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.links_label.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        links: list[str] = []
        for url, text in (
            (about.website_url, "Mobile App"),
            (about.feedback_url, "Feedback"),
            (about.privacy_url, "Privacy"),
        ):
            if url:
                links.append(f'<a href="{escape(url, quote=True)}">{text}</a>')
        self.links_label.setText(" &nbsp;·&nbsp; ".join(links))
        self.links_label.setVisible(bool(links))
        self.links_label.setAccessibleName("About links")
        self.links_label.setAccessibleDescription(
            "Mobile app, feedback, and privacy links. "
            "Each opens only when you activate it."
        )
        # openExternalLinks stays False: navigation requires an explicit
        # activation, routed through the desktop services handler.
        self._url_opener = QDesktopServices.openUrl
        self.links_label.linkActivated.connect(self._open_link)
        outer.addWidget(self.links_label)

        outer.addStretch(1)

    def _open_link(self, url: str) -> None:
        self._url_opener(QUrl(str(url)))
