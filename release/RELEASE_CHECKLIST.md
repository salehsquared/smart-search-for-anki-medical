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
- [ ] Regenerate all publication screenshots for v1.0.35. The listing reuses
      the privacy-safe v1.0.15 core-search capture as a generic illustration;
      `release/render_screenshots.py` still contains older About-page copy.
- [x] Inspect the tagged hero at full resolution for clipping, patient
      information, profile names, decks, tags, filesystem paths, or card text
      from a real collection.
- [x] Replace materially inaccurate mockups or record an explicit
      illustration-only waiver. The v1.0.35 listing reuses the privacy-safe
      v1.0.15 core-search capture as a generic illustration, not as current UI
      or compatibility evidence; the waiver is in `RELEASE_RECORD_1.0.35.md`.
- [x] Publish Support, Privacy, Known Limitations, and third-party notices where
      users can reach them without installing the add-on.
- [x] Enable the prepared bug and feature-request issue forms.
- [x] Ensure the public repository does not contain generated profile indexes or
      expanded runtimes.

## 6. Distribution-path validation — v1.0.35 public release

Complete local and public v1.0.35 evidence is in
`RELEASE_RECORD_1.0.35.md`. Historical releases remain in their versioned
release records.

### A. Isolated clean-install matrix

- [x] Create disposable test environments with isolated add-on roots.
- [x] Install the exact supported Anki release with no existing add-ons.
- [ ] Create and open a synthetic Anki profile. The v1.0.35 public harness did
      not instantiate a profile or collection.
- [x] Install the release through the public AnkiWeb numeric code. The live
      code passed disposable clean installs at the Anki 24.11 and 26.08 support
      boundaries; the full automated suite passed under all seven supported
      Anki runtimes.
- [ ] Launch/restart the public package in disposable Anki and confirm no
      startup warnings. Installer-level duplicate-folder checks passed.
- [ ] Verify Smart and Exact live from the exact public package. Their source
      and Anki-runtime automated tests passed.
- [ ] Enable Semantic explicitly; verify download progress, digest validation,
      cancellation, retry, preparation progress, and search. These paths are
      covered by automated tests; the opt-in real-model integration test was
      skipped in CI.
- [ ] While Semantic indexes, switch modes repeatedly and confirm Smart and
      Exact stay responsive in a disposable live profile. Automated coverage
      passed.
- [ ] Submit live Semantic searches during review from the exact public
      package. Automated tests cover reviewer-paused work, timeout, error,
      supersession, and result refresh.
- [ ] Exercise selection, Browser opening, flags, suspend/unsuspend, tags, Undo,
      profile switching, sync, import, and Anki shutdown during idle work in a
      disposable live profile. Mutation and lifecycle combinations are covered
      by the offscreen suite.
- [ ] Confirm uninstall removes the add-on but does not damage the collection.

### B. Upgrade and rollback

- [x] Install the oldest version users could reasonably have.
- [x] Create a synthetic SQLite persistence probe and non-default settings.
- [x] Upgrade through Anki's native `AddonManager.install()` mechanism to the
      release through the public numeric-code path at the 24.11 and 26.08
      support boundaries. Exact v1.0.26-to-v1.0.35 upgrades passed; the
      supported Anki API contracts passed in CI on all seven runtimes.
- [x] Confirm the synthetic `user_files`, non-default configuration, disabled
      state, and custom metadata survive byte-for-byte and no duplicate add-on
      folder appears.
- [ ] Confirm stale generated assets migrate or rebuild and no duplicate menu
      item appears in a launched disposable profile.
- [ ] Confirm a failed/cancelled Semantic upgrade leaves Smart and Exact usable.
- [ ] Confirm the prior release can be restored after upgrading without touching
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
| 26.05 | macOS / Apple silicon | Pass | Pass | Pass | Automated | Pass |
| 26.08 | macOS / Apple silicon | Pass | Pass | Pass | Automated | Pass |
| 26.08 | Windows 11 / x86-64 | Not claimed | Not claimed | Unsupported | Not claimed | Out of beta scope |
| 26.08 | Linux / x86-64 | Not claimed | Not claimed | Unsupported | Not claimed | Out of beta scope |
| 26.08 | Intel Mac | Not claimed | Not claimed | Unsupported | Not claimed | Out of beta scope |

Semantic is expected to be unsupported on Windows, Linux, and Intel Mac for this
release; the required pass is that this state is graceful and Smart/Exact remain
fully usable. In the seven supported-runtime rows, **Pass** records automated
behavior and Anki-API coverage. It is not a live-model end-to-end claim; the
opt-in real-model worker integration test was skipped because
`SMART_SEARCH_REAL_MODEL_DIR` was not set.

### D. AnkiWeb staging and final publication

- [x] Update the existing public item in place with the frozen archive; no
      duplicate staging item or compatibility branch was created.
- [x] Record the assigned numeric add-on code: `677438639`.
- [x] Repeat clean installations and v1.0.26 → v1.0.35 upgrades at the 24.11
      and 26.08 boundaries using that numeric code through Anki's official
      updater.
- [x] Verify the listing formatting and version range, the tagged image, the
      **Contact Author** target, and the privacy text in AnkiWeb's rendered
      page. Bare project/mobile/email strings remain text and the privacy URL is
      not linked; both presentation details are recorded in the release record.
- [x] Obtain explicit approval for the public listing.
- [x] Make the listing public.
- [x] Install once more from the public code and compare all 68 installed
      archive members byte-for-byte with the approved release candidate.
- [ ] Monitor the support channel for installation or compatibility failures
      during the first release window.

The publication-boundary, public-code install, and live-upgrade checks are
complete. First-window support monitoring remains an ongoing post-publication
task. Other unchecked compound stress cases are explicitly disclosed in the
release record and are not part of the public beta support claim.

## 7. Release record

The v1.0.35 artifact, hashes, compatibility evidence, GitHub release, live
AnkiWeb boundaries, and numeric-code installation evidence are recorded in
`RELEASE_RECORD_1.0.35.md`. Earlier releases remain in their versioned records.
