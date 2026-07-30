# Public release checklist

This is the release gate for Smart Search for Anki — Medical. It is intentionally
stricter than the local development build. Check an item only when its evidence
has been recorded for the exact archive being published.

## 1. Scope and identity

- [ ] Choose the release channel: private beta, public beta, or stable.
- [ ] Confirm the public title and version match `manifest.json`.
- [ ] Confirm the supported Anki version range reflects versions actually
      tested, not versions merely expected to work.
- [ ] State prominently that Semantic Search supports **macOS 14 or later on
      Apple-silicon Macs only**.
- [ ] State that Smart and Exact remain usable while the separate Semantic index
      is preparing.
- [ ] Confirm the creator credit reads **Saleh Mostafa** and the mobile-app link
      is `https://medbrevia.com/app`.

## 2. Privacy-safe, minimal archive

- [ ] Build from an explicit allowlist of distributable source and data files.
- [ ] Include only the documented seed/readme content under `user_files/`.
- [ ] Hard-fail the build if it finds profile names, card/note text, search
      databases, vector indexes, logs, crash dumps, caches, or temporary files.
- [ ] Reject at minimum: `user_files/profiles/`, `*.sqlite*`, `*.db`, `*.npy`,
      `*.npz`, `*.log`, `*.tmp`, `.DS_Store`, `__pycache__/`, and `*.pyc`.
- [ ] Do not bundle both wheel archives and an expanded copy of the same runtime.
- [ ] Confirm the archive contains no tests, development scripts, source-control
      metadata, local settings, or secrets.
- [ ] Confirm there is no enclosing top-level directory inside the
      `.ankiaddon` archive.
- [ ] Record the final byte size and SHA-256 digest.
- [ ] Run an independent archive listing and inspect every `user_files/` entry.

Suggested evidence commands:

```sh
unzip -Z1 dist/*.ankiaddon | sort > release/archive-contents.txt
rg -n '(^|/)(profiles?|__pycache__)(/|$)|\.(sqlite|sqlite3|db|npy|npz|log|tmp|pyc)$|\.DS_Store$' \
  release/archive-contents.txt
shasum -a 256 dist/*.ankiaddon
```

The `rg` command must return no matches. Keep `archive-contents.txt` as local
release evidence; do not publish it if it reveals internal filenames that are
not part of the public package.

## 3. Licensing, attribution, and network disclosure

- [ ] Include the add-on license.
- [ ] Include complete third-party notices for every shipped library, model,
      tokenizer, terminology set, font, and image.
- [ ] Record the exact RxTerms source date/version and include the required NLM
      attribution without implying NLM endorsement.
- [ ] Record the exact model repository, model revision, upstream license, and
      conversion provenance.
- [ ] Verify that every optional download uses HTTPS, a pinned source, and an
      expected SHA-256 digest.
- [ ] Confirm the Privacy page accurately lists every network request.
- [ ] Confirm no telemetry, analytics, collection upload, remote query, or
      automatic model installation was introduced.
- [ ] Review the final notices for license text, copyright lines, attribution
      requirements, and modification disclosures.

## 4. Static quality gates

- [ ] Compile every Python source in the public allowlist.
- [ ] Run the complete unit and offscreen UI test suites.
- [ ] Validate `manifest.json`, configuration defaults, and archive CRCs.
- [ ] Scan distributable files for absolute home paths, emails other than the
      intended support address, API keys, tokens, cookies, and profile data.
- [ ] Confirm normal UI strings do not expose model/runtime implementation names.
- [ ] Confirm every external link opens only after explicit user activation.
- [ ] Confirm the About page, Privacy text, Support text, Known Limitations, and
      changelog all describe the same release.

## 5. Listing and public project materials

- [ ] Proofread `ANKIWEB_LISTING.md` against the exact release.
- [ ] Replace all placeholders and remove publication notes before pasting.
- [ ] Render screenshots from `render_screenshots.py`.
- [ ] Inspect every screenshot at full resolution for clipping, inaccurate UI,
      patient information, profile names, decks, tags, or card text from a real
      collection.
