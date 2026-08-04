# Smart Search for Anki — Medical v1.0.24

This release adds a safe **Create Copy…** action directly to Smart Search
result menus.

## What changed

- Right-click one result and choose **Create Copy…** to open Anki's native Add
  Cards editor with the note type, fields, tags, media references, and source
  home deck prefilled.
- Unsaved edits in Smart Search's inline editor are saved before the copy
  editor opens.
- After those pending edits are saved, the copy workflow does not otherwise
  modify the source. Nothing new is created until **Add** is clicked; the new
  note and its cards then receive fresh identities and scheduling.
- Filtered-deck results resolve to their normal home deck and stale source IDs
  are rejected safely.
- Multi-template and cloze notes follow Anki's normal behavior and can generate
  their usual sibling cards.

The implementation uses Anki's supported native Add Cards workflow and never
clones raw card records or writes directly to `collection.anki2`.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Smart and Exact remain available when Semantic is unsupported or still
  preparing.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
