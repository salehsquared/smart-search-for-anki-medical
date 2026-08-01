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
        QAbstractItemView,
        QAbstractListModel,
        QAction,
        QApplication,
        QButtonGroup,
        QBrush,
        QCheckBox,
        QColor,
        QComboBox,
        QDesktopServices,
        QDialog,
        QDialogButtonBox,
        QEvent,
        QFont,
        QFontMetrics,
        QIcon,
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
        QPen,
        QPixmap,
        QProgressBar,
        QPushButton,
        QRect,
        QShortcut,
        QSize,
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QStackedWidget,
        QStyle,
        QStyleOptionButton,
        QStyledItemDelegate,
        QStyleOptionViewItem,
        QTabWidget,
        QTextDocument,
        QTimer,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
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
        QBrush,
        QDesktopServices,
        QFont,
        QFontMetrics,
        QIcon,
        QAction,
        QKeySequence,
        QPainter,
        QPalette,
        QPen,
        QPixmap,
        QShortcut,
        QTextDocument,
    )
    from PyQt6.QtWidgets import (  # noqa: F401
        QAbstractItemView,
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
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QStackedWidget,
        QStyle,
        QStyleOptionButton,
        QStyledItemDelegate,
        QStyleOptionViewItem,
        QTabWidget,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
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
        app_pal = QApplication.palette()

        def concrete(role):
            color = pal.color(role)
            return app_pal.color(role) if color.alpha() == 0 else color

        base = concrete(QPalette.ColorRole.Base)
        text = concrete(QPalette.ColorRole.Text)
        accent = concrete(QPalette.ColorRole.Highlight)
        window = concrete(QPalette.ColorRole.Window)
        button = concrete(QPalette.ColorRole.Button)
        is_dark = base.lightness() < 128
        return {
            "base": _hex(base),
            "text": _hex(text),
            "accent": _hex(accent),
            "accent_text": _hex(
                concrete(QPalette.ColorRole.HighlightedText)
            ),
            "accent_soft": _hex(blend_colors(base, accent, 0.18)),
            "accent_mid": _hex(blend_colors(base, accent, 0.35)),
            "chip_bg": _hex(blend_colors(window, text, 0.07)),
            "chip_border": _hex(blend_colors(window, text, 0.22)),
            "surface": _hex(blend_colors(window, text, 0.055)),
            "surface_high": _hex(blend_colors(window, text, 0.085)),
            "button": _hex(button),
            "muted": _hex(concrete(QPalette.ColorRole.PlaceholderText)),
            "window": _hex(window),
            # Semantic status colors chosen per-theme for contrast; the
            # accompanying text label always carries the meaning too.
            "ok": "#3d9a50" if is_dark else "#1d7a34",
            "warn": "#d9a13b" if is_dark else "#9a6a00",
            "err": "#e06c60" if is_dark else "#b3261e",
        }


class SearchField(QLineEdit, PaletteMixin):
    """The dominant query input with a quiet leading search glyph."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._compound = False
        self.setPlaceholderText("Search notes, tags, decks…")
        self.setClearButtonEnabled(True)
        self.setMinimumHeight(48)
        font = self.font()
        font.setPointSizeF(font.pointSizeF() * 1.12)
        self.setFont(font)
        self._search_action = self.addAction(
            QIcon(),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.setAccessibleName("Search query")
        self.setAccessibleDescription(
            "Type to search notes. Press Return to search now, "
            "Down to move to results, "
            f"{_PRIMARY_KEY}+1, 2 or 3 switches search mode."
        )
        self.refresh_palette()

    def set_compound(self, compound: bool = True) -> None:
        """Render as the right segment of a compound deck/search field."""

        self._compound = bool(compound)
        self.refresh_palette()

    def _search_icon(self, color: str) -> QIcon:
        pixmap = QPixmap(22, 22)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(3, 3, 11, 11)
        painter.drawLine(12, 12, 18, 18)
        painter.end()
        return QIcon(pixmap)

    def refresh_palette(self) -> None:
        c = self._palette_colors()
        self._search_action.setIcon(self._search_icon(c["muted"]))
        corners = (
            "border-top-left-radius: 0; border-bottom-left-radius: 0;"
            " border-top-right-radius: 11px; border-bottom-right-radius: 11px;"
            if self._compound
            else "border-radius: 11px;"
        )
        self.setStyleSheet(
            "QLineEdit {"
            f" background: {c['surface']}; color: {c['text']};"
            f" border: 1px solid {c['chip_border']}; {corners}"
            " padding: 0 12px 0 4px;"
            " selection-background-color: "
            f"{c['accent']}; selection-color: {c['accent_text']};"
            "}"
            f"QLineEdit:hover {{ border-color: {c['accent_mid']}; }}"
            f"QLineEdit:focus {{ border: 1px solid {c['accent']};"
            f" background: {c['surface_high']}; }}"
            f"QLineEdit:disabled {{ color: {c['muted']}; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type()
            in (
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
            )
            and not getattr(self, "_refreshing_palette", False)
        ):
            self._refreshing_palette = True
            try:
                self.refresh_palette()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)


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
        layout.setSpacing(4)

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
            btn.setMinimumSize(92, 48)
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
            f"  background: {c['surface']}; color: {c['muted']};"
            "  border-radius: 10px; padding: 8px 16px; font-weight: 600;"
            "}"
            f"QToolButton:checked {{ background: {c['accent']};"
            f" color: {c['accent_text']}; border-color: {c['accent']}; }}"
            f"QToolButton:focus {{ border: 2px solid {c['accent']}; }}"
            f"QToolButton:hover:!checked {{ background: {c['surface_high']};"
            f" color: {c['text']}; }}"
            f"QToolButton:disabled {{ color: {c['muted']};"
            f" background: {c['window']}; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type()
            in (
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
            )
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
        self.setMinimumHeight(32)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 3, 7, 3)
        layout.setSpacing(5)

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
            f"Chip {{ background: {bg}; border: 1px solid {border};"
            " border-radius: 16px; }"
            "QLabel { background: transparent; border: none; font-weight: 600; }"
            "QToolButton { background: transparent; border: none; padding: 1px 4px;"
            f"  color: {c['muted']}; font-weight: 600; }}"
            f"QToolButton:hover {{ color: {c['accent']}; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type()
            in (
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
            )
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
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(8)
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


class _StatusDot(QFrame):
    """Tiny status indicator painted directly to avoid Qt QSS cache drift."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor("#808080")
        self.setFixedSize(10, 10)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect())
        painter.end()


