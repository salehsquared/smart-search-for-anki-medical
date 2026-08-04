# Smart Search settings

- `shortcut`: global shortcut used from Anki's main window. Qt maps `Ctrl+K` to Command-K on macOS.
- `result_limit`: maximum notes shown for one search.
- `preview_enabled`: automatically open the inline card preview while browsing results.
- `preview_default`: initial preview surface for each newly selected card: `question`, `answer`, or `edit`.
- `debounce_ms`: pause before a search begins.
- `auto_reconcile`: refresh the disposable index after collection-changing operations.
- `semantic_enabled`: make local semantic search available on supported computers.
- `auto_semantic_index`: automatically build or resume the profile's semantic index when the local model is already installed. This never downloads or installs missing model files.
- `semantic_autostart_delay_ms`: delay automatic semantic indexing long enough for the text index and startup reconciliation to finish. Default: 20 seconds.

The add-on never adds tables to or writes directly into Anki's collection database.
