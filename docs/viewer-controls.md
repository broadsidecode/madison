# Viewer controls

Madison lets you inspect a rendered movie beside a timeline manifest and copy precise feedback. It does not change clips, source files, audio, or an editor project. Playback speed, zoom, the divider, selected clips, and feedback marks affect the review page only.

Open the address printed by the review server. The page loads the manifest and any supplied images first. It requests the movie when you play or seek. Titles, lane names, timing, frame rate, notes, and clip details come from the manifest.

## Preview and layout

Use **Play** or the movie's native controls to start and pause playback. **Preview speed** offers 0.5x, 1x, 1.5x, and 2x. This setting does not change the speed stored for any clip.

Drag the divider between the preview and timeline to give either area more room. Double click it to reset the layout. With the divider focused, press **Arrow Up** or **Arrow Down** to resize it; hold **Shift** for larger steps. **Home** selects the smallest permitted preview and **End** selects the largest. The bounds adapt to the window size. On narrow screens the divider changes the preview height while the details panel stays below the movie.

## Timeline navigation

| Control | Action |
| --- | --- |
| Click a clip | Select it, show its details, and seek to its start |
| Drag the ruler, final mix waveform, or playhead | Scrub through the movie without moving clips |
| Zoom slider or plus and minus buttons | Change the timeline scale around the visible playhead, or the viewport center if the playhead is elsewhere |
| Fit | Show the full timeline width |
| Ctrl or Cmd with the mouse wheel | Zoom around the pointer over the timeline |
| Shift with the mouse wheel | Scroll horizontally through the timeline |
| Middle mouse drag | Pan horizontally and vertically |
| Normal wheel or trackpad scrolling | Scroll through the timeline lanes |
| Frame buttons | Pause and step backward or forward by one manifest frame interval |
| Cut buttons | Pause and jump to the previous or next active picture clip boundary |

Cut navigation includes the starts and ends of active video clips, including overlays. It excludes audio clips, hidden clips, and parked lanes. Opening parked lanes does not change those cut targets.

Enter a position in **Time**, then choose **Go** or press **Enter**. Accepted formats are `mm:ss`, `mm:ss.mmm`, `hh:mm:ss`, and `hh:mm:ss.mmm`. For example, `01:23.456` means one minute and 23.456 seconds. The page reports invalid input and positions beyond the manifest duration.

Scrubbing temporarily pauses a playing movie and resumes it when you release the pointer. A canceled gesture leaves playback paused. Frame stepping uses the manifest frame rate; the exact decoded image depends on the movie and browser seeking behavior.

## Lanes and clip details

Video lanes use blue and teal. Separate audio lanes use amber. Picture clips may include their own dialogue. The **Final mix** waveform represents the supplied combined movie audio, not the isolated contents of each audio lane. Large waveform arrays are grouped by their strongest sample for display. If the manifest has no waveform, the lane says **No waveform supplied** and seeking still works.

**Show parked takes** reveals lanes marked as parked. The header counts visible lanes and clips and reports the total parked clip count. Clips marked hidden use a dashed outline and a text label. These are manifest annotations; the preview remains the supplied rendered movie.

**Find a clip** searches clip names, original source filenames when provided, clip identifiers, and lane names. Matching visible clips are highlighted. Matches in concealed parked lanes are counted separately so you can reveal them. Clearing the search restores normal emphasis.

Selecting a clip shows its movie range, source range, duration, speed, color review status, and a readable lane and clip number. **Copy edit reference** includes the precise timing, source filename, source identifiers, current movie position, and manifest revision. If the clipboard is unavailable, the page exposes selectable text for manual copying.

## Feedback ranges and notes

1. Seek to the start of the moment you want to discuss and choose **Mark in** or press **I**.
2. Seek to its end and choose **Mark out** or press **O**.
3. The marked range appears as a highlighted band with exact times. **Loop range** repeats that range during playback.
4. Open **Feedback** in the details panel, describe the requested change, and choose **Copy feedback**.
5. Paste the copied text into your conversation or review tool.

Copied feedback includes your note, the current position, any marked range, the selected clip's manifest identifiers when one is selected, and the manifest revision. The application adds no local filesystem paths. Source names and other manifest text are included as supplied.

A range needs an out point after its in point. If a new mark conflicts with the other endpoint, the other endpoint is cleared so you can set it again. **Clear** removes both endpoints and disables looping; it keeps your written note.

Notes, marks, and view settings stay in the current page only. Reloading or closing the page clears them. Copy feedback before leaving if you want to keep it. Copying uses the clipboard or a manual text field; the viewer does not submit feedback to a server.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| Space | Play or pause when focus is outside form fields and buttons |
| Arrow Left / Arrow Right | Pause and step one frame interval |
| Shift + Arrow Left / Arrow Right | Pause and seek five seconds |
| I / O | Mark the feedback range start or end |
| Home / End on the ruler, waveform, or playhead | Seek to the start or end of the timeline |
| Arrow Up / Arrow Down on the divider | Resize the preview |
| Shift + Arrow Up / Arrow Down on the divider | Resize the preview in larger steps |
| Home / End on the divider | Select its minimum or maximum height |

Global shortcuts do not run while you type in a field. Focused buttons and form controls retain their normal keyboard behavior. Native movie controls follow the browser's keyboard conventions. **Shortcuts** in the footer opens a quick reference.

## Missing or invalid content

If a manifest field is invalid, the page names the field and asks you to correct the manifest. The server also validates manifests before serving a bundle. Media URLs must be relative paths inside the review bundle; external URLs, absolute paths, queries, fragments, and traversal are rejected.

The movie must use a format your browser can decode. If it fails to load, the timeline can still be inspected. Missing thumbnails leave readable clip blocks; an absent poster or waveform is supported. The viewer does not generate missing media or render the timeline itself.
