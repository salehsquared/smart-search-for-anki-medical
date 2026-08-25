Install from AnkiWeb with code **677438639**.

Smart Search for Anki — Medical is a keyboard-first search palette for large
medical collections. Press **Command-K on macOS** or **Ctrl-K on
Windows/Linux** and start typing.

![Smart Search results](https://raw.githubusercontent.com/salehsquared/smart-search-for-anki-medical/v1.0.35/release/assets/screenshots/01-smart-search.png)

- **Smart:** case-insensitive search with typo recovery and common medication
  aliases.
- **Exact:** Anki's native Browser-search syntax, including phrases, Boolean
  operators, fields, wildcards, and filters.
- **Semantic:** local clinical-meaning search after an explicit, one-time
  setup.

Use the searchable deck picker to choose one or several decks, or select a
parent while excluding individual subdeck branches. One regular deck can be
saved as the profile-specific default for new Smart Search windows. Native
`deck:`, `tag:`, `note:`, `is:`, `flag:`, `prop:`, `rated:`, field, wildcard,
and Boolean filters are delegated to Anki.

Search results can be previewed or edited in place, opened in Anki's Browser,
selected in ranges, and acted on with safe flag, suspension, burial, deck,
tag, copy, and Undo workflows.

## New in version 1.0.35

- Smart Search now stops collection access before Anki starts collection or
  media sync. Queued work rechecks that safety state before it can run.
- If an add-on collection call receives a PyO3-wrapped Anki backend panic,
  Smart Search stops all later collection work for that Anki process and asks
  for a restart instead of calling the damaged backend again.
- Native Semantic model and vector work now runs only in the disposable helper
  process, outside Anki.
- A compact emerald button in Anki's Browser sends the visible Browser query
  to Smart Search. Right-click the Smart Search query field and select
  **Search in Anki Browser** to send the query back.
- The deck picker can save or clear one profile-specific default deck. A saved
  default applies only to a fresh, blank Smart Search window.
- A compact **Suspended only** control safely adds or removes
  `is:suspended`. Complex native queries remain unchanged.

## Compatibility

Version 1.0.35 supports **Anki Desktop 24.11 through 26.08**, including the
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
