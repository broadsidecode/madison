# Madison contributor guide

Madison is a local timeline review companion for Tesseract, with limited opt in editing and an optional experimental CapCut importer. Review only mode remains the default and also works with exports from other tools.

## Scope
- Keep the viewer independent of proprietary editing engines.
- Never edit a source editor project. Import into a new destination.
- Do not publish personal footage, project archives, absolute local paths, credentials, or generated review bundles.
- Review mode serves only validated bundle assets on IPv4 loopback. Editable mode may also serve explicitly approved local source videos from selected media roots and must reject cross origin writes.
- Keep changes scoped and preserve keyboard and pointer accessibility.

## Validation
Run `python -m unittest discover -s tests -v`.
Check modified JavaScript with `node --check timeline_reviewer/web/app.js`.
For UI changes, inspect desktop and narrow browser layouts and exercise playback, seeking, zoom, resizing and feedback.
Use synthetic fixtures in tests. No external account, editor installation or paid service is required for the default test suite.

## Access and secrets
Local viewer use needs no credentials. External publishing credentials belong to the contributor's local credential manager and must never enter repository files or logs.

## Third party tools
Tesseract and FFmpeg are separately installed tools. Do not bundle their executables, vendor plugin files or license-restricted resources. Preserve the documented experimental limitations.

## Publishing
Use the main branch and stage explicit files only. Review the complete publication file list and scan for private data before a public push.
