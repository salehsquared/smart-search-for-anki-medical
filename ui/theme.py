"""Shared palette-derived chrome styling for the smart-search UI.

Every color is derived from the widget's own ``QPalette`` (with a few
semantic status colors chosen per light/dark), so Anki's dark mode, light
mode, and custom themes all keep working — nothing here hardcodes a
dark-only look. Stylesheets are strings built on demand and reapplied by
the dialogs on palette changes; no per-frame work happens here.
"""

from __future__ import annotations

from .widgets import (  # reuse the Qt shim and blend helpers
    QApplication,
    QColor,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    Qt,
    QWidget,
    _hex,
    blend_colors,
)


def chrome_colors(widget: QWidget) -> dict[str, str]:
    """Derived colors for elevated surfaces, borders, and the accent."""

    pal = widget.palette()
    app_pal = QApplication.palette()

    def concrete(role):
        color = pal.color(role)
        return app_pal.color(role) if color.alpha() == 0 else color

    base = concrete(QPalette.ColorRole.Base)
    text = concrete(QPalette.ColorRole.Text)
    accent = concrete(QPalette.ColorRole.Highlight)
    accent_text = concrete(QPalette.ColorRole.HighlightedText)
    window = concrete(QPalette.ColorRole.Window)
    is_dark = base.lightness() < 128
    return {
        "window": _hex(window),
        "base": _hex(base),
        "text": _hex(text),
        "muted": _hex(concrete(QPalette.ColorRole.PlaceholderText)),
        "accent": _hex(accent),
        "accent_text": _hex(accent_text),
        "accent_soft": _hex(blend_colors(base, accent, 0.18)),
        "accent_mid": _hex(blend_colors(base, accent, 0.35)),
        "accent_hover": _hex(blend_colors(accent, accent_text, 0.14)),
        "accent_disabled": _hex(blend_colors(window, accent, 0.30)),
        # Elevated surfaces sit slightly above the window toward the text
        # color in dark mode and toward white in light mode.
        "surface": _hex(blend_colors(window, text, 0.055)),
        "surface_high": _hex(blend_colors(window, text, 0.085)),
        "border": _hex(blend_colors(window, text, 0.16)),
        "border_strong": _hex(blend_colors(window, text, 0.30)),
        "callout_bg": _hex(blend_colors(window, accent, 0.12)),
        "callout_border": _hex(blend_colors(window, accent, 0.38)),
        "ok": "#3d9a50" if is_dark else "#1d7a34",
        "warn": "#d9a13b" if is_dark else "#9a6a00",
        "err": "#e06c60" if is_dark else "#b3261e",
    }


def semantic_icon_pixmap(c: dict[str, str], size: int = 64) -> QPixmap:
    """A quiet ring-and-arc glyph for the semantic setup card."""

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    ring = QPen(QColor(c["callout_border"]), max(1.5, size * 0.03))
    painter.setPen(ring)
    margin = size * 0.08
    painter.drawEllipse(
        int(margin), int(margin), int(size - 2 * margin), int(size - 2 * margin)
    )
    arc = QPen(QColor(c["accent"]), max(3.0, size * 0.07))
    arc.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(arc)
    inset = size * 0.22
    # drawArc angles are in sixteenths of a degree, counterclockwise from 3 o'clock.
    painter.drawArc(
        int(inset),
        int(inset),
        int(size - 2 * inset),
        int(size - 2 * inset),
        50 * 16,
        280 * 16,
    )
    painter.end()
    return pixmap
