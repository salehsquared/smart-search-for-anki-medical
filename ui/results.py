"""Result list: model, custom delegate, and view.

Each note renders as a separated rounded card on the window background:
a bold elided primary line with a quiet sibling-card count at the right, a
single-line span-highlighted supporting excerpt, a muted deck/note-type
line, and a row of compact colored match-reason chips. The current row is
marked with a violet outline and subtle tint — never a solid bright fill —
so text contrast survives selection. Raw card HTML is never rendered —
snippets are plain text and every span is clamped and merged before
painting.
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
    QFont,
    QKeySequence,
    QListView,
    QModelIndex,
    QPalette,
    QPainter,
    QPen,
    QRect,
    QSize,
    QStyle,
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

# QListView multiplies its scrollbar single-step by the platform's configured
# wheel-line count (normally three). The automatic per-pixel step is one full
# 128px result card, which makes a normal mouse-wheel notch jump about three
# cards. Forty pixels keeps a notch close to one card while precision
# trackpads retain Qt's native pixel scrolling and momentum.
_MOUSE_WHEEL_SINGLE_STEP_PX = 40


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
    buried = sum(1 for state in states if state.buried)
    flag_counts = Counter(state.flag for state in states)
    parts: list[str] = []
    if suspended:
        parts.append(
            f"{suspended} of {total} "
            f"card{'s' if total != 1 else ''} suspended"
        )
    if buried:
        parts.append(
            f"{buried} of {total} "
            f"card{'s' if total != 1 else ''} buried"
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


def tag_summary(tags: Sequence[str], *, limit: int = 6) -> str:
    """Return a bounded metadata summary without changing stored tags."""

    visible = tuple(str(tag) for tag in tags[:limit] if str(tag))
    if not visible:
        return ""
    hidden = max(0, len(tags) - len(visible))
    suffix = f" (+{hidden} more)" if hidden else ""
    return "Tags: " + ", ".join(visible) + suffix


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

    def checked_note_ids(self) -> tuple[int, ...]:
        return tuple(
            result.note_id
            for result in self._results
            if result.note_id in self._checked_note_ids
        )

    def set_checked_note_ids(self, note_ids: Sequence[int]) -> None:
        """Atomically restore checks that still exist in this result set."""

        available = {result.note_id for result in self._results}
        desired = {
            int(note_id)
            for note_id in note_ids
            if int(note_id) in available
        }
        if desired == self._checked_note_ids:
            return
        self._checked_note_ids = desired
        if self._results:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._results) - 1, 0),
                [
                    Qt.ItemDataRole.CheckStateRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                ],
            )
        self.checkedChanged.emit()

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
        buried: Optional[bool] = None,
    ) -> None:
        """Update visible live-state indicators after a successful Anki op."""

        targets = {int(card_id) for card_id in card_ids if int(card_id) > 0}
        if not targets or (
            flag is None and suspended is None and buried is None
        ):
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
                    buried=(
                        state.buried
                        if buried is None
                        else bool(buried)
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
            details: list[str] = []
            state_summary = card_state_summary(result)
            if state_summary:
                details.append(state_summary)
            if result.related_by_tags:
                label = (
                    "Exact related tag"
                    if len(result.related_by_tags) == 1
                    else "Exact related tags"
                )
                details.append(label + ": " + ", ".join(result.related_by_tags))
            tags = tag_summary(result.tags)
            if tags:
                details.append(tags)
            return "\n".join(details) or None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            parts = [result.title, result.snippet, result.deck, result.note_type]
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
                tags = tag_summary(result.tags)
                if tags:
                    parts.append(tags)
                if result.related_by_tags:
                    parts.append(
                        "related through exact tag "
                        + ", ".join(result.related_by_tags)
                    )
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
    """Paint each result as a calm, separated card with explicit hierarchy."""

    ROW_GAP = 8
    CARD_INSET = 2
    CARD_RADIUS = 10
    H_PAD = 18
    V_PAD = 13
    CHECK_GUTTER = 34
    # Enough room for the suspension glyph and the multi-color flag when a
    # result contains both states. Keep this independent from the text column
    # so state indicators never collide with the title or snippet.
    STATE_GUTTER = 42
    CHIP_H = 22
    CHIP_H_PAD = 9
    CHIP_GAP = 7

    @classmethod
    def card_rect(cls, row_rect: QRect) -> QRect:
        return row_rect.adjusted(
            cls.CARD_INSET,
            cls.ROW_GAP // 2,
            -cls.CARD_INSET - 1,
            -(cls.ROW_GAP // 2) - 1,
        )

    @classmethod
    def checkbox_rect(cls, row_rect: QRect) -> QRect:
        card = cls.card_rect(row_rect)
        size = 22
        return QRect(
            card.left() + cls.H_PAD,
            card.top() + max(0, (card.height() - size) // 2),
            size,
            size,
        )

    def _colors(self, option: QStyleOptionViewItem) -> dict[str, object]:
        pal = option.palette
        # The view itself is intentionally transparent; Qt therefore exposes
        # transparent roles on some Anki themes. Prefer the widget-local
        # palette whenever it is concrete, and fall back to the application
        # palette only for those transparent roles.
        app_pal = QApplication.palette()

        def concrete(role: QPalette.ColorRole) -> QColor:
            color = pal.color(role)
            return app_pal.color(role) if color.alpha() == 0 else color

        window = concrete(QPalette.ColorRole.Window)
        base = concrete(QPalette.ColorRole.Base)
        text = concrete(QPalette.ColorRole.Text)
        muted = concrete(QPalette.ColorRole.PlaceholderText)
        accent = concrete(QPalette.ColorRole.Highlight)
        dark = base.lightness() < 128
        surface = blend_colors(
            window,
            text,
            0.06 if dark else 0.025,
        )
        surface.setAlpha(255)
        return {
            "text": text,
            "muted": muted,
            "accent": accent,
            "accent_text": concrete(QPalette.ColorRole.HighlightedText),
            "accent_soft": blend_colors(surface, accent, 0.16),
            "surface": surface,
            "hover": blend_colors(surface, accent, 0.065),
            "checked": blend_colors(surface, accent, 0.075),
            "border": blend_colors(surface, text, 0.16),
            "dark": dark,
        }

    def _draw_checkbox(
        self,
        painter: QPainter,
        rect: QRect,
        checked: bool,
        colors: dict[str, object],
    ) -> None:
        accent = QColor(colors["accent"])
        border = accent if checked else QColor(colors["muted"])
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(accent if checked else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 6, 6)
        if not checked:
            return
        pen = QPen(QColor(colors["accent_text"]), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(
            rect.left() + 5,
            rect.center().y(),
            rect.left() + 9,
            rect.bottom() - 5,
        )
        painter.drawLine(
            rect.left() + 9,
            rect.bottom() - 5,
            rect.right() - 4,
            rect.top() + 5,
        )

    def _draw_card_state(
        self,
        painter: QPainter,
        result: SearchResult,
        *,
        x: int,
        center_y: int,
        colors: dict[str, object],
    ) -> None:
        """Draw compact, wordless suspension and flag indicators."""

        states = result.card_states
        if not states:
            return
        suspended = sum(1 for state in states if state.suspended)
        cursor = x
        if suspended:
            icon = QRect(cursor, center_y - 8, 16, 16)
            suspension_color = (
                QColor("#c4ccd8")
                if bool(colors["dark"])
                else QColor("#5f6977")
            )
            suspension_color.setAlpha(255 if suspended == len(states) else 170)
            painter.setBrush(
                blend_colors(
                    QColor(colors["surface"]),
                    suspension_color,
                    0.08,
                )
            )
            painter.setPen(QPen(suspension_color, 1.4))
            painter.drawEllipse(icon)
            painter.drawLine(
                icon.left() + 3,
                icon.bottom() - 3,
                icon.right() - 3,
                icon.top() + 3,
            )
            cursor += 20

        flags = sorted({state.flag for state in states if state.flag})
        if not flags:
            return
        flag_palette = (
            _DARK_FLAG_COLORS if bool(colors["dark"]) else _LIGHT_FLAG_COLORS
        )
        flag_y = center_y - 7
        painter.fillRect(
            QRect(cursor, flag_y, 2, 15),
            QColor(colors["muted"]),
        )
        cloth = QRect(cursor + 2, flag_y, 14, 9)
        stripe_left = cloth.left()
        for offset, flag in enumerate(flags):
            stripe_right = (
                cloth.right() + 1
                if offset == len(flags) - 1
                else cloth.left()
                + round(cloth.width() * (offset + 1) / len(flags))
            )
            painter.fillRect(
                QRect(
                    stripe_left,
                    cloth.top(),
                    max(1, stripe_right - stripe_left),
                    cloth.height(),
                ),
                QColor(flag_palette[flag]),
            )
            stripe_left = stripe_right

    def _chip_palette(
        self,
        text: str,
        colors: dict[str, object],
    ) -> tuple[QColor, QColor, QColor]:
        lowered = text.casefold()
        dark = bool(colors["dark"])
        if any(word in lowered for word in ("exact", "filter", "all terms")):
            hue = QColor("#54d89a" if dark else "#16845b")
        elif any(word in lowered for word in ("alias", "concept", "related")):
            hue = QColor("#63aefc" if dark else "#176bb5")
        elif any(word in lowered for word in ("typo", "spell", "correct")):
            hue = QColor("#a795ff" if dark else "#6550cf")
        else:
            hue = QColor(colors["muted"])
        surface = QColor(colors["surface"])
        return (
            blend_colors(surface, hue, 0.13),
            blend_colors(surface, hue, 0.52),
            hue,
        )

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        line = option.fontMetrics.height()
        return QSize(option.rect.width(), max(128, line * 6 + 34))

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        result: Optional[SearchResult] = index.data(ResultRole)
        if result is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        if not enabled:
            # Prior results remain visible while a replacement search runs,
            # but their reduced opacity makes the temporarily inactive state
            # unmistakable.
            painter.setOpacity(0.52)
        colors = self._colors(option)
        focused = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        checked = (
            index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        )
        card = self.card_rect(option.rect)

        fill = QColor(colors["surface"])
        if checked:
            fill = QColor(colors["checked"])
        elif focused:
            fill = blend_colors(
                QColor(colors["surface"]),
                QColor(colors["accent"]),
                0.04,
            )
        elif hovered:
            fill = QColor(colors["hover"])
        outline = QColor(colors["accent"]) if checked else QColor(colors["border"])
        outline_width = 1.4 if checked else 1.0
        if focused and not checked:
            outline = blend_colors(
                QColor(colors["border"]),
                QColor(colors["accent"]),
                0.72,
            )
            outline_width = 1.2
        painter.setBrush(fill)
        painter.setPen(QPen(outline, outline_width))
        painter.drawRoundedRect(card, self.CARD_RADIUS, self.CARD_RADIUS)
        if focused and checked:
            # Checked is the bulk-action state; focused is the keyboard
            # navigation state. Show both simultaneously so a range of
            # checked rows still has one unambiguous current row.
            focus = QColor(colors["accent_text"])
            focus.setAlpha(145)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(focus, 1.2))
            inner = card.adjusted(3, 3, -3, -3)
            painter.drawRoundedRect(
                inner,
                self.CARD_RADIUS - 2,
                self.CARD_RADIUS - 2,
            )

        checkbox = self.checkbox_rect(option.rect)
        self._draw_checkbox(painter, checkbox, checked, colors)
        state_x = checkbox.right() + 14
        self._draw_card_state(
            painter,
            result,
            x=state_x,
            center_y=card.center().y(),
            colors=colors,
        )

        left = (
            card.left()
            + self.H_PAD
            + self.CHECK_GUTTER
            + self.STATE_GUTTER
        )
        right = card.right() - self.H_PAD
        width = max(80, right - left)
        y = card.top() + self.V_PAD

        count = ""
        if result.sibling_count > 1:
            count = f"{result.sibling_count} cards"
        elif result.sibling_count == 1:
            count = "1 card"
        count_font = QFont(option.font)
        count_font.setPointSizeF(max(count_font.pointSizeF() - 1.0, 8.0))
        painter.setFont(count_font)
        count_fm = painter.fontMetrics()
        count_width = count_fm.horizontalAdvance(count) if count else 0
        if count:
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(
                right - count_width,
                y,
                count_width,
                count_fm.height(),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                count,
            )

        title_font = QFont(option.font)
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() + 1.4)
        painter.setFont(title_font)
        title_fm = painter.fontMetrics()
        title_width = width - (count_width + 18 if count_width else 0)
        title = title_fm.elidedText(
            result.title or "(untitled note)",
            Qt.TextElideMode.ElideRight,
            title_width,
        )
        for span in merge_spans(clamp_spans(result.title_spans, len(title))):
            span_x = title_fm.horizontalAdvance(title[: span.start])
            span_width = title_fm.horizontalAdvance(
                title[span.start : span.end]
            )
            if span_width:
                painter.fillRect(
                    QRect(left + span_x, y, span_width, title_fm.height()),
                    QColor(colors["accent_soft"]),
                )
        painter.setPen(QColor(colors["text"]))
        painter.drawText(
            left,
            y,
            title_width,
            title_fm.height(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title,
        )

        line_h = option.fontMetrics.height()
        snippet_y = y + title_fm.height() + 5
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(option.font)
        doc.setHtml(
            '<div style="color:'
            + _hex(QColor(colors["text"]))
            + ';">'
            + snippet_html(
                result.snippet,
                result.spans,
                fg_hex=_hex(QColor(colors["text"])),
                hl_hex=_hex(QColor(colors["accent_soft"])),
            )
            + "</div>"
        )
        doc.setTextWidth(width)
        painter.save()
        painter.translate(left, snippet_y)
        painter.setClipRect(0, 0, width, line_h + 3)
        doc.drawContents(painter)
        painter.restore()

        meta_y = snippet_y + line_h + 6
        # Visible metadata stays quiet: deck and note type only. Raw
        # hierarchical tags remain on the result object for tooltips,
        # accessibility, actions, filtering, and Related matching.
        meta_parts = [part for part in (result.deck, result.note_type) if part]
        meta_font = QFont(option.font)
        meta_font.setPointSizeF(max(meta_font.pointSizeF() - 0.8, 8.0))
        meta_font.setBold(True)
        painter.setFont(meta_font)
        meta_fm = painter.fontMetrics()
        meta = meta_fm.elidedText(
            "  ›  ".join(meta_parts),
            Qt.TextElideMode.ElideRight,
            width,
        )
        painter.setPen(QColor(colors["muted"]))
        painter.drawText(
            left,
            meta_y,
            width,
            meta_fm.height(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            meta,
        )

        chip_y = meta_y + meta_fm.height() + 5
        chip_font = QFont(option.font)
        chip_font.setPointSizeF(max(chip_font.pointSizeF() - 0.9, 8.0))
        chip_font.setBold(True)
        painter.setFont(chip_font)
        chip_fm = painter.fontMetrics()
        chip_x = left
        # One explanation is enough at a glance. Additional reasons remain
        # represented by a compact +N chip and in the accessible row text.
        named_reasons = list(result.match_reasons[:1])
        hidden_reasons = max(0, len(result.match_reasons) - len(named_reasons))

        def chip_width(label: str) -> int:
            return chip_fm.horizontalAdvance(label) + 2 * self.CHIP_H_PAD

        def total_width(labels: Sequence[str]) -> int:
            if not labels:
                return 0
            return (
                sum(chip_width(label) for label in labels)
                + self.CHIP_GAP * (len(labels) - 1)
            )

        available_width = max(0, right - left)
        while True:
            overflow = f"+{hidden_reasons}" if hidden_reasons else ""
            reasons = named_reasons + ([overflow] if overflow else [])
            if total_width(reasons) <= available_width:
                break
            if named_reasons:
                named_reasons.pop()
                hidden_reasons += 1
                continue
            reasons = (
                [overflow]
                if overflow and chip_width(overflow) <= available_width
                else []
            )
            break
        for reason in reasons:
            width_for_chip = chip_width(reason)
            chip_rect = QRect(chip_x, chip_y, width_for_chip, self.CHIP_H)
            chip_fill, chip_border, chip_text = self._chip_palette(reason, colors)
            painter.setBrush(chip_fill)
            painter.setPen(QPen(chip_border, 1.0))
            painter.drawRoundedRect(chip_rect, self.CHIP_H // 2, self.CHIP_H // 2)
            painter.setPen(chip_text)
            painter.drawText(
                chip_rect,
                int(Qt.AlignmentFlag.AlignCenter),
                reason,
            )
            chip_x += width_for_chip + self.CHIP_GAP

        painter.restore()


class ResultsView(QListView):
    """Single-column, keyboard-first result list."""

    resultContextRequested = pyqtSignal(int, object)  # row, global QPoint
    currentResultChanged = pyqtSignal(object)  # SearchResult | None
    resultBrowsed = pyqtSignal(object)  # SearchResult

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
        self.verticalScrollBar().setSingleStep(_MOUSE_WHEEL_SINGLE_STEP_PX)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QListView { background: transparent; border: none; outline: none; }"
            "QListView::item { background: transparent; border: none; }"
        )
        self.setAccessibleName("Search results")
        self.setAccessibleDescription(
            "List of matching notes. With card preview enabled, press Space or "
            "Right Arrow to show the answer and Left Arrow to show the front. "
            "Click a checkbox or press Shift+Space to include a result in a "
            "bulk action; Shift-click a row to include a range. "
            "Right-click a row to open, create a copy, move, bury, flag, "
            "suspend, or tag the target selection. "
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
            self.scrollTo(
                self.currentIndex(),
                QListView.ScrollHint.EnsureVisible,
            )

    def row_for_note(self, note_id: int) -> int:
        target = int(note_id)
        for row, result in enumerate(self._model.results()):
            if result.note_id == target:
                return row
        return -1

    def move_current_row(self, offset: int) -> bool:
        """Move the highlight without changing independent bulk checkboxes."""

        count = self._model.count()
        if count <= 0:
            return False
        current = self.currentIndex().row()
        if not 0 <= current < count:
            target = 0 if offset >= 0 else count - 1
        else:
            target = max(0, min(count - 1, current + int(offset)))
        if target == current:
            return False
        self.select_row(target)
        return True

    def can_move(self, offset: int) -> bool:
        count = self._model.count()
        current = self.currentIndex().row()
        if count <= 0:
            return False
        if not 0 <= current < count:
            return True
        return 0 <= current + int(offset) < count

    def checked_results(self) -> tuple[SearchResult, ...]:
        return self._model.checked_results()

    def _reset_check_anchor(self) -> None:
        self._range_anchor_row = None

    def currentChanged(self, current: QModelIndex, previous: QModelIndex) -> None:
        """Publish highlighted-row changes for the optional native preview."""

        super().currentChanged(current, previous)
        self.currentResultChanged.emit(self._model.result_at(current.row()))

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
        previous_row = self.currentIndex().row()
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
            if row_index.row() == previous_row:
                result = self._model.result_at(row_index.row())
                if result is not None:
                    self.resultBrowsed.emit(result)

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
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            index = self.currentIndex()
            if index.isValid():
                # QListView's platform styles do not consistently emit
                # `activated` for Return. Emit it explicitly so keyboard
                # opening is deterministic while preserving double-click.
                self.activated.emit(index)
                event.accept()
                return
        if (
            event.key() == Qt.Key.Key_Space
            and event.modifiers() == Qt.KeyboardModifier.ShiftModifier
        ):
            row = self.currentIndex().row()
            if 0 <= row < self._model.count():
                self._model.toggle_checked(row)
                self._range_anchor_row = row
                event.accept()
                return
        if (
            event.key() == Qt.Key.Key_Space
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            # SearchDialog consumes this when a previewable result is focused.
            # Otherwise keep plain Space inert instead of changing meaning
            # based on whether Card preview is enabled or a card was deleted.
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self._model.set_all_checked(True)
            event.accept()
            return
        super().keyPressEvent(event)
