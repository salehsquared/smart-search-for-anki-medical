#!/usr/bin/env python3
"""Render deterministic, privacy-safe publication mockups.

The screenshots intentionally use synthetic study content. They are release
artwork, not captures from Saleh's Anki profile and not evidence of compatibility
testing. SVG output uses only the Python standard library; ``--png`` additionally
requires ``rsvg-convert``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
from pathlib import Path
import shutil
import subprocess


WIDTH = 1440
HEIGHT = 900
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUTPUT = HERE / "assets" / "screenshots"
LOGO = ROOT / "resources" / "medbrevia-logo.png"

FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', sans-serif"
BG = "#101217"
WINDOW = "#1c2026"
TITLE_BAR = "#252a31"
SURFACE = "#242930"
SURFACE_2 = "#2d333c"
BORDER = "#414957"
TEXT = "#f3f5f8"
MUTED = "#aeb6c2"
FAINT = "#7e8794"
ACCENT = "#7b6dff"
ACCENT_2 = "#9f95ff"
GREEN = "#51d092"
ORANGE = "#ffb866"
RED = "#ff6f79"
BLUE = "#68a8ff"
PINK = "#f478c5"


def rect(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    fill: str,
    radius: float = 0,
    stroke: str = "none",
    stroke_width: float = 1,
    opacity: float = 1,
) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="{radius}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{stroke_width}" opacity="{opacity}"/>'
    )


def line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    stroke: str,
    width: float = 1,
    opacity: float = 1,
) -> str:
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        f'stroke="{stroke}" stroke-width="{width}" opacity="{opacity}"/>'
    )


def circle(
    cx: float,
    cy: float,
    radius: float,
    *,
    fill: str,
    stroke: str = "none",
    stroke_width: float = 1,
) -> str:
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{stroke_width}"/>'
    )


def text(
    x: float,
    y: float,
    value: str,
    *,
    size: float = 18,
    fill: str = TEXT,
    weight: int = 400,
    anchor: str = "start",
    letter_spacing: float = 0,
) -> str:
    return (
        f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" '
        f'font-family="{FONT}" font-weight="{weight}" text-anchor="{anchor}" '
        f'letter-spacing="{letter_spacing}">{escape(value)}</text>'
    )


def pill(
    x: float,
    y: float,
    label: str,
    *,
    fill: str = SURFACE_2,
    color: str = MUTED,
    border: str = BORDER,
    width: float | None = None,
    dot: str | None = None,
) -> tuple[str, float]:
    calculated = max(76, 24 + len(label) * 8.1 + (18 if dot else 0))
    width = width or calculated
    pieces = [rect(x, y, width, 34, fill=fill, radius=17, stroke=border)]
    offset = 14
    if dot:
        pieces.append(circle(x + 16, y + 17, 4.5, fill=dot))
        offset = 28
    pieces.append(text(x + offset, y + 23, label, size=14, fill=color, weight=600))
    return "".join(pieces), width


def button(
    x: float,
    y: float,
    label: str,
    *,
    width: float | None = None,
    primary: bool = False,
    disabled: bool = False,
) -> tuple[str, float]:
    width = width or max(82, 30 + len(label) * 8.5)
    fill = ACCENT if primary else SURFACE_2
    border = ACCENT if primary else BORDER
    color = "#ffffff" if primary else TEXT
    opacity = 0.42 if disabled else 1
    markup = (
        f'<g opacity="{opacity}">'
        + rect(x, y, width, 38, fill=fill, radius=8, stroke=border)
        + text(x + width / 2, y + 25, label, size=14, fill=color, weight=650, anchor="middle")
        + "</g>"
    )
    return markup, width


def checkbox(x: float, y: float, *, checked: bool = False, partial: bool = False) -> str:
    fill = ACCENT if checked or partial else SURFACE
    border = ACCENT if checked or partial else "#6a7381"
    pieces = [rect(x, y, 22, 22, fill=fill, radius=6, stroke=border, stroke_width=1.5)]
    if checked:
        pieces.append(
            f'<path d="M {x + 5} {y + 11.5} l 4 4 l 8 -9" fill="none" '
            'stroke="#fff" stroke-width="2.3" stroke-linecap="round" '
            'stroke-linejoin="round"/>'
        )
    elif partial:
        pieces.append(
            line(x + 5, y + 11, x + 17, y + 11, stroke="#fff", width=2.4)
        )
    return "".join(pieces)


def base_start(title_value: str = "Smart Search") -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(title_value)}">',
        "<defs>",
        '<linearGradient id="background" x1="0" y1="0" x2="1" y2="1">',
        '<stop offset="0" stop-color="#11141a"/>',
        '<stop offset="1" stop-color="#19152b"/>',
        "</linearGradient>",
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="160%">',
        '<feDropShadow dx="0" dy="18" stdDeviation="24" flood-color="#000" flood-opacity=".48"/>',
        "</filter>",
        "</defs>",
        rect(0, 0, WIDTH, HEIGHT, fill="url(#background)"),
        rect(40, 34, 1360, 832, fill=WINDOW, radius=18, stroke="#333946", stroke_width=1.2),
        rect(40, 34, 1360, 64, fill=TITLE_BAR, radius=18),
        rect(40, 78, 1360, 20, fill=TITLE_BAR),
        circle(72, 66, 7, fill="#ff635f"),
        circle(96, 66, 7, fill="#ffbf44"),
        circle(120, 66, 7, fill="#36c84a"),
        circle(167, 66, 16, fill=ACCENT),
        '<path d="M160 67 h14 M167 60 v14" stroke="#fff" stroke-width="2" stroke-linecap="round"/>',
        text(194, 73, title_value, size=18, weight=650),
        text(1368, 72, "Synthetic release preview", size=13, fill=FAINT, anchor="end"),
    ]


def base_end(parts: list[str]) -> str:
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def mode_tabs(active: str) -> str:
    pieces: list[str] = []
    x = 1070
    for label in ("Smart", "Exact", "Semantic"):
        selected = label == active
        pieces.append(
            rect(
                x,
                121,
                96,
                48,
                fill=ACCENT if selected else SURFACE,
                radius=8,
                stroke=ACCENT if selected else BORDER,
            )
        )
        pieces.append(
            text(
                x + 48,
                151,
                label,
                size=14,
                fill="#fff" if selected else MUTED,
                weight=700 if selected else 550,
                anchor="middle",
            )
        )
        x += 98
    return "".join(pieces)


def search_header(query: str, active: str) -> list[str]:
    return [
        rect(68, 121, 984, 48, fill=SURFACE, radius=10, stroke=BORDER),
        circle(92, 145, 8, fill="none", stroke=MUTED, stroke_width=2),
        line(98, 151, 104, 157, stroke=MUTED, width=2),
        text(117, 152, query, size=17, weight=500),
        circle(1027, 145, 9, fill="#3c424d"),
        text(1027, 150, "×", size=14, fill=MUTED, weight=600, anchor="middle"),
        mode_tabs(active),
    ]


def selection_bar(
    selected_notes: int,
    selected_cards: int,
    *,
    partial: bool = False,
    actions_enabled: bool = True,
) -> str:
    pieces = [
        rect(68, 230, 1304, 56, fill="#20242a", radius=10, stroke="#343b46"),
        checkbox(88, 247, checked=selected_notes > 0 and not partial, partial=partial),
        text(
            122,
            263,
            f"{selected_notes} {'note' if selected_notes == 1 else 'notes'} · "
            f"{selected_cards} {'card' if selected_cards == 1 else 'cards'} selected",
            size=15,
            fill=TEXT if selected_notes else MUTED,
            weight=600,
        ),
    ]
    cursor = 340
    for label, width in (("Select ▾", 96), ("Browser", 98)):
        markup, _ = button(
            cursor,
            239,
            label,
            width=width,
            disabled=(label == "Browser" and not actions_enabled),
        )
        pieces.append(markup)
        cursor += width + 10
    cursor = 1036
    for label, width in (("Flag ▾", 92), ("Suspend ▾", 112), ("Tags ▾", 92)):
        markup, _ = button(
            cursor,
            239,
            label,
            width=width,
            disabled=not actions_enabled,
        )
        pieces.append(markup)
        cursor += width + 10
    return "".join(pieces)


def card_state_icon(
    x: float,
    y: float,
    *,
    flag: str | None = None,
    suspended: bool = False,
) -> str:
    pieces: list[str] = []
    if suspended:
        pieces.extend(
            [
                circle(x, y, 11, fill="#363c46", stroke="#697382"),
                line(x - 6, y + 6, x + 6, y - 6, stroke="#d5d9df", width=2),
            ]
        )
    if flag:
        pieces.extend(
            [
                f'<path d="M {x + 18} {y - 10} v20" stroke="{flag}" stroke-width="2.2"/>',
                f'<path d="M {x + 19} {y - 9} h13 l-3.5 5 l3.5 5 h-13 z" fill="{flag}"/>',
            ]
        )
    return "".join(pieces)


def result_row(
    y: float,
    *,
    title_value: str,
    deck: str,
    snippet: str,
    badges: tuple[tuple[str, str], ...],
    checked: bool = False,
    flag: str | None = None,
    suspended: bool = False,
    cards: int = 1,
) -> str:
    row_fill = "#292d36" if checked else SURFACE
    border = ACCENT if checked else "#343b46"
    pieces = [
        rect(68, y, 1304, 142, fill=row_fill, radius=12, stroke=border),
        checkbox(88, y + 58, checked=checked),
        card_state_icon(135, y + 69, flag=flag, suspended=suspended),
        text(181, y + 34, title_value, size=20, weight=680),
        text(181, y + 61, snippet, size=15, fill="#d6dbe2"),
        text(181, y + 91, deck, size=13, fill=FAINT, weight=550),
        text(
            1339,
            y + 34,
            f"{cards} {'card' if cards == 1 else 'cards'}",
            size=13,
            fill=FAINT,
            anchor="end",
        ),
    ]
    cursor = 181
    for label, color in badges:
        markup, width = pill(
            cursor,
            y + 101,
            label,
            fill="#222733",
            color=color,
            border="#3d4553",
        )
        pieces.append(markup)
        cursor += width + 8
    return "".join(pieces)


def bottom_bar(
    *,
    status: str,
    status_color: str = GREEN,
    summary: str = "",
    action: str | None = None,
) -> str:
    pieces = [
        line(68, 797, 1372, 797, stroke="#343a45"),
        circle(83, 825, 5.5, fill=status_color),
        text(97, 830, status, size=14, fill=MUTED, weight=600),
    ]
    if action:
        markup, _ = button(1007, 807, action, width=154, primary=True)
        pieces.append(markup)
    pieces.append(text(1190 if not action else 990, 830, summary, size=14, fill=FAINT, anchor="end"))
    settings, _ = button(1212, 807, "Settings", width=76)
    refresh, _ = button(1298, 807, "Refresh", width=74)
    pieces.extend([settings, refresh])
    return "".join(pieces)


def smart_search() -> str:
    parts = base_start()
    parts.extend(search_header("buproprion  tag:psychopharmacology", "Smart"))
    parts.append(rect(68, 184, 1304, 36, fill="none"))
    x = 68
    for label, color, border in (
        ("tag: psychopharmacology", GREEN, "#366853"),
        ("buproprion → bupropion", ACCENT_2, "#5b52a9"),
        ("generic + brand aliases", BLUE, "#3e638f"),
    ):
        markup, width = pill(x, 184, label, color=color, border=border)
        parts.append(markup)
        x += width + 10
    parts.append(selection_bar(0, 0, actions_enabled=False))
    parts.append(
        result_row(
            300,
            title_value="Bupropion — mechanism and uses",
            deck="Synthetic Medical Study  ›  Psychiatry",
            snippet="Bupropion inhibits norepinephrine and dopamine reuptake and is also used for smoking cessation.",
            badges=(("typo fix", ACCENT_2), ("exact concept", GREEN)),
            cards=2,
        )
    )
    parts.append(
        result_row(
            452,
            title_value="Smoking-cessation pharmacotherapy",
            deck="Synthetic Medical Study  ›  Preventive Medicine",
            snippet="Bupropion and varenicline are non-nicotine options used alongside behavioral support.",
            badges=(("alias", BLUE), ("related phrase", MUTED)),
            flag=ORANGE,
            cards=1,
        )
    )
    parts.append(
        result_row(
            604,
            title_value="Antidepressant adverse-effect comparison",
            deck="Synthetic Medical Study  ›  Pharmacology",
            snippet="Bupropion is less likely to cause sexual dysfunction but can lower the seizure threshold.",
            badges=(("exact", GREEN),),
            suspended=True,
            cards=3,
        )
    )
    parts.append(
        bottom_bar(
            status="Smart & Exact ready · Semantic ready",
            summary="3 strong matches · 18 ms",
        )
    )
    return base_end(parts)


def semantic_setup() -> str:
    parts = base_start()
    parts.extend(search_header("causes of painless hematuria", "Semantic"))
    parts.extend(
        [
            rect(68, 190, 1304, 74, fill="#25243a", radius=12, stroke="#504b8b"),
            circle(98, 227, 13, fill=ACCENT),
            text(98, 232, "i", size=15, fill="#fff", weight=750, anchor="middle"),
            text(
                124,
                217,
                "Semantic has a separate one-time setup and index.",
                size=16,
                weight=680,
            ),
            text(
                124,
                242,
                "Smart and Exact remain available now—and throughout Semantic preparation.",
                size=14,
                fill="#cfd3df",
            ),
        ]
    )
    parts.extend(
        [
            rect(312, 315, 816, 348, fill=SURFACE, radius=18, stroke="#3a414d"),
            circle(720, 384, 42, fill="#312e54", stroke="#5c54a4", stroke_width=1.5),
            '<path d="M697 384 c0-16 10-27 23-27 c13 0 23 11 23 27 c0 17-10 27-23 27" '
            'fill="none" stroke="#9d93ff" stroke-width="4" stroke-linecap="round"/>',
            text(720, 464, "Set up Semantic Search", size=25, weight=720, anchor="middle"),
            text(
                720,
                499,
                "Enable a private, local search by clinical meaning.",
                size=16,
                fill=MUTED,
                anchor="middle",
            ),
            text(
                720,
                529,
                "The required files are downloaded only after you choose setup.",
                size=15,
                fill=MUTED,
                anchor="middle",
            ),
            text(
                720,
                563,
                "Available on macOS 14+ with Apple silicon",
                size=14,
                fill=ACCENT_2,
                weight=650,
                anchor="middle",
            ),
        ]
    )
    setup, _ = button(610, 590, "Set Up Semantic Search", width=220, primary=True)
    parts.append(setup)
    parts.append(
        bottom_bar(
            status="Smart & Exact ready",
            summary="Semantic needs setup",
            status_color=GREEN,
        )
    )
    return base_end(parts)


def bulk_actions() -> str:
    parts = base_start()
    parts.extend(search_header('deck:"Synthetic FM Shelf"  is:suspended', "Exact"))
    x = 68
    for label, color, border in (
        ("deck: Synthetic FM Shelf", BLUE, "#3e638f"),
        ("is: suspended", ORANGE, "#796040"),
    ):
        markup, width = pill(x, 184, label, color=color, border=border)
        parts.append(markup)
        x += width + 10
    parts.append(selection_bar(2, 4, partial=True, actions_enabled=True))
    parts.append(
        result_row(
            300,
            title_value="Hypertension screening and confirmation",
            deck="Synthetic FM Shelf  ›  Preventive Care",
            snippet="Confirm an elevated office blood pressure with measurements outside the clinical setting.",
            badges=(("exact filter", GREEN),),
            checked=True,
            flag=RED,
            suspended=True,
            cards=2,
        )
    )
    parts.append(
        result_row(
            452,
            title_value="Adult immunization intervals",
            deck="Synthetic FM Shelf  ›  Immunization",
            snippet="Review age, pregnancy, risk conditions, prior doses, and immunocompromising conditions.",
            badges=(("exact filter", GREEN),),
            checked=True,
            flag=BLUE,
            suspended=True,
            cards=2,
        )
    )
    parts.append(
        result_row(
            604,
            title_value="Colorectal cancer screening",
            deck="Synthetic FM Shelf  ›  Screening",
            snippet="Offer an evidence-based screening strategy using age, risk, history, and patient preference.",
            badges=(("same deck", MUTED),),
            suspended=True,
            cards=1,
        )
    )
    parts.append(
        bottom_bar(
            status="Smart & Exact ready",
            summary="3 results · 2 selected",
        )
    )
    return base_end(parts)


def about_privacy() -> str:
    parts = base_start("Smart Search Settings")
    logo_data = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    parts.extend(
        [
            rect(268, 128, 904, 620, fill=SURFACE, radius=16, stroke="#3b424e"),
            rect(268, 128, 904, 58, fill="#282d35", radius=16),
            rect(268, 170, 904, 16, fill="#282d35"),
            rect(280, 138, 124, 38, fill="#282d35", radius=8),
            text(342, 163, "Search", size=15, fill=MUTED, weight=600, anchor="middle"),
            rect(410, 138, 124, 38, fill=ACCENT, radius=8),
            text(472, 163, "About", size=15, fill="#fff", weight=700, anchor="middle"),
            f'<image href="data:image/png;base64,{logo_data}" x="644" y="219" width="152" height="152"/>',
            text(720, 409, "Smart Search for Anki — Medical", size=27, weight=740, anchor="middle"),
            text(720, 440, "Version 1.0.12", size=14, fill=FAINT, weight=600, anchor="middle"),
            text(720, 483, "Created by Saleh Mostafa with MedBrevia", size=17, fill=MUTED, anchor="middle"),
            text(
                720,
                525,
                "Fast, forgiving medical search—without sending card content away.",
                size=16,
                fill="#d7dbe2",
                anchor="middle",
            ),
            rect(390, 555, 660, 88, fill="#20242b", radius=12, stroke="#363e49"),
            circle(426, 584, 7, fill=GREEN),
            text(446, 590, "Searches, card contents, and indexes remain on this computer.", size=14, fill=MUTED),
            circle(426, 616, 7, fill=GREEN),
            text(446, 622, "Networking occurs only when you explicitly set up or repair Semantic Search.", size=14, fill=MUTED),
        ]
    )
    mobile, _ = button(562, 672, "Mobile App", width=142, primary=True)
    feedback, _ = button(718, 672, "Feedback", width=126)
    parts.extend(
        [
            mobile,
            feedback,
            text(720, 789, "medbrevia.com/app", size=14, fill=ACCENT_2, weight=600, anchor="middle"),
            text(1368, 830, "No real collection data shown", size=13, fill=FAINT, anchor="end"),
        ]
    )
    return base_end(parts)


SCREENS = {
    "01-smart-search.svg": smart_search,
    "02-semantic-setup.svg": semantic_setup,
    "03-bulk-actions.svg": bulk_actions,
    "04-about-privacy.svg": about_privacy,
}


def write_checksums(paths: list[Path]) -> None:
    lines = []
    for path in sorted(paths, key=lambda item: item.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (OUTPUT / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--png",
        action="store_true",
        help="also render PNG copies with rsvg-convert",
    )
    args = parser.parse_args()

    OUTPUT.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for filename, renderer in SCREENS.items():
        path = OUTPUT / filename
        path.write_text(renderer(), encoding="utf-8")
        generated.append(path)

    if args.png:
        converter = shutil.which("rsvg-convert")
        if converter is None:
            raise SystemExit("--png requires rsvg-convert")
        for svg_path in generated.copy():
            png_path = svg_path.with_suffix(".png")
            subprocess.run(
                [
                    converter,
                    "--format=png",
                    f"--width={WIDTH}",
                    f"--height={HEIGHT}",
                    f"--output={png_path}",
                    str(svg_path),
                ],
                check=True,
            )
            generated.append(png_path)

    write_checksums(generated)
    print(f"Rendered {len(generated)} assets in {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
