# Public release checklist

This is the release gate for Smart Search for Anki — Medical. It is intentionally
stricter than the local development build. Check an item only when its evidence
has been recorded for the exact archive being published.

## 1. Scope and identity

- [x] Choose the release channel: private beta, public beta, or stable.
- [x] Confirm the public title and version match `manifest.json`.
- [x] Confirm the supported Anki version range reflects versions actually
      tested, not versions merely expected to work.
- [x] State prominently that Semantic Search supports **macOS 14 or later on
      Apple-silicon Macs only**.
- [x] State that Smart and Exact remain usable while the separate Semantic index
      is preparing.
- [x] Confirm the creator credit reads **Saleh Mostafa** and the mobile-app link
      is `https://medbrevia.com/app`.

## 2. Privacy-safe, minimal archive

- [x] Build from an explicit allowlist of distributable source and data files.
- [x] Include only the documented seed/readme content under `user_files/`.
- [x] Hard-fail the build if it finds profile names, card/note text, search
      databases, vector indexes, logs, crash dumps, caches, or temporary files.
- [x] Reject at minimum: `user_files/profiles/`, `*.sqlite*`, `*.db`, `*.npy`,
      `*.npz`, `*.log`, `*.tmp`, `.DS_Store`, `__pycache__/`, and `*.pyc`.
- [x] Do not bundle both wheel archives and an expanded copy of the same runtime.
- [x] Confirm the archive contains no tests, development scripts, source-control
      metadata, local settings, or secrets.
- [x] Confirm there is no enclosing top-level directory inside the
      `.ankiaddon` archive.
- [x] Record the final byte size and SHA-256 digest.
- [x] Run an independent archive listing and inspect every `user_files/` entry.

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

- [x] Include the add-on license.
- [x] Include complete third-party notices for every shipped library, model,
      tokenizer, terminology set, font, and image.
- [x] Record the exact RxTerms source date/version and include the required NLM
      attribution without implying NLM endorsement.
- [x] Record the exact model repository, model revision, upstream license, and
      conversion provenance.
- [x] Verify that every optional download uses HTTPS, a pinned source, and an
      expected SHA-256 digest.
- [x] Confirm the Privacy page accurately lists every network request.
- [x] Confirm no telemetry, analytics, collection upload, remote query, or
      automatic model installation was introduced.
- [x] Review the final notices for license text, copyright lines, attribution
      requirements, and modification disclosures.

## 4. Static quality gates

- [x] Compile every Python source in the public allowlist.
- [x] Run the complete unit and offscreen UI test suites.
- [x] Validate `manifest.json`, configuration defaults, and archive CRCs.
- [x] Scan distributable files for absolute home paths, emails other than the
      intended support address, API keys, tokens, cookies, and profile data.
- [x] Confirm normal UI strings do not expose model/runtime implementation names.
- [x] Confirm every external link opens only after explicit user activation.
- [x] Confirm the About page, Privacy text, Support text, Known Limitations, and
      changelog all describe the same release.

## 5. Listing and public project materials

- [x] Proofread `ANKIWEB_LISTING.md` against the exact release.
- [x] Replace all placeholders and remove publication notes before pasting.
- [x] Render screenshots from `render_screenshots.py`.
- [x] Inspect every screenshot at full resolution for clipping, inaccurate UI,
      patient information, profile names, decks, tags, or card text from a real
      collection.
- [x] Replace mockups with clean-install captures if the shipped interface
      differs materially.
- [x] Publish Support, Privacy, Known Limitations, and third-party notices where
      users can reach them without installing the add-on.
- [x] Enable the prepared bug and feature-request issue forms.
- [x] Ensure the public repository does not contain generated profile indexes or
      expanded runtimes.

## 6. Distribution-path validation — refreshed 2026-08-01

Current compatibility and native-upgrade evidence is in
`RELEASE_RECORD_1.0.19.md`; the original public-listing evidence remains in
`RELEASE_RECORD_1.0.15.md`. The v1.0.18 candidate was never published and is
superseded. The v1.0.19 AnkiWeb-code checks remain pending until upload.