- [ ] Replace mockups with clean-install captures if the shipped interface
      differs materially.
- [ ] Publish Support, Privacy, Known Limitations, and third-party notices where
      users can reach them without installing the add-on.
- [ ] Enable the prepared bug and feature-request issue forms.
- [ ] Ensure the public repository does not contain generated profile indexes or
      expanded runtimes.

## 6. Distribution-path validation — planned, not yet executed

Do not begin this section until the release candidate above is frozen.

### A. Isolated clean-install matrix

- [ ] Create a new local OS user or disposable test environment.
- [ ] Install the exact supported Anki release with no existing add-ons.
- [ ] Create a synthetic profile containing only generated, non-personal notes.
- [ ] Install the release candidate through the same route users will use:
      first as a local `.ankiaddon`, then using AnkiWeb's assigned numeric code
      after the private/unlisted upload exists.
- [ ] Restart Anki and confirm no duplicate add-on folders or startup warnings.
- [ ] Verify Smart and Exact before enabling Semantic.
- [ ] Enable Semantic explicitly; verify download progress, digest validation,
      cancellation, retry, preparation progress, and search.
- [ ] While Semantic indexes, switch modes repeatedly and confirm Smart and Exact
      stay responsive.
- [ ] Exercise selection, Browser opening, flags, suspend/unsuspend, tags, Undo,
      profile switching, sync, import, and Anki shutdown during idle work.
- [ ] Confirm uninstall removes the add-on but does not damage the collection.

### B. Upgrade and rollback

- [ ] Install the oldest version users could reasonably have.
- [ ] Create synthetic indexes and non-default settings.
- [ ] Upgrade through the public update mechanism to the release candidate.
- [ ] Confirm intended `user_files` survive, stale generated assets are migrated
      or safely rebuilt, and no duplicate menu item appears.
- [ ] Confirm a failed/cancelled Semantic upgrade leaves Smart and Exact usable.
- [ ] Confirm the prior release can be restored without touching
      `collection.anki2`.

### C. Compatibility matrix

Record Anki version, OS version, architecture, install path, pass/fail, and any
waiver. At minimum:

| Anki | OS / architecture | Smart | Exact | Semantic | Bulk actions | Status |
|---|---|---:|---:|---:|---:|---|
| Declared minimum | macOS 14 / Apple silicon | — | — | — | — | Not run |
| Declared maximum | macOS / Apple silicon | — | — | — | — | Not run |
| Declared minimum | Windows 11 / x86-64 | — | — | Unsupported | — | Not run |
| Declared minimum | Linux / x86-64 | — | — | Unsupported | — | Not run |

Semantic is expected to be unsupported on Windows, Linux, and Intel Mac for this
release; the required pass is that this state is graceful and Smart/Exact remain
fully usable.

### D. AnkiWeb staging and final publication

- [ ] Upload the frozen archive as an unlisted/private staging item if AnkiWeb
      permits the intended staging workflow.
- [ ] Record the assigned numeric add-on code.
- [ ] Repeat clean install and upgrade tests using that numeric code.
- [ ] Verify the listing, images, links, formatting, version range, and support
      contact in AnkiWeb's rendered page.
- [ ] Obtain explicit approval for the public listing.
- [ ] Make the listing public.
- [ ] Install once more from the public code and compare its archive checksum to
      the approved release candidate when the service permits that comparison.
- [ ] Monitor the support channel for installation or compatibility failures
      during the first release window.

No item in section 6 has been executed merely because this checklist exists.

## 7. Release record

Complete this block for the exact artifact:

```text
Version:
Git commit:
Build command:
Build machine:
Artifact:
Bytes:
SHA-256:
Anki minimum:
Anki maximum:
Test summary:
Screenshot SHA-256 file:
Reviewer:
Approval date:
AnkiWeb code:
Published URL:
```
