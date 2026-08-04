Install from AnkiWeb with code **677438639**.

Smart Search for Anki — Medical is a keyboard-first search palette for large
medical collections. Press **Command-K on macOS** or **Ctrl-K on
Windows/Linux** and start typing.

![Smart Search results](https://raw.githubusercontent.com/salehsquared/smart-search-for-anki-medical/v1.0.24/release/assets/screenshots/01-smart-search.png)

- **Smart:** case-insensitive search with typo recovery and common medication
  aliases.
- **Exact:** literal search with Anki's native search syntax.
- **Semantic:** local clinical-meaning search after an explicit, one-time
  setup.

Use the searchable deck picker to choose one or several decks, or select a
parent while excluding individual subdeck branches. Native `deck:`, `tag:`,
`note:`, `is:`, `flag:`, `prop:`, `rated:`, field, wildcard, and Boolean
filters are delegated to Anki.

Search results can be previewed or edited in place, opened in Anki's Browser,
selected in ranges, and acted on with safe flag, suspension, burial, deck,
tag, and copy workflows.

## New in version 1.0.24

- Right-click one result and choose **Create Copy…** to open Anki's native Add
  Cards editor prefilled with that note's content, tags, media references, note
  type, and home deck.
- Inline edits are saved first. The copy workflow does not otherwise modify the
  source, and nothing new is created until **Add** is clicked. New notes and
  cards start with fresh identities and scheduling.
- Filtered-deck results return to their normal home deck, and stale results
  fail safely.
- Multi-template and cloze notes follow Anki's normal behavior and may generate
  their usual sibling cards.

## Compatibility

Version 1.0.24 supports **Anki Desktop 24.11 through 26.08**, including the
25.02, 25.07, 25.09, 26.05, and 26.08 release families. The compatibility
matrix was tested on macOS with Apple silicon.

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
