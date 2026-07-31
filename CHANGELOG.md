# Changelog

## [1.0.15] — 2026-07-30

### Changed

- Moved sibling position, audio replay, and answer controls into the compact
  preview header so the card body has more room.
- Preview X now dismisses the pane only until the next result selection or
  same-row click; disabling Card preview in Settings remains persistent.
- Search settings now use native macOS combo-box and stepper rendering with
  aligned, font-aware sizing and shorter semantic-status copy.

### Fixed

- Removed the custom compound-control styling that could leave clipped dark
  strips beside Default mode and Result limit on macOS.
- Fixed a managed-close recursion that could leave the Smart Search dialog
  visible after its editor-save cleanup completed.

## [1.0.14] — 2026-07-30

### Added

- Replaced the separate Preview window with a resizable right-hand pane inside
  Smart Search.
- Added rendered **Card** and native **Edit** views, sibling-card navigation,
  and in-window expand, restore, and close controls.
- The native editor is created only when first used and saves through Anki's
  supported note operation.

### Fixed

- Opening one or more results in Anki's Browser no longer closes Smart Search
  or clears its query, results, highlighted row, or checked selection.
- Window close and Escape handling are now separate: Escape still clears a
  query first, while an actual close request closes the dialog normally.

## [1.0.13] — 2026-07-30

### Added

- Added a compact search-icon **Smart** entry immediately after Browse in
  Anki's main toolbar.

### Changed

- Reduced ordinary mouse-wheel movement in search results from about three
  result cards to about one card per notch while preserving native precision
  trackpad scrolling.

## [1.0.12] — 2026-07-30

### Changed

- Card Preview is enabled by default and opens automatically only after the
  user enters or moves through the result list, so ordinary search typing
  keeps its focus.
- Added a simple **Card preview** switch in Search settings.
- The native Preview remains in front and owns keyboard focus; Up/Down still
  moves through Smart Search results.

### Fixed

- Stopped and disposed dialog-owned status work before Qt deletes its widgets,
  preventing the stale `IndexStatusWidget has been deleted` traceback after
  profile or dialog teardown.
- Guarded queued search/rebuild callbacks after a dialog closes.
- Preview creation is queued outside result-selection reentrancy and cancelled
  safely when the dialog, profile, or preference changes.
- On macOS, Ctrl+Shift+P now uses the physical Control key instead of
  colliding with Anki's Command+Shift+P profile switcher.

## [1.0.11] — 2026-07-30

### Added

- Native Anki card Preview for the highlighted search result, including the
  rendered template, cloze behavior, media, MathJax, audio, flags, and theme.
- Up/Down navigation through search results while Preview is open.
- Exact-scope sibling-card navigation for results that represent more than one
  card, with a compact “Card 1 of N” window title.
- A Preview toolbar button and Ctrl+Shift+P shortcut.

### Reliability

- Preview follows the highlighted row without changing independent bulk-action
  checkboxes.
- Preview closes with the search dialog, tolerates cards deleted after a
  search, refreshes after card actions, and uses separate saved window geometry
  from Anki's Browser Preview.

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
- Palette-aware result cards, compact live flag/suspension indicators, a clear
  Semantic setup surface, and polished Settings/About screens.
- Automatic targeted refresh after ordinary note additions, edits, and deletes.
- Settings and About surfaces with privacy, support, MedBrevia, and creator
  details.

### Performance and reliability

- Search, external-index work, spelling-vocabulary construction, and Semantic
  inference are kept off Anki's graphical interface thread.
- Stale search requests are cancelled so mode switches and newer queries are
  not overwritten by older results.
- Smart and Exact remain usable while the separate Semantic index is preparing.
- Drug-name suggestions use concise single-word names while full product names
  remain searchable, and automatic typo corrections now continue through
  generic/brand alias expansion.
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
