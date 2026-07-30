"""Result list: model, custom delegate, and view.

The delegate renders each note as a dense three-part row: a bold elided
title with match-reason badges on the right, a muted deck/note-type/tags
line, and a two-line snippet whose highlight spans are drawn with escaped
HTML inside a QTextDocument. Raw card HTML is never rendered — snippets are
plain text and every span is clamped and merged before painting.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from html import escape
from typing import Optional, Sequence

from .contracts import SearchResult, clamp_spans, merge_spans
from .widgets import (  # the Qt shim lives here
    QAbstractListModel,
    QApplication,
    QColor,
    QKeySequence,
    QListView,
    QModelIndex,
    QPalette,
    QPainter,
    QRect,
    QSize,
    QStyle,
    QStyleOptionButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextDocument,
    Qt,
    QWidget,
    _hex,
    blend_colors,
    pyqtSignal,
)

ResultRole = Qt.ItemDataRole.UserRole

_FLAG_NAMES = {
    1: "red",
    2: "orange",
    3: "green",
    4: "blue",
    5: "pink",
    6: "turquoise",
    7: "purple",
}
_LIGHT_FLAG_COLORS = {
    1: "#ef4444",
    2: "#fb923c",
    3: "#4ade80",
    4: "#3b82f6",
    5: "#e879f9",
    6: "#2dd4bf",
    7: "#a855f7",
}
_DARK_FLAG_COLORS = {
    1: "#f87171",
    2: "#fdba74",
    3: "#86efac",
    4: "#60a5fa",
    5: "#f0abfc",
    6: "#5eead4",
    7: "#c084fc",
}


def snippet_html(snippet: str, spans, fg_hex: str, hl_hex: str, *, bold_only: bool = False) -> str:
    """Build escaped, span-highlighted HTML for a plain-text snippet."""
    safe_spans = merge_spans(clamp_spans(spans, len(snippet)))
    parts: list[str] = []
    pos = 0
    for span in safe_spans:
        parts.append(escape(snippet[pos:span.start]))
        inner = escape(snippet[span.start:span.end])
        if bold_only:
            parts.append(f'<span style="font-weight:700;">{inner}</span>')
        else:
            parts.append(
                f'<span style="background-color:{hl_hex}; color:{fg_hex};'
                f' font-weight:600;">{inner}</span>'
            )
        pos = span.end
    parts.append(escape(snippet[pos:]))
    return "".join(parts)


def card_state_summary(result: SearchResult) -> str:
    """Describe live row indicators for tooltips and assistive technology."""

    states = result.card_states
    if not states:
        return ""
    total = len(states)
    suspended = sum(1 for state in states if state.suspended)
    flag_counts = Counter(state.flag for state in states)
    parts: list[str] = []
    if suspended:
        parts.append(
            f"{suspended} of {total} "
            f"card{'s' if total != 1 else ''} suspended"
        )
    flagged = [
        f"{_FLAG_NAMES[flag]} {count}"
        for flag, count in sorted(flag_counts.items())
        if flag
    ]
    if flagged:
        unflagged = flag_counts.get(0, 0)
        if unflagged:
            flagged.append(f"unflagged {unflagged}")
        parts.append("Flags: " + ", ".join(flagged))
    return ". ".join(parts) + ("." if parts else "")


class ResultsModel(QAbstractListModel):
    """List model over ``SearchResult`` dataclasses."""

    checkedChanged = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._results: list[SearchResult] = []
        # Checked rows are intentionally independent of the view's highlighted
        # row. Search results are one row per note, so note IDs are stable keys
        # for the lifetime of one result set.
        self._checked_note_ids: set[int] = set()

    def set_results(self, results: Sequence[SearchResult]) -> None:
        self.beginResetModel()
        self._results = list(results)
        self._checked_note_ids.clear()
        self.endResetModel()
        self.checkedChanged.emit()

    def clear(self) -> None:
        self.set_results([])

    def result_at(self, row: int) -> Optional[SearchResult]:
        if 0 <= row < len(self._results):
            return self._results[row]
        return None

    def count(self) -> int:
        return len(self._results)

    def results(self) -> tuple[SearchResult, ...]:
        return tuple(self._results)

    def refresh_results(self, results: Sequence[SearchResult]) -> bool:
        """Merge live card state only when the visible card scope still matches.

        Card-state hydration runs asynchronously after a search.  A response
        from an older search can therefore arrive after a newer search that
        happens to contain the same notes but a different subset of sibling
        cards.  Treat both the note and its exact card IDs as the row identity,
        and never let this refresh path replace search-owned metadata such as
        title, snippet, highlights, score, or match reasons.
        """

        incoming = list(results)
        current_identity = [
            (result.note_id, tuple(result.card_ids))
            for result in self._results
        ]
        incoming_identity = [
            (result.note_id, tuple(result.card_ids))
            for result in incoming
        ]
        if incoming_identity != current_identity:
            return False

        refreshed = [
            replace(
                current,
                card_states=fresh.card_states,
                sibling_count=fresh.sibling_count,
            )
            for current, fresh in zip(self._results, incoming)
        ]
        if refreshed == self._results:
            return True
        self._results = refreshed
        if self._results:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._results) - 1, 0),
                [
                    ResultRole,
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                ],
            )
        return True

    def is_checked(self, row: int) -> bool:
        result = self.result_at(row)
        return bool(result is not None and result.note_id in self._checked_note_ids)

    def checked_results(self) -> tuple[SearchResult, ...]:
        return tuple(
            result
            for result in self._results
            if result.note_id in self._checked_note_ids
        )

    def checked_counts(self) -> tuple[int, int]:
        """Return unique selected note/card counts in display order."""

        note_ids: set[int] = set()
        card_ids: set[int] = set()
        for result in self.checked_results():
            if result.note_id > 0:
                note_ids.add(int(result.note_id))
            card_ids.update(int(cid) for cid in result.card_ids if int(cid) > 0)
        return len(note_ids), len(card_ids)

    def set_checked(self, row: int, checked: bool) -> bool:
        result = self.result_at(row)
        if result is None:
            return False
        before = result.note_id in self._checked_note_ids
        if before == bool(checked):
            return False
        if checked:
            self._checked_note_ids.add(result.note_id)
        else:
            self._checked_note_ids.discard(result.note_id)
        index = self.index(row, 0)
        self.dataChanged.emit(
            index,
            index,
            [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.AccessibleTextRole],
        )
        self.checkedChanged.emit()
        return True

    def toggle_checked(self, row: int) -> bool:
        return self.set_checked(row, not self.is_checked(row))

    def set_range_checked(self, first: int, last: int, checked: bool) -> None:
        if not self._results:
            return
        low = max(0, min(first, last))
        high = min(len(self._results) - 1, max(first, last))
        changed = False
        for row in range(low, high + 1):
            result = self._results[row]
            before = result.note_id in self._checked_note_ids
            if before == bool(checked):
                continue
            changed = True
            if checked:
                self._checked_note_ids.add(result.note_id)
            else:
                self._checked_note_ids.discard(result.note_id)
        if changed:
            self.dataChanged.emit(
                self.index(low, 0),
                self.index(high, 0),
                [
                    Qt.ItemDataRole.CheckStateRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                ],
            )
            self.checkedChanged.emit()

    def set_all_checked(self, checked: bool = True) -> None:
        if not self._results:
            return
        desired = (
            {result.note_id for result in self._results}
            if checked
            else set()
        )
        if desired == self._checked_note_ids:
            return
        self._checked_note_ids = desired
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._results) - 1, 0),
            [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.AccessibleTextRole],
        )
        self.checkedChanged.emit()

    def invert_checked(self) -> None:
        if not self._results:
            return
        all_note_ids = {result.note_id for result in self._results}
        self._checked_note_ids = all_note_ids - self._checked_note_ids
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._results) - 1, 0),
            [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.AccessibleTextRole],
        )
        self.checkedChanged.emit()

    def apply_card_state_change(
        self,
        card_ids: Sequence[int],
        *,
        flag: Optional[int] = None,
        suspended: Optional[bool] = None,
    ) -> None:
        """Update visible live-state indicators after a successful Anki op."""

        targets = {int(card_id) for card_id in card_ids if int(card_id) > 0}
        if not targets or (flag is None and suspended is None):
            return
        changed_rows: list[int] = []
        for row, result in enumerate(self._results):
            states = []
            changed = False
            for state in result.card_states:
                if state.card_id not in targets:
                    states.append(state)
                    continue
                updated = replace(
                    state,
                    flag=state.flag if flag is None else int(flag),
                    suspended=(
                        state.suspended
                        if suspended is None
                        else bool(suspended)
                    ),
                )
                states.append(updated)
                changed = changed or updated != state
            if changed:
                self._results[row] = replace(result, card_states=tuple(states))
                changed_rows.append(row)
        if changed_rows:
            self.dataChanged.emit(
                self.index(min(changed_rows), 0),
                self.index(max(changed_rows), 0),
                [
                    ResultRole,
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                ],
            )

    # -- Qt API ------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._results)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        result = self.result_at(index.row())
        if result is None:
            return None
        if role == ResultRole:
            return result
        if role == Qt.ItemDataRole.CheckStateRole:
            return (
                Qt.CheckState.Checked
                if result.note_id in self._checked_note_ids
                else Qt.CheckState.Unchecked
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return card_state_summary(result) or None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            parts = [result.title, result.deck, result.note_type]
            if result.match_reasons:
                parts.append("matched by " + ", ".join(result.match_reasons))
            if result.sibling_count > 1:
                parts.append(f"{result.sibling_count} cards")
            if role == Qt.ItemDataRole.AccessibleTextRole:
                parts.insert(
                    0,
                    "checked"
                    if result.note_id in self._checked_note_ids
                    else "not checked",
                )
                state_summary = card_state_summary(result)
                if state_summary:
                    parts.append(state_summary)
            return ", ".join(p for p in parts if p)
        return None

    def setData(
        self,
        index: QModelIndex,
        value,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid():
            return False
        return self.set_checked(index.row(), value == Qt.CheckState.Checked)

    def flags(self, index: QModelIndex):
        flags = super().flags(index)
        if index.isValid():
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags


class ResultDelegate(QStyledItemDelegate):
    """Paints title + badges, meta line, and highlighted two-line snippet."""

    H_PAD = 12
    V_PAD = 8
    BADGE_H_PAD = 7
    BADGE_GAP = 6
    CHECK_GUTTER = 28
    STATE_GUTTER = 24

    # -- helpers -----------------------------------------------------------

    def _badges(self, result: SearchResult) -> list[str]:
        badges = list(result.match_reasons[:3])
        if len(result.match_reasons) > 3:
            badges.append(f"+{len(result.match_reasons) - 3}")
        if result.sibling_count > 1:
            badges.append(f"{result.sibling_count} cards")
        elif result.sibling_count == 1:
            badges.append("1 card")
        return badges

    def _colors(self, option: QStyleOptionViewItem) -> dict[str, object]:
        pal = option.palette
        base = pal.color(QPalette.ColorRole.Base)
        accent = pal.color(QPalette.ColorRole.Highlight)
        dark = base.lightness() < 128
        return {
            "text": _hex(pal.color(QPalette.ColorRole.Text)),
            "muted": _hex(pal.color(QPalette.ColorRole.PlaceholderText)),
            "hl_text": _hex(pal.color(QPalette.ColorRole.HighlightedText)),
            "accent": _hex(accent),
            "accent_soft": _hex(blend_colors(base, accent, 0.22)),
            "base": _hex(base),
            "suspended": "#fef9c3" if dark else "#facc15",
            "dark": dark,
        }

    def _draw_card_state(
        self,
        painter: QPainter,
        result: SearchResult,
        *,
        x: int,
        y: int,
        line_h: int,
        colors: dict[str, object],
    ) -> None:
        """Draw wordless suspension and flag indicators in a fixed gutter."""

        states = result.card_states
        if not states:
            return

        suspended = sum(1 for state in states if state.suspended)
        if suspended:
            color = QColor(str(colors["suspended"]))
            painter.setPen(color)
            bar_y = y + max(1, (line_h - 12) // 2)
            first = QRect(x + 2, bar_y, 3, 12)
            second = QRect(x + 9, bar_y, 3, 12)
            painter.fillRect(first, color)
            if suspended == len(states):
                painter.fillRect(second, color)
            else:
                painter.drawRect(second)

        flags = sorted({state.flag for state in states if state.flag})
        if not flags:
            return
        palette = _DARK_FLAG_COLORS if bool(colors["dark"]) else _LIGHT_FLAG_COLORS
        flag_y = y + line_h + 4
        pole = QRect(x + 1, flag_y, 2, 12)
        painter.fillRect(pole, QColor(str(colors["muted"])))
        cloth = QRect(x + 3, flag_y, 14, 8)
        stripe_left = cloth.left()
        for offset, flag in enumerate(flags):
            stripe_right = (
                cloth.right() + 1
                if offset == len(flags) - 1
                else cloth.left() + round(cloth.width() * (offset + 1) / len(flags))
            )
            painter.fillRect(
                QRect(
                    stripe_left,
                    cloth.top(),
                    max(1, stripe_right - stripe_left),
                    cloth.height(),
                ),
                QColor(palette[flag]),
            )
            stripe_left = stripe_right
        outline = QColor(str(colors["base"]))
        painter.setPen(outline)
        painter.drawRect(cloth)

    @classmethod
    def checkbox_rect(cls, row_rect: QRect) -> QRect:
        style = QApplication.style()
        width = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth)
        height = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight)
        return QRect(
            row_rect.left() + cls.H_PAD,
            row_rect.top() + max(0, (row_rect.height() - height) // 2),
            width,
            height,
        )

    # -- Qt API ------------------------------------------------------------

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        fm = option.fontMetrics
        line = fm.height()
        height = self.V_PAD * 2 + line + 2 + line + 4 + 2 * line + 4
        return QSize(option.rect.width(), height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        result: Optional[SearchResult] = index.data(ResultRole)
        if result is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colors = self._colors(option)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect

        if selected:
            painter.fillRect(rect, option.palette.color(QPalette.ColorRole.Highlight))
        elif hovered:
            painter.fillRect(rect, option.palette.color(QPalette.ColorRole.AlternateBase))

        fg = colors["hl_text"] if selected else colors["text"]
        muted = colors["hl_text"] if selected else colors["muted"]
        fm = option.fontMetrics
        line_h = fm.height()
        left = (
            rect.left()
            + self.H_PAD
            + self.CHECK_GUTTER
            + self.STATE_GUTTER
        )
        width = (
            rect.width()
            - 2 * self.H_PAD
            - self.CHECK_GUTTER
            - self.STATE_GUTTER
        )
        y = rect.top() + self.V_PAD

        checkbox = QStyleOptionButton()
        checkbox.rect = self.checkbox_rect(rect)
        checkbox.palette = option.palette
        checkbox.state = QStyle.StateFlag.State_Enabled
        if index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked:
            checkbox.state |= QStyle.StateFlag.State_On
        else:
            checkbox.state |= QStyle.StateFlag.State_Off
        QApplication.style().drawControl(
            QStyle.ControlElement.CE_CheckBox,
            checkbox,
            painter,
        )
        self._draw_card_state(
            painter,
            result,
            x=rect.left() + self.H_PAD + self.CHECK_GUTTER,
            y=y,
            line_h=line_h,
            colors=colors,
        )

        # --- title row: badges are right-aligned, title elided around them
        badge_font = painter.font()
        badge_font.setPointSizeF(max(badge_font.pointSizeF() - 1.5, 7.0))
        painter.setFont(badge_font)
        badge_fm = painter.fontMetrics()
        badges = self._badges(result)
        badge_widths = [badge_fm.horizontalAdvance(b) + 2 * self.BADGE_H_PAD for b in badges]
        badges_total = sum(badge_widths) + self.BADGE_GAP * max(len(badges) - 1, 0)

        title_font = option.font
        title_font.setBold(True)
        painter.setFont(title_font)
        title_fm = painter.fontMetrics()
        title_width = width - (badges_total + 12 if badges_total else 0)
        elided_title = title_fm.elidedText(
            result.title or "(untitled note)", Qt.TextElideMode.ElideRight, title_width
        )
        painter.setPen(QColor(fg))
        painter.drawText(
            left, y, title_width, line_h,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided_title,
        )

        badge_x = rect.right() - self.H_PAD
        painter.setFont(badge_font)
        for badge, bw in zip(reversed(badges), reversed(badge_widths)):
            badge_x -= bw
            badge_rect = (badge_x, y + max((line_h - badge_fm.height()) // 2 - 1, 0), bw, badge_fm.height() + 3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["accent_soft"]))
            painter.drawRoundedRect(*badge_rect, 6, 6)
            painter.setPen(QColor(fg))
            painter.drawText(*badge_rect, int(Qt.AlignmentFlag.AlignCenter), badge)
            badge_x -= self.BADGE_GAP

        # --- meta line: deck · note type · tags
        y += line_h + 2
        meta_parts = [p for p in (result.deck, result.note_type) if p]
        if result.tags:
            meta_parts.append(" ".join("#" + t for t in result.tags[:4]))
        meta = fm.elidedText("  ·  ".join(meta_parts), Qt.TextElideMode.ElideRight, width)
        meta_font = option.font
        meta_font.setPointSizeF(max(meta_font.pointSizeF() - 1.0, 7.0))
        meta_font.setBold(False)
        painter.setFont(meta_font)
        painter.setPen(QColor(muted))
        painter.drawText(left, y, width, line_h, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), meta)

        # --- snippet: two lines, span-highlighted, always escaped
        y += line_h + 4
        clip_h = 2 * line_h + 2
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        html = snippet_html(
            result.snippet,
            result.spans,
            fg_hex=colors["hl_text"] if selected else colors["text"],
            hl_hex=colors["accent_soft"],
            bold_only=selected,
        )
        doc.setHtml(
            f'<div style="color:{fg};">{html}</div>'
        )
        doc.setTextWidth(width)
        painter.save()
        painter.translate(left, y)
        painter.setClipRect(0, 0, width, clip_h)
        doc.drawContents(painter)
        painter.restore()  # pops translate + clip

        painter.restore()


class ResultsView(QListView):
    """Single-column, keyboard-first result list."""

    resultContextRequested = pyqtSignal(int, object)  # row, global QPoint

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._model = ResultsModel(self)
        self._delegate = ResultDelegate(self)
        self.setModel(self._model)
        self.setItemDelegate(self._delegate)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName("Search results")
        self.setAccessibleDescription(
            "List of matching notes. Click a checkbox or press Space to include "
            "a result in a bulk action; Shift-click a row to include a range. "
            "Right-click a row to open, flag, suspend, or tag the target selection. "
            "Press Return to open the highlighted note, or Up on the first row "
            "to return to the search field."
        )
        self._range_anchor_row: Optional[int] = None
        self._model.modelReset.connect(self._reset_check_anchor)

    def results_model(self) -> ResultsModel:
        return self._model

    def current_result(self) -> Optional[SearchResult]:
        return self._model.result_at(self.currentIndex().row())

    def select_row(self, row: int) -> None:
        if 0 <= row < self._model.count():
            self.setCurrentIndex(self._model.index(row, 0))

    def checked_results(self) -> tuple[SearchResult, ...]:
        return self._model.checked_results()

    def _reset_check_anchor(self) -> None:
        self._range_anchor_row = None

    def check_range_to(self, row: int, *, checked: bool = True) -> bool:
        """Check an inclusive range from the persistent click/keyboard anchor."""

        if not 0 <= row < self._model.count():
            return False
        anchor = self._range_anchor_row
        if anchor is None:
            current = self.currentIndex().row()
            anchor = current if 0 <= current < self._model.count() else row
            self._range_anchor_row = anchor
        self._model.set_range_checked(anchor, row, checked)
        self.setCurrentIndex(self._model.index(row, 0))
        return True

    def _checkbox_index_at(self, position) -> QModelIndex:
        index = self.indexAt(position)
        if (
            index.isValid()
            and ResultDelegate.checkbox_rect(self.visualRect(index)).contains(position)
        ):
            return index
        return QModelIndex()

    def mousePressEvent(self, event) -> None:
        position = event.position().toPoint()
        row_index = self.indexAt(position)
        checkbox_index = self._checkbox_index_at(position)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        left_click = event.button() == Qt.MouseButton.LeftButton

        if left_click and shift and row_index.isValid():
            row = row_index.row()
            checked = not self._model.is_checked(row)
            # Shift-clicking a row body adds the range. Shift-clicking its
            # checkbox applies the target checkbox's toggled state, allowing a
            # range to be cleared as well.
            self.check_range_to(
                row,
                checked=checked if checkbox_index.isValid() else True,
            )
            event.accept()
            return

        if left_click and checkbox_index.isValid():
            row = checkbox_index.row()
            self._model.toggle_checked(row)
            self.setCurrentIndex(checkbox_index)
            self._range_anchor_row = row
            event.accept()
            return

        super().mousePressEvent(event)
        if left_click and row_index.isValid():
            self._range_anchor_row = row_index.row()

    def mouseDoubleClickEvent(self, event) -> None:
        # The first press already toggled the checkbox. Swallow the second
        # click so QListView does not emit `activated` and open the note.
        if self._checkbox_index_at(event.position().toPoint()).isValid():
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        """Ask the dialog for a menu only when a real result was clicked."""

        index = self.indexAt(event.pos())
        if not index.isValid():
            event.ignore()
            return
        self.resultContextRequested.emit(index.row(), event.globalPos())
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            row = self.currentIndex().row()
            if 0 <= row < self._model.count():
                self._model.toggle_checked(row)
                self._range_anchor_row = row
                event.accept()
                return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self._model.set_all_checked(True)
            event.accept()
            return
        super().keyPressEvent(event)