### A. Isolated clean-install matrix

- [x] Create a new local OS user or disposable test environment.
- [x] Install the exact supported Anki release with no existing add-ons.
- [x] Create a synthetic profile containing only generated, non-personal notes.
- [ ] Install the release candidate through the same route users will use:
      first as a local `.ankiaddon`, then using AnkiWeb's assigned numeric code
      after the private/unlisted upload exists. The local `.ankiaddon` path
      passed; the real assigned-code path is the post-upload gate.
- [x] Restart Anki and confirm no duplicate add-on folders or startup warnings.
- [x] Verify Smart and Exact before enabling Semantic.
- [ ] Enable Semantic explicitly; verify download progress, digest validation,
      cancellation, retry, preparation progress, and search. Download, digest,
      preparation, and live search passed; cancellation/repair are covered by
      automated tests rather than a second destructive live setup.
- [x] While Semantic indexes, switch modes repeatedly and confirm Smart and Exact
      stay responsive.
- [ ] Exercise selection, Browser opening, flags, suspend/unsuspend, tags, Undo,
      profile switching, sync, import, and Anki shutdown during idle work.
      Selection, Browser invocation, inline editing, and shutdown passed live;
      mutation and lifecycle combinations are covered by the offscreen suite.
- [ ] Confirm uninstall removes the add-on but does not damage the collection.

### B. Upgrade and rollback

- [x] Install the oldest version users could reasonably have.
- [x] Create synthetic indexes and non-default settings.
- [x] Upgrade through Anki's native `AddonManager.install()` mechanism to the
      release candidate on every supported version.
- [x] Confirm intended `user_files` survive, stale generated assets are migrated
      or safely rebuilt, and no duplicate menu item appears.
- [ ] Confirm a failed/cancelled Semantic upgrade leaves Smart and Exact usable.
- [x] Confirm the prior release can be restored without touching
      `collection.anki2`.

### C. Compatibility matrix

Record Anki version, OS version, architecture, install path, pass/fail, and any
waiver. At minimum:

| Anki | OS / architecture | Smart | Exact | Semantic | Bulk actions | Status |
|---|---|---:|---:|---:|---:|---|
| 24.11 | macOS / Apple silicon | Pass | Pass | Pass | Automated | Pass |
| 25.02.7 | macOS / Apple silicon | Pass | Pass | Pass | Automated | Pass |
| 25.07.5 | macOS / Apple silicon | Pass | Pass | Pass | Automated | Pass |
| 25.09.4 | macOS / Apple silicon | Pass | Pass | Pass | Automated | Pass |
| 25.09.5 | macOS / Apple silicon | Pass | Pass | Pass | Automated | Pass |
| 26.05 | macOS / Apple silicon | Pass | Pass | Pass | Automated + selection smoke | Pass |
| 26.08 | macOS / Apple silicon | Pass | Pass | Pass | Automated | Pass |
| 26.08 | Windows 11 / x86-64 | Not claimed | Not claimed | Unsupported | Not claimed | Out of beta scope |
| 26.08 | Linux / x86-64 | Not claimed | Not claimed | Unsupported | Not claimed | Out of beta scope |
| 26.08 | Intel Mac | Not claimed | Not claimed | Unsupported | Not claimed | Out of beta scope |

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

The unchecked AnkiWeb items are intentionally the only publication-boundary
steps left. Other unchecked compound stress cases are explicitly disclosed in
the release record and are not part of the public beta support claim.

## 7. Release record

The v1.0.19 artifact, hashes, compatibility evidence, and remaining AnkiWeb
fields are recorded in `RELEASE_RECORD_1.0.19.md`. The original AnkiWeb
publication remains documented in `RELEASE_RECORD_1.0.15.md`; v1.0.18 is an
unpublished, superseded candidate retained only for audit history.
