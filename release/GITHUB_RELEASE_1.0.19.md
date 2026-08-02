# Smart Search for Anki — Medical v1.0.19

This public-beta release expands support from the original Anki 26.05 build to
**Anki Desktop 24.11 through 26.08** and adds a faster way to scope searches and
receive future updates.

## Highlights

- Added a searchable hierarchical deck picker with **All decks**, **Current
  deck**, multi-selection, and keyboard navigation.
- Parent decks can keep individual subdeck branches excluded, with the exact
  scope shown as native `deck:` search syntax.
- Added a simple **Check & Update** button to About for AnkiWeb-installed
  copies. It delegates to Anki's native compatibility-aware updater.
- Update installation pauses deferred indexing and closes profile-owned search
  files before Anki replaces the bundle; successful installs stay quiesced
  until Anki restarts.
- Added tested compatibility for Anki 24.11, 25.02, 25.07, 25.09, 26.05, and
  26.08 on macOS with Apple silicon.
- Added a pinned Python 3.9 Semantic runtime so supported older Anki releases
  retain local Semantic Search instead of falling back to Smart and Exact.

## Update safety

Updates preserve Anki's normal confirmation, compatibility selection,
`user_files`, error handling, and restart workflow. The in-add-on update action
is hidden in named development/manual copies so it cannot create a duplicate
numeric AnkiWeb installation. If Smart Search is actively preparing or
refreshing an index, installation waits until that work finishes.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Windows, Linux, and Intel Mac integration testing is not part of this public
  beta support claim; Smart and Exact remain available when Semantic is
  unsupported.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
