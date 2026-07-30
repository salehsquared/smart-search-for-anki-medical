# Contributing

Thank you for helping improve Smart Search for Anki — Medical.

## Before opening an issue

- Use synthetic or fully redacted examples.
- Never attach an Anki collection, profile folder, index database, vector file,
  credentials, patient information, or identifiable card content.
- For a bug, include the Smart Search version, Anki version, operating system,
  processor architecture, search mode, and numbered reproduction steps.
- Send suspected security or privacy problems privately as described in
  [SECURITY.md](SECURITY.md).

## Development

The add-on is deliberately dependency-light for Smart and Exact search.
Semantic Search uses the reviewed wheels under `vendor_wheels/` on its supported
platform.

```sh
python3 -m pip install -r scripts/requirements-test.txt
python3 -m unittest discover -s tests -t . -p 'test_*.py' -v
python3 scripts/build_addon.py
```

The public builder uses an explicit allowlist and rejects profile databases,
generated indexes, credentials, expanded runtimes, downloaded models, and
unexpected binary assets. A pull request that adds a network endpoint, binary,
model, tokenizer, terminology source, or dependency must also add:

- an immutable source revision;
- a SHA-256 integrity check;
- the applicable license and notices;
- a reproducible generation record when the artifact is transformed; and
- an accurate update to the privacy and data-source documentation.

## Collection safety

- Do not edit `collection.anki2` directly.
- Use Anki's supported collection operations for every mutation.
- Keep expensive indexing and inference work off the graphical interface
  thread.
- Smart and Exact must remain usable when Semantic Search is absent,
  unsupported, preparing, or damaged.
- Add regression tests for card-vs-note scope, sibling cards, undo behavior,
  stale results, profile switching, and cancellation when relevant.

## Pull requests

Keep changes focused and list the exact validation commands run. UI screenshots
must use synthetic content and must not reveal a real profile, deck, tag, or
card. Distribution-path compatibility claims require the isolated testing
matrix in `release/RELEASE_CHECKLIST.md`; expected compatibility alone is not
evidence.
