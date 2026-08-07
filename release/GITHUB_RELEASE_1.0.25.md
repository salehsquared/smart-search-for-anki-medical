# Smart Search for Anki — Medical v1.0.25

This release makes search results easier to scan and puts the first rendered
card in view automatically.

## What changed

- The first result's card preview opens automatically after normal and Related
  searches while the query field keeps focus, so another search can be typed
  immediately.
- Result rows now emphasize the primary card text, show only a bounded `Extra`
  excerpt when one exists, and move deck/note-type details into a quieter line.
  Hidden fields and tags remain fully searchable.
- **Find Related Cards** finds notes sharing the same complete UWorld or AMBOSS
  source tag and explains the exact relationship. **Back to search** restores
  the previous result view without rerunning it.
- Native filters in Smart and Semantic modes now narrow eligible cards without
  changing the relevance scores or ordering of cards that remain.
- Exact mode now explains Anki's native Browser-search behavior directly in the
  interface, including separate terms, quoted phrases, `OR`, and `w:`.
- Card/note actions expose a guarded one-click Undo, and Search Settings can
  choose whether newly opened previews begin on the question, answer, or editor.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Smart and Exact remain available when Semantic is unsupported or still
  preparing.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
