Install from AnkiWeb with code **677438639**.

Smart Search for Anki — Medical is a keyboard-first search palette for large
medical collections. Press **Command-K on macOS** or **Ctrl-K on
Windows/Linux** and start typing.

![Smart Search results](https://raw.githubusercontent.com/salehsquared/smart-search-for-anki-medical/v1.0.25/release/assets/screenshots/01-smart-search.png)

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

## New in version 1.0.25

- The first result's rendered card opens automatically after each normal or
  Related search while the query field remains ready for the next search.
- Result rows now prioritize the primary card text, add a short `Extra` excerpt
  only when that field actually exists, and keep deck/note-type details quiet.
  Other fields and tags remain searchable without cluttering the row.
- **Find Related Cards** finds notes sharing the same complete UWorld or AMBOSS
  source tag, explains the match, and can return to the original results
  without rerunning the search.
- Smart and Semantic native filters now narrow eligible cards without changing
  relevance or order for the cards that remain.
- Exact mode now describes Anki's standard rules in the interface: separate
  terms are ANDed, quoted words stay together, `OR` matches either side, and
  `w:` requests a whole-word match.
- Successful collection actions expose a guarded one-click Undo, and Settings
  can choose whether previews begin on the question, answer, or editor.

## Compatibility

Version 1.0.25 supports **Anki Desktop 24.11 through 26.08**, including the
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
