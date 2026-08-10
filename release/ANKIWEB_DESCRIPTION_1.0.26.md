Install from AnkiWeb with code **677438639**.

Smart Search for Anki — Medical is a keyboard-first search palette for large
medical collections. Press **Command-K on macOS** or **Ctrl-K on
Windows/Linux** and start typing.

![Smart Search results](https://raw.githubusercontent.com/salehsquared/smart-search-for-anki-medical/v1.0.26/release/assets/screenshots/01-smart-search.png)

- **Smart:** case-insensitive search with typo recovery and common medication
  aliases.
- **Exact:** Anki's native Browser-search syntax, including phrases, Boolean
  operators, fields, wildcards, and filters.
- **Semantic:** local clinical-meaning search after an explicit, one-time
  setup.

Use the searchable deck picker to choose one or several decks, or select a
parent while excluding individual subdeck branches. Native `deck:`, `tag:`,
`note:`, `is:`, `flag:`, `prop:`, `rated:`, field, wildcard, and Boolean
filters are delegated to Anki.

Search results can be previewed or edited in place, opened in Anki's Browser,
selected in ranges, and acted on with safe flag, suspension, burial, deck,
tag, copy, and Undo workflows.

## New in version 1.0.26

- An explicitly submitted Semantic search now works while Anki is in review
  mode. Reviewing still pauses background indexing, reconciliation, and model
  preparation.
- Semantic searches now leave `Searching…` through results, a clear
  retryable error, or a 45-second end-to-end timeout, including delays before
  the isolated helper starts.
- A stopped or crashed helper now produces a retryable error, while model or
  index runtime failures show repair guidance. Neither path silently returns
  Exact results or an empty successful response.
- Newer queries supersede older work, and a timed-out helper is reset so the
  next query starts cleanly.
- Smart and Semantic matches render as soon as retrieval finishes; best-effort
  refreshes of live flags, suspension, burial, and sibling IDs can no longer
  hold the result list.
- The isolated helper may remain warm for up to 90 seconds between adjacent
  searches. Closing the search window reaps it immediately; after switching
  modes, the existing idle timer unloads it.

## Compatibility

Version 1.0.26 supports **Anki Desktop 24.11 through 26.08**, including the
25.02, 25.07, 25.09, 26.05, and 26.08 release families. The compatibility
matrix is tested on macOS with Apple silicon.

Semantic Search requires **macOS 14 or later on Apple silicon**. Smart and
Exact remain available when Semantic is unsupported, not prepared, or still
building its separate index. Windows, Linux, and Intel Mac integration testing
is not part of this public-beta support claim.

## Privacy and safety

Searches, card text, and indexes stay on the computer. There is no analytics or
telemetry. Semantic files download only after the user explicitly starts
setup. Card changes use Anki's supported operations; the add-on never edits
`collection.anki2` directly.

Created by **Saleh Mostafa** with **MedBrevia**.

- Project and support: https://github.com/salehsquared/smart-search-for-anki-medical
- MedBrevia mobile app: https://medbrevia.com/app
- Feedback: product@medbrevia.com

Smart Search is an independent add-on and is not affiliated with or endorsed
by Anki or AnkiWeb.
