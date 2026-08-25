# Smart Search for Anki — Medical v1.0.35

This release makes Smart Search safer around Anki sync and adds direct,
two-way query handoff between Smart Search and Anki's Browser.

## What changed

- Smart Search now closes its collection-access gate before Anki starts a
  collection or media sync. Queued work rechecks that gate before it can touch
  the collection.
- A PyO3-wrapped Anki backend panic now stops Smart Search collection work for
  the rest of that Anki process and shows one restart-required error. It does
  not keep calling a poisoned backend.
- All native Semantic model and vector work now runs in the disposable helper
  process, outside Anki.
- Anki's Browser search field now has a compact emerald magnifier button that
  sends the visible Browser query to Smart Search.
- Right-clicking the Smart Search query field keeps the standard text menu and
  adds **Search in Anki Browser**.
- The deck picker can save one profile-specific default deck, with compact
  **Set as default deck** and **Clear default** controls.
- A compact **Suspended only** control safely adds or removes
  `is:suspended` without rewriting complex native queries.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Smart and Exact remain available when Semantic is unsupported or still
  preparing.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
