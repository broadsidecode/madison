# Experimental CapCut importer

The viewer works without CapCut, Tesseract or FFmpeg. This optional adapter can
inspect a local CapCut project and create a new editable Tesseract document from
a limited set of media clips. It never edits or exports back into CapCut.

CapCut's project format is undocumented and can change. Successful conversion
does not certify that the result looks or sounds like the original. Keep the
original editor project and compare any result before using it for delivery.

## Requirements

* Python 3.10 or later. Inspection uses only the Python standard library and
  works on Windows, macOS and Linux.
* Conversion requires a separately installed Tesseract **0.1.0** CLI on a
  supported Windows or macOS computer. Other versions fail the version gate.
* Audio imports require `ffprobe` on PATH. Audio referenced from a video
  container also requires `ffmpeg` to extract a WAV into the new output.
* The adapter does not install tools, accept licenses, download media, or make
  network requests. Third party tools retain their own terms and licenses.

The executable can be passed explicitly using `--tesseract`. Otherwise the adapter
checks PATH, then standard per-user Tesseract installation locations. Use an
installation you trust. This repository includes no editor engine, proprietary
plugin instructions, or other third party executable.

On Windows the standard installed `tsrct.cmd` shim is resolved to the existing
native `public-cli/0.1.0-x86_64/bin/tsrct.exe` inside that installation. If PATH
points to a batch launcher, the standard native executable is preferred when
available. Other batch launchers are rejected: pass the actual `tsrct.exe` with
`--tesseract`. Project and media filenames are never sent through a batch shell.

## Inspect first

Use the CLI's CapCut inspection command with an explicit project directory and
the exact displayed timeline name. Consult `python -m timeline_reviewer --help`
for the available commands and their argument spelling.

The reader uses `timeline_layout.json` to pair `timelineNames` with
`timelineIds`, then reads only the selected
`Timelines/<selected-id>/draft_content.json`. The root `draft_content.json` is
read for identity and provenance; its content is not silently substituted for a
missing named timeline. Duplicate names that map to different ids are rejected.
The nested document id must match the selected id. No other timeline drafts are
opened.

The inspection report contains the selected identity, source JSON hashes,
timeline duration, canvas, frame rate, segments, exact original microseconds,
missing media, and unsupported features. Missing media must be relinked to real
local files before conversion. A displayed material name is not a usable source
file. Network URLs, UNC paths, NUL paths, malformed ranges, non-finite numbers,
and timeline directory traversal are rejected.

## Conversion boundary

Supported conversion is intentionally narrow:

| Feature | Behavior |
| --- | --- |
| Video and audio cuts | One editable native layer per supported source segment |
| Source and edit ranges | Separate millisecond ranges; original microseconds retained |
| Layer order | Later CapCut tracks become higher native layers |
| Hidden clips | Retained with native hidden state |
| Static audio gain | Linear gain copied |
| Scalar playback speed | Linear native time remap, including speeds such as 0.95 |
| Basic video transforms | Position, scale, rotation, flip and opacity mapped |
| Source assets | Imported once per path and media type through the official CLI |

Active unsupported features stop conversion **before creating output or calling
Tesseract**. Findings include unsupported track and material types, keyframes,
volume automation, fades, effects, color operations, masks, transitions,
non-default crop, speed curves, and unrecognized referenced feature materials.
Non-neutral track gain, including a muted track with gain zero, is also a
blocking finding. Flip, separated-audio and pitch flags must be actual JSON
Booleans; strings or numeric substitutes are rejected.
Separated embedded audio and enabled pitch flags are also findings because
their source semantics are not verified. Lossy import mutes a video marked as
having separated audio and copies its pitch flag; audition the result.
Empty or recognized neutral records do not require an override. Unsupported
features on hidden clips remain reported even when they do not block import.

`--allow-lossy` explicitly accepts omission of diagnosed unsupported features.
It does not repair, emulate or flatten them. Unsupported tracks are omitted;
unsupported features on imported media layers are omitted. It never permits
missing sources, invalid timings, unsafe paths or overwriting a destination.
Because CapCut can introduce new fields, the findings are a conservative check
of the recognized format, not a complete compatibility guarantee.

The output must be outside the source project, its parent must exist, and the
destination must not already exist. After preflight, the adapter claims the
directory atomically. It invokes official `project create`, media import,
`checkout`, `commit`, and supported `apply` actions. It does not construct or
modify native archives directly. A final checkout verifies layer order, ranges,
hidden states, transforms, static gain and persisted playback remaps. Known
neutral transform defaults added by the engine are accepted; altered authored
values and unexpected transform fields fail verification. If native actions
trim trailing empty time, the adapter restores the original duration through a
fresh checkout and commit, then verifies a second readback. It hashes the
selected source JSON and every used source media file before and after conversion.

Source files should remain unchanged during import. Close the editor or stop
saving while converting. If an input changes during the operation, conversion
fails with a diagnostic report. Failed output is retained for inspection and
is never silently deleted or reused. Retry into a fresh directory.

## Output and privacy

The new output contains:

* `timeline.tsrct`, the editable native document with packaged imported assets.
* `import-report.json`, the selection, findings, source hashes, mapping and
  verification outcome.
* `import-data/`, working editable JSON, action batches and any derived audio.
* `preview.mp4` only when optional rendering was requested and succeeded.

Reports can contain absolute source paths, names, ids and project metadata.
Native documents contain your media. Keep generated imports and reports
private unless you deliberately review and authorize sharing them. The public
repository's fixtures use only invented metadata and synthetic bytes; they are
not usable footage.

## Rendering limitations

Conversion does **not** render by default. `--render` opts into an additional
native export. Tesseract 0.1.0 has a known native audio source-offset limitation;
an exported mix may select the wrong source moment even when the editable
layer's source range is correct. This adapter does not reconstruct a replacement
audio mix. Audition audio and compare cut boundaries before relying on an export.
Source endpoints up to 50 milliseconds beyond measured media duration are
retained with a warning, accommodating small editor timebase differences without
inflating the intrinsic media duration. Larger overruns fail conversion.

Native rendering is limited to the canvas sizes supported by the pinned engine.
Output frame rate is engine controlled; the original frame rate is recorded but
not guaranteed. Millisecond quantization can shift a boundary by up to half a
millisecond. Color effects, curves, automation and other omitted features are
not validated by an export succeeding.

On Windows, foreign application DLL directories on PATH can interfere with
native rendering. If the CLI fails to load a library, use a clean terminal
environment and consult the engine's own installation guidance. The adapter
does not rewrite your PATH, search unrelated application folders, or bypass
platform security prompts.

There is no bidirectional editing, CapCut writeback, cloud publishing, or
promise of full visual or audio fidelity in this experimental release.

## Testing

`python -m unittest discover -s tests -v` uses temporary synthetic projects and
mocked CLI responses. It requires no Tesseract installation or paid service.
Tests cover named timeline isolation, identity checks, microsecond conversion,
missing and unsafe media, unsupported preflight, destination preservation,
source change detection, layer order, hidden clips, scalar remap, and explicit
rendering. These are adapter contract tests, not a real-engine render audit.
