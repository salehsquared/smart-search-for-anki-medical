# Smart Search for Anki — Medical v1.0.21

This release fixes the large RAM increase that could remain after Semantic
Search had finished, while preserving Smart, Exact, Semantic, filtering,
preview, editing, and collection actions.

## What changed

- Native Semantic inference now runs in a disposable local worker instead of
  loading ONNX Runtime, Tokenizers, and the model into Anki itself.
- The worker starts only for Semantic work and exits afterward, allowing macOS
  to reclaim its model, native libraries, and allocator caches.
- Entering review, closing the profile, repairing the runtime, or canceling
  Semantic work also stops and reaps the worker.
- Anki retains only a NumPy-based vector-ranking layer. Searches scan vectors
  in bounded 512-note chunks to limit allocator high-water memory.
- Worker inference is constrained to one CPU thread, one sequence at a time,
  16 KiB per input, bounded local messages, and a 256 MiB macOS process-memory
  ceiling.

## Why this was necessary

The previous implementation could release its Python model object but could
not force native inference libraries and allocator caches to leave Anki's
process. Once loaded, that memory could remain mapped during ordinary browsing
and review. Process isolation gives that memory a definitive lifetime: when
the helper exits, the operating system reclaims it.

## Measured behavior

The release candidate was exercised with a copied, disposable 40,666-note,
384-dimension Semantic index and 25 fresh worker launches using 16 KiB inputs:

- Worst host-process vector-search growth: **17.40 MiB**, equivalent to
  **6.35%** of the measured 274.03 MiB Anki baseline.
- Final retained host growth after 25 cycles: **12.46 MiB (4.55%)**.
- Worker physical footprint: **190.50 MiB** on the cold launch and
  **182.77 MiB** steady-state median.
- Worker idle CPU: **0.00%**; median/max exit time: **20/78 ms**.
- All 25 worker processes exited, no orphan remained, and the steady footprint
  trend was effectively flat.

Active Semantic inference still temporarily uses additional CPU and memory;
the improvement is that this work is bounded, kept outside Anki, and reclaimed
when it finishes.

## Upgrade notes

- Existing downloaded model files and profile indexes are reused.
- The first launch after updating performs a one-time local runtime migration
  to the isolated worker. It does not change cards, scheduling, or review data.
- The bundled standalone runtime makes the add-on download larger than earlier
  releases.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Windows, Linux, and Intel Mac integration testing is not part of this
  public-beta support claim; Smart and Exact remain available when Semantic is
  unsupported.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
