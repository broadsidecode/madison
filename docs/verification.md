# Verification notes

Release candidate checked on September 22, 2026. These results describe bounded tests, not a certification that arbitrary editor projects will convert faithfully.

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

## Repeatable automation

The repository's GitHub Actions workflow runs the Python suite, JavaScript syntax check and demo validation on Windows, macOS and Ubuntu with Python 3.10 and 3.13. Check the Actions tab for the result of the specific revision you use. Optional FFmpeg and symlink cases skip where their prerequisites are unavailable. Native Tesseract is not installed in CI; importer tests use a synthetic mocked engine.

No runtime Python packages, npm packages, editor binaries or vendor plugin resources are bundled. FFmpeg preparation and native Tesseract conversion are optional, separately installed tools.
