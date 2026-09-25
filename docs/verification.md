# Verification notes

Release candidate checked on September 22, 2026. These results describe bounded tests, not a certification that arbitrary editor projects will convert faithfully.

## Single instance launcher safeguards

The launcher tests use synthetic state and process fixtures. They cover the portable demo default on 127.0.0.1:8464, private project selection, atomic concurrent launch locking, stale lock recovery, healthy instance reuse, unrelated port ownership, protected active work, authenticated safe replacement, verified legacy process handling, hidden background startup, and application selection rollback without project changes.

The running server handshake includes the process and frozen startup identity, application version, build identity, and configuration fingerprint. A changed checkout cannot make an older process claim the new build. Launcher shutdown requires the private bearer token. A release ZIP derives the same build identity without Git.

The start scripts now use this launcher. The original foreground `demo` and `serve` commands remain for contributor testing and explicitly selected ports. A full release check must still compare local source, the running process, served browser assets, remote main and CI, and the latest download separately.

## CapCut sync update

On September 23, the normal unconnected demo was checked again after a visibility fix. **Connect CapCut** appeared on desktop and phone; its setup panel opened by keyboard and pointer, offered a copyable agent request, and made no sync request. A separately bound sample still showed **Sync from CapCut** and completed a real Tesseract import, render, and viewer update. The full audition project was checked only through its change preview; no import was started because its CapCut effects and audio need review. That project also exposed an inflated move count: lane changes and subframe rounding had been counted as moved clips. The comparison now reports lane changes, small timing shifts, and larger moves separately. Four focused tests cover those distinctions.

The optional local sync was checked with 81 Python tests (one environment skip), JavaScript syntax, and a validated demo bundle. A separate two second synthetic project completed a real Tesseract import and render, prepared a matching review bundle, and switched the viewer only after verification. The old preview remained available. A second check with no saved source changes created no new version. Browser interaction checked the change preview, Sync button, completed viewer update, and no-change state. Rendered desktop, tablet, and phone checks passed.

The same contract tests cover missing media, stale source revisions, unsupported effect acknowledgement, trimmed audio warnings, failed rendering, and preservation of the prior review. These checks do not prove full CapCut effect or audio fidelity for arbitrary projects. A creator should compare the new movie with the saved CapCut cut before delivery.

## Local checks

Windows, Python 3.12, Node.js 24 and a fresh headless Chromium browser:

- Python suite: 48 tests run, 47 passed and one skipped because the host could not create symlinks. The FFmpeg preparation test ran against the included synthetic movie and preserved its source hash.
- JavaScript syntax and the bundled manifest passed validation.
- A new synthetic demo was generated into a separate directory and validated.
- Rendered layout checks passed at desktop, tablet, narrow phone and 200 percent zoom sizes. Expected hidden controls were recorded as warnings, not missing functionality.
- Five browser regression groups passed: selection, playback and navigation; zoom and pan; divider resizing and scrubbing; parked takes, range looping and clipboard fallbacks; and the 390 pixel layout.
- Browser tests recorded no script errors or failed requests. Demo movie and metadata hashes remained unchanged.
- Server tests cover IPv4 loopback, occupied ports, approved asset access, write rejection, Host validation, malformed paths and byte ranges.

The README uses an episode review screenshot supplied and approved for publication by the creator. The underlying footage and editor project are not included. The runnable demo remains synthetic.

## Native importer smoke test

A separately installed Windows Tesseract 0.1.0 executable imported a synthetic six second project with two video layers, distinct source offsets, a 0.95 scalar retime and static gain. Native checkout verified the layer ranges, transforms, playback graph and full document duration, including trailing empty time. Source hashes were unchanged.

The engine adds neutral transform fields and can trim trailing duration when applying a playback action. The adapter checks the authored transform values, accepts only known neutral additions, restores duration through a fresh checkout and commit, and verifies the result again.

This smoke test did not render a movie. It does not establish audio export accuracy, visual fidelity for arbitrary CapCut projects, or macOS native engine compatibility. Unsupported features and the known native audio timing limitation remain documented in the [importer guide](experimental-importer.md).

## Tesseract 0.2.0 check

A separately installed Windows Tesseract 0.2.0 executable repeated both native checks with synthetic footage only. The CapCut import read back every authored layer field, rendered a six second 1920 by 1080 movie with the expected source moment, recorded version 0.2.0 in its report and left the source media hash unchanged. The editing smoke test removed, restored, trimmed and changed volume on a new native project, resumed the saved draft and left the original project unchanged. Tesseract 0.1.0 repeated the import check with the same result. Both versions read the same document format. The native audio timing limitation was not retested.

## Tesseract status light

The viewer's version panel compares the installed engine with the versions Madison supports and with Mirage's published version pin. Unit tests cover every status, an offline check, a failing engine, and sanitized server output. A synthetic demo rendered the amber update state on desktop and phone layouts, and opening the panel showed the Tesseract line. On this Windows host, the live pin read 0.2.0: an installed 0.2.0 engine showed current and 0.1.0 showed a newer version available.

## Repeatable automation

The repository's GitHub Actions workflow runs the Python suite, JavaScript syntax check and demo validation on Windows, macOS and Ubuntu with Python 3.10 and 3.13. Check the Actions tab for the result of the specific revision you use. Optional FFmpeg and symlink cases skip where their prerequisites are unavailable. Native Tesseract is not installed in CI; importer tests use a synthetic mocked engine.

No runtime Python packages, npm packages, editor binaries or vendor plugin resources are bundled. FFmpeg preparation and native Tesseract conversion are optional, separately installed tools.

## Limited editing checks

The native edit engine has focused synthetic tests for clip removal, range trimming, volume, changed projects, persistent drafts, duplicate native layer references and malformed animation metadata. The full local Python suite ran 69 tests, with one skipped because this Windows host could not create symlinks.

A local Tesseract 0.1.0 smoke test used only synthetic footage. It applied a batch to a new native project, read back the edited ranges and volume, confirmed every untouched native field and the original file were unchanged, then recovered the saved version after reopening the editor session. Repeated viewer polls reused the native file hash while fresh commits forced a new hash. Native rendering was not part of that smoke test.

The read only demo passed governed rendered layout checks. An editable synthetic bundle passed browser interaction checks for remove, restore, undo, redo, draft source picture, reload state and a custom picture in picture window. A separate external review update kept the selected clip, note, zoom and playhead in place while its movie and duration changed; editing paused until a matching native project could be reconnected. The governed scan of the editable example flagged canceled video range requests during seeking as network failures. Its source frame rendered and the interaction checks passed; the scan is not counted as a clean editable mode PASS.

Browser picture in picture support can differ by browser. The custom window was exercised in a headless Chromium test; visible Edge behavior is not established by that result. A pending draft movie is an approximation until a fresh final export is reviewed.
