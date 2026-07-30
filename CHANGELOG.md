# Changelog

## [1.0.10] — 2026-07-29

### Added

- Keyboard-first search palette opened with Command/Ctrl-K.
- Smart mode with case-insensitive matching, medical typo recovery, and RxTerms
  generic/brand aliases.
- Exact mode with literal matching and native Anki search syntax.
- Optional, local Semantic mode with a separate setup and indexing lifecycle.
- Card-aware handling of native deck, tag, note, flag, suspension, property,
  review-history, field, wildcard, and Boolean filters.
- Adaptive relevance cutoffs that hide weak trailing matches.
- Match explanations, correction chips, filter chips, and note-grouped previews.
- Multi-select controls with checkboxes, Shift-click ranges, All shown, None,
  Invert, and Command/Ctrl-Return.
- Browser opening for the exact selected card set.
- Undoable card flags, suspend/unsuspend actions, and note tags from the toolbar
  or result context menu.
- Compact visual indicators for live flag and suspension state.
- Automatic targeted refresh after ordinary note additions, edits, and deletes.
- Settings and About surfaces with privacy, support, MedBrevia, and creator
  details.

### Performance and reliability

- Search, external-index work, spelling-vocabulary construction, and Semantic
  inference are kept off Anki's graphical interface thread.
- Stale search requests are cancelled so mode switches and newer queries are
  not overwritten by older results.
- Smart and Exact remain usable while the separate Semantic index is preparing.
- Expensive refreshes are coalesced and delayed until edit activity settles.
- Profile-scoped search data is disposable and can be rebuilt without changing
  `collection.anki2`.

### Privacy and distribution

- Searches, card contents, and indexes remain local.
- Semantic setup requires an explicit user action before any model download.
- Replaced the earlier third-party model conversion with a reproducible,
  provenance-complete ONNX INT8 export pinned to an immutable public revision.
- Added complete RxTerms attribution, model lineage, wheel licenses, and a
  deterministic 120-component Tokenizers notice bundle.
- Public builds exclude profile-derived indexes, logs, caches, and expanded
  development/runtime artifacts.
- Added complete release-facing privacy, support, known-limitations, and
  third-party attribution materials.

### Compatibility

- Smart and Exact: supported on the Anki Desktop versions listed on the release
  page.
- Semantic Search: macOS 14 or later on Apple-silicon Macs only.

### Known limitations

- Local indexes do not sync to other computers.
- Semantic ranking is approximate and may miss or mis-rank relevant cards.
- Sync, imports, native bulk edits, undo/redo, and structural collection changes
  may schedule a later full reconciliation.