class IndexStatusWidget(QFrame, PaletteMixin):
    """Compact search status: colored dot plus always-present text."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)
        self._dot = _StatusDot(self)
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

    def refresh_palette(self) -> None:
        self._apply()

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
        self._dot.set_color(color)
        self._label.setText(text)
        self.setToolTip(text)
        self.setAccessibleDescription(text)
        self.setStyleSheet(
            "IndexStatusWidget { background: transparent; border: none; }"
            f"QLabel {{ background: transparent; border: none; color: {c['muted']};"
            " font-weight: 600; }"
        )

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type()
            in (
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
            )
            and not getattr(self, "_refreshing_palette", False)
        ):
            self._refreshing_palette = True
            try:
                self._apply()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)


_ABOUT_PRIVACY_LOCAL = (
    "Searches, card contents, and indexes remain on this computer."
)
_ABOUT_PRIVACY_NETWORK = (
    "Networking occurs only when you explicitly set up or repair Semantic Search."
)
_ABOUT_PRIVACY = f"{_ABOUT_PRIVACY_LOCAL} {_ABOUT_PRIVACY_NETWORK}"
_ABOUT_INDEPENDENCE = (
    "Independent add-on; not affiliated with or endorsed by Anki."
)
_ABOUT_LOGO_SIZE = 96


class AboutPanel(QWidget, PaletteMixin):
    """The quiet About tab: logo, identity, attribution, privacy, actions.

    All content comes from :class:`AboutInfo` plus fixed truthful copy, so
    the panel stays a dumb renderer.  The Mobile App and Feedback buttons
    are the only outbound controls; they open only on explicit activation
    and carry no card or search data.
    """

    def __init__(
        self,
        about: AboutInfo,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._about = about
        self._url_opener = QDesktopServices.openUrl
        self.setObjectName("aboutPanel")
        self.setAccessibleName(f"About {about.product_name}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(42, 28, 42, 20)
        outer.setSpacing(9)
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

        outer.addSpacing(8)

        self.name_label = QLabel(about.product_name, self)
        name_font = self.name_label.font()
        name_font.setBold(True)
        name_font.setPointSizeF(name_font.pointSizeF() * 1.45)
        self.name_label.setFont(name_font)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setAccessibleName(about.product_name)
        outer.addWidget(self.name_label)

        self.version_label = QLabel(f"Version {about.version}", self)
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version_label.setAccessibleName(f"Version {about.version}")
        self.version_label.setVisible(bool(about.version))
        outer.addWidget(self.version_label)

        outer.addSpacing(6)

        self.attribution_label = QLabel(
            f"Created by {escape(about.creator)} with MedBrevia",
            self,
        )
        self.attribution_label.setTextFormat(Qt.TextFormat.RichText)
        self.attribution_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.attribution_label.setAccessibleName("Attribution")
        self.attribution_label.setAccessibleDescription(
            f"Built by MedBrevia. Created by {about.creator}."
        )
        outer.addWidget(self.attribution_label)

        outer.addSpacing(8)

        # The privacy promise sits in a quiet two-row card so the local-data
        # and networking guarantees remain easy to scan.
        self.privacy_callout = QFrame(self)
        self.privacy_callout.setObjectName("privacyCallout")
        callout = QVBoxLayout(self.privacy_callout)
        callout.setContentsMargins(18, 14, 18, 14)
        callout.setSpacing(10)

        local_row = QHBoxLayout()
        local_row.setSpacing(11)
        self.privacy_dot = _StatusDot(self.privacy_callout)
        self.privacy_dot.setAccessibleName("")
        local_row.addWidget(
            self.privacy_dot,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        self.privacy_label = QLabel(
            _ABOUT_PRIVACY_LOCAL,
            self.privacy_callout,
        )
        self.privacy_label.setWordWrap(True)
        self.privacy_label.setAccessibleName("Privacy statement")
        self.privacy_label.setAccessibleDescription(_ABOUT_PRIVACY)
        local_row.addWidget(self.privacy_label, 1)
        callout.addLayout(local_row)

        network_row = QHBoxLayout()
        network_row.setSpacing(11)
        self.network_dot = _StatusDot(self.privacy_callout)
        self.network_dot.setAccessibleName("")
        network_row.addWidget(
            self.network_dot,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        self.network_label = QLabel(
            _ABOUT_PRIVACY_NETWORK,
            self.privacy_callout,
        )
        self.network_label.setWordWrap(True)
        self.network_label.setAccessibleName("Networking statement")
        self.network_label.setAccessibleDescription(_ABOUT_PRIVACY_NETWORK)
        network_row.addWidget(self.network_label, 1)
        callout.addLayout(network_row)
        outer.addWidget(self.privacy_callout)

        outer.addSpacing(8)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        button_row.addStretch(1)
        self.mobile_button = QPushButton("Mobile App", self)
        self.mobile_button.setObjectName("aboutPrimaryButton")
        self.mobile_button.setMinimumSize(144, 42)
        self.mobile_button.setAccessibleName("Open the MedBrevia mobile app")
        self.mobile_button.setVisible(bool(about.website_url))
        self.mobile_button.clicked.connect(
            lambda _checked=False: self._open_link(about.website_url)
        )
        button_row.addWidget(self.mobile_button)

        self.feedback_button = QPushButton("Feedback", self)
        self.feedback_button.setObjectName("aboutSecondaryButton")
        self.feedback_button.setMinimumSize(128, 42)
        self.feedback_button.setAccessibleName("Send Smart Search feedback")
        self.feedback_button.setVisible(bool(about.feedback_url))
        self.feedback_button.clicked.connect(
            lambda _checked=False: self._open_link(about.feedback_url)
        )
        button_row.addWidget(self.feedback_button)
        button_row.addStretch(1)
        outer.addLayout(button_row)

        self.independence_label = QLabel(_ABOUT_INDEPENDENCE, self)
        self.independence_label.setWordWrap(True)
        self.independence_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.independence_label.setAccessibleName("Anki affiliation notice")
        self.independence_label.setAccessibleDescription(_ABOUT_INDEPENDENCE)
        outer.addWidget(self.independence_label)

        outer.addStretch(1)
        self.refresh_palette()

    def refresh_palette(self) -> None:
        c = self._palette_colors()
        self.setStyleSheet(
            "AboutPanel#aboutPanel { background: transparent; border: none; }"
            "QFrame#privacyCallout {"
            f" background: {c['surface_high']};"
            f" border: 1px solid {c['chip_border']}; border-radius: 12px;"
            "}"
            "QPushButton#aboutPrimaryButton {"
            f" background: {c['accent']}; color: {c['accent_text']};"
            f" border: 1px solid {c['accent']}; border-radius: 9px;"
            " font-weight: 700;"
            "}"
            "QPushButton#aboutSecondaryButton {"
            f" background: {c['surface_high']}; color: {c['text']};"
            f" border: 1px solid {c['chip_border']}; border-radius: 9px;"
            " font-weight: 700;"
            "}"
            "QPushButton#aboutSecondaryButton:hover {"
            f" border-color: {c['accent_mid']};"
            "}"
        )
        self.version_label.setStyleSheet(
            f"color: {c['muted']}; background: transparent;"
        )
        self.attribution_label.setStyleSheet(
            f"color: {c['muted']}; background: transparent;"
        )
        self.independence_label.setStyleSheet(
            f"color: {c['muted']}; background: transparent;"
        )
        self.privacy_dot.set_color(c["ok"])
        self.network_dot.set_color(c["ok"])
        self.privacy_label.setStyleSheet("background: transparent;")
        self.network_label.setStyleSheet("background: transparent;")

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type()
            in (
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
            )
            and not getattr(self, "_refreshing_palette", False)
        ):
            self._refreshing_palette = True
            try:
                self.refresh_palette()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)

    def _open_link(self, url: str) -> None:
        if url:
            self._url_opener(QUrl(str(url)))
