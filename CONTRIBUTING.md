# Contributing

Small improvements, accessibility fixes and synthetic bug reports are welcome.

Keep the core viewer dependency light and usable without an editing engine. Do not add accounts, uploads, telemetry or project writing as incidental changes. Keep the optional importer clearly separated and conservative about unsupported features.

Before submitting a change:

1. Run the Python test suite and JavaScript syntax check described in the README.
2. For visible changes, inspect wide and narrow layouts and keyboard navigation.
3. Exercise affected pointer controls, playback and feedback copying.
4. Confirm that tests and examples contain no personal media, project IDs, absolute private paths or credentials.
5. Explain limitations and what you tested. Do not claim editor compatibility solely because a file was created.

Please use synthetic fixtures. A rendered preview plus a small metadata example is usually enough to reproduce a viewer issue.
