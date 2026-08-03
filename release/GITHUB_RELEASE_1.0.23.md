# Smart Search for Anki — Medical v1.0.23

This release adds safe, undoable deck moves and bury controls directly to
Smart Search result menus.

## What changed

- Right-click one result—or check several results—and choose **Change Deck…**
  to move the exact selected cards.
- The destination picker is searchable, keyboard-friendly, and uses the same
  clear deck hierarchy as Smart Search's deck filter. It offers one exact
  destination and excludes filtered decks.
- Right-click selected cards to **Bury** or **Unbury** them. Mixed selections
  show the applicable actions and keep suspension separate from burial.
- Every move, bury, and unbury action uses Anki's supported collection APIs and
  creates one native Undo step.
- Fixed timestamp-sized Anki deck IDs being truncated by a 32-bit UI signal,
  which could make an existing destination appear unavailable.
- Fixed Return after filtering the destination picker selecting a visible
  parent instead of the matching nested deck. Failed refreshes now retain the
  last usable deck list.

These actions update the current results in place. Search is rerun only when
the user requests it, while a successful deck move schedules a targeted index
refresh for the affected notes.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Smart and Exact remain available when Semantic is unsupported or still
  preparing.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
