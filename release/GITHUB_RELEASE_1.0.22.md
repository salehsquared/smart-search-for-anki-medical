# Smart Search for Anki — Medical v1.0.22

This release makes repeated Semantic searches substantially faster, adds
reviewer-style keyboard controls to the inline card preview, and keeps search
results stable after card actions.

## What changed

- Press **Space** or **Right Arrow** to reveal the selected card's answer and
  **Left Arrow** to return to the front. These shortcuts work from the results
  list and after clicking inside the rendered card preview.
- Use **Shift+Space** to check or uncheck a result for bulk actions. Up and Down
  continue moving through results.
- Repeated Semantic searches are now much faster because the isolated local
  model stays warm during an active search session.
- Superseded typing discards stale results without repeatedly restarting the
  Semantic model.
- Flagging, suspending, unsuspending, and tagging update visible rows in place
  without reloading the result list. Press **Enter** or search again to reapply
  filters such as `is:suspended`, `flag:`, or `tag:`.

The Semantic worker still unloads immediately when Search closes, reviewing
begins, the profile closes, or Anki exits. Search, indexing, and inference stay
on the user's computer.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Smart and Exact remain available when Semantic is unsupported or still
  preparing.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
