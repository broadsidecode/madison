'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const video = $('review-video');
  const sourceVideo = $('source-video');
  const stage = $('video-stage');
  const previewMessage = $('draft-preview-message');
  const hasDocumentPip = 'documentPictureInPicture' in window && typeof window.documentPictureInPicture.requestWindow === 'function';
  const hasNativePip = Boolean(document.pictureInPictureEnabled && typeof video.requestPictureInPicture === 'function');
  const scroller = $('timeline-scroll');
  const content = $('timeline-content');
  const ruler = $('time-ruler');
  const clipElements = new Map();
  const trackElements = new Map();
  const knownClipNames = new Map();
  const baseClips = new Map();
  let project;
  let scale = 1;
  let fitScale = 1;
  let fitMode = true;
  let selected;
  let animation;
  let pendingSeek = null;
  let mediaLoadStarted = false;
  let previewHeight = 340;
  let dividerDrag = null;
  let scrub = null;
  let pan = null;
  let manualPanUntil = 0;
  let rangeStart = null;
  let rangeEnd = null;
  let timecodeDirty = false;
  let pictureCuts = [];
  let rulerAnimation = null;
  let editState = null;
  let editBusy = false;
  let undoStates = [];
  let redoStates = [];
  let refreshBusy = false;
  let capcutSyncState = null;
  let capcutSyncBusy = false;
  let capcutSyncPoll = null;
  let capcutSyncWasLossy = false;
  let renderPending = false;
  let sourceClipId = null;
  const failedSourceUrls = new Set();
  let popoutWindow = null;
  let popoutHost = null;
  let saveTimer = null;
  const viewKey = `madison-view:${location.pathname}`;

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const currentSeconds = () => pendingSeek ?? video.currentTime;
  const hasRange = () => rangeStart !== null && rangeEnd !== null && rangeEnd > rangeStart;
  const maxScale = () => Math.max(80, fitScale * 32);
  const calculateFitScale = () => Math.max(0.000001, Math.max(1, scroller.clientWidth - labelWidth() - 3) / project.duration);

  function safeSourceUrl(value) {
    if (typeof value !== 'string' || !value || value !== value.trim() || value.includes('\\') || /[\u0000-\u001f\u007f]/.test(value)) return '';
    try {
      const url = new URL(value, document.baseURI);
      if (!['http:', 'https:'].includes(url.protocol) || url.origin !== location.origin || !url.pathname.startsWith(new URL('.', document.baseURI).pathname)) return '';
      return url.href;
    } catch { return ''; }
  }

  function saveViewState() {
    if (!project || saveTimer) return;
    saveTimer = setTimeout(() => {
      saveTimer = null;
      try {
        sessionStorage.setItem(viewKey, JSON.stringify({ time: currentSeconds(), scale, fitMode, scrollLeft: scroller.scrollLeft, scrollTop: scroller.scrollTop, selectedId: selected?.clip.id || null, previewHeight, rangeStart, rangeEnd, loop: $('loop-range').checked, note: $('review-note').value, playbackRate: Number($('playback-rate').value), playing: !video.paused && !video.ended, showParked: $('show-parked').checked }));
      } catch { /* Storage can be unavailable in private browsing. */ }
    }, 150);
  }

  function saveViewNow() {
    if (!project) return;
    clearTimeout(saveTimer);
    saveTimer = null;
    try { sessionStorage.setItem(viewKey, JSON.stringify(captureView())); } catch { /* Storage is optional. */ }
  }

  function storedView() {
    try {
      const state = JSON.parse(sessionStorage.getItem(viewKey) || 'null');
      return state && typeof state === 'object' && !Array.isArray(state) ? state : null;
    } catch { return null; }
  }

  function captureView() {
    return { time: currentSeconds(), scale, fitMode, scrollLeft: scroller.scrollLeft, scrollTop: scroller.scrollTop, selectedId: selected?.clip.id || null, previewHeight, rangeStart, rangeEnd, loop: $('loop-range').checked, note: $('review-note').value, playbackRate: Number($('playback-rate').value), playing: !video.paused && !video.ended, showParked: $('show-parked').checked };
  }

  function previewBounds() {
    if (matchMedia('(max-width: 650px)').matches) return { min: 180, max: Math.max(300, Math.floor(innerHeight * 0.75)) };
    const headerHeight = document.querySelector('.app-header').getBoundingClientRect().height;
    return { min: 160, max: Math.max(160, Math.floor(innerHeight - headerHeight - 310)) };
  }

  function setPreviewHeight(height) {
    const bounds = previewBounds();
    previewHeight = Math.round(clamp(height, bounds.min, bounds.max));
    document.documentElement.style.setProperty('--preview-height', `${previewHeight}px`);
    document.body.dataset.previewHeight = String(previewHeight);
    const divider = $('preview-divider');
    divider.setAttribute('aria-valuemin', String(bounds.min));
    divider.setAttribute('aria-valuemax', String(bounds.max));
    divider.setAttribute('aria-valuenow', String(previewHeight));
    divider.setAttribute('aria-valuetext', `Preview height ${previewHeight} pixels`);
    saveViewState();
  }

  function resetPreviewHeight() {
    setPreviewHeight(matchMedia('(max-width: 650px)').matches ? 360 : 340);
  }

  function clock(seconds, precise = true) {
    const millis = Math.max(0, Math.round((Number(seconds) || 0) * 1000));
    const minutes = Math.floor(millis / 60000);
    const secs = Math.floor(millis / 1000) % 60;
    return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}${precise ? `.${String(millis % 1000).padStart(3, '0')}` : ''}`;
  }

  function labelWidth() {
    return parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--label-width')) || 112;
  }

  function fail(message) {
    $('error-message').hidden = false;
    $('error-message').textContent = message;
  }

  function safeLocalUrl(value) {
    if (typeof value !== 'string' || !value || value !== value.trim()) return '';
    try {
      const decoded = decodeURIComponent(value);
      if (decoded.startsWith('/') || decoded.includes('\\') || /[:%?#\u0000-\u001f\u007f]/.test(decoded) || decoded.split('/').some((part) => part === '..')) return '';
      const base = new URL('.', document.baseURI);
      const url = new URL(value, base);
      if (!['http:', 'https:'].includes(url.protocol) || url.origin !== base.origin || !url.pathname.startsWith(base.pathname)) return '';
      const relativePath = url.pathname.slice(base.pathname.length);
      return relativePath || '';
    } catch {
      return '';
    }
  }

  function validateManifest(data) {
    const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
    const finite = (value) => typeof value === 'number' && Number.isFinite(value);
    const text = (value) => typeof value === 'string' && value.trim().length > 0;
    const requireField = (condition, field, explanation) => {
      if (!condition) throw new Error(`Manifest ${field}: ${explanation}.`);
    };
    requireField(object(data), 'root', 'provide a JSON object');
    requireField(data.schemaVersion === 1, 'schemaVersion', 'use version 1');
    requireField(text(data.title), 'title', 'provide a nonempty title');
    requireField(finite(data.duration) && data.duration > 0 && data.duration <= 86400, 'duration', 'provide a positive duration of at most one day in seconds');
    requireField(finite(data.fps) && data.fps > 0 && data.fps <= 240, 'fps', 'provide a positive frame rate of at most 240');
    requireField(typeof data.revision === 'string', 'revision', 'provide a string');
    requireField(Boolean(safeLocalUrl(data.videoUrl)), 'videoUrl', 'use a relative media path inside the review folder');
    if (data.posterUrl != null) requireField(Boolean(safeLocalUrl(data.posterUrl)), 'posterUrl', 'use a relative image path inside the review folder');
    requireField(Array.isArray(data.tracks) && data.tracks.length <= 200, 'tracks', 'provide an array of at most 200 lanes');
    const laneIds = new Set();
    const clipIds = new Set();
    let clipCount = 0;
    data.tracks.forEach((track, trackIndex) => {
      const path = `tracks[${trackIndex}]`;
      requireField(object(track), path, 'provide a lane object');
      requireField(text(track.id) && !laneIds.has(track.id), `${path}.id`, 'provide a unique nonempty lane ID');
      laneIds.add(track.id);
      requireField(text(track.name), `${path}.name`, 'provide a lane name');
      requireField(track.kind === 'video' || track.kind === 'audio', `${path}.kind`, 'use video or audio');
      requireField(typeof track.parked === 'boolean', `${path}.parked`, 'use true or false');
      requireField(Array.isArray(track.clips), `${path}.clips`, 'provide an array of clips');
      clipCount += track.clips.length;
      requireField(clipCount <= 10000, 'tracks', 'provide at most 10000 clips across all lanes');
      track.clips.forEach((clip, clipIndex) => {
        const field = `${path}.clips[${clipIndex}]`;
        requireField(object(clip), field, 'provide a clip object');
        requireField(text(clip.id) && !clipIds.has(clip.id), `${field}.id`, 'provide a unique nonempty clip ID');
        clipIds.add(clip.id);
        requireField(text(clip.label), `${field}.label`, 'provide a clip name');
        for (const key of ['start', 'end', 'duration', 'sourceStart', 'sourceEnd']) {
          requireField(finite(clip[key]) && clip[key] >= 0, `${field}.${key}`, 'provide a nonnegative number of seconds');
        }
        requireField(clip.duration > 0 && clip.end > clip.start && Math.abs(clip.end - clip.start - clip.duration) <= 0.002, field, 'duration must agree with the start and end times');
        requireField(clip.end <= data.duration + 0.002, `${field}.end`, 'keep the clip within the timeline duration');
        requireField(clip.sourceEnd >= clip.sourceStart, field, 'source end must not precede source start');
        requireField(finite(clip.speed) && clip.speed > 0, `${field}.speed`, 'provide a positive playback speed');
        requireField(typeof clip.hidden === 'boolean', `${field}.hidden`, 'use true or false');
        if (clip.layerId != null) requireField(finite(clip.layerId) && clip.layerId >= 0, `${field}.layerId`, 'provide a nonnegative source layer identifier');
        if (clip.sourceFilename != null) requireField(typeof clip.sourceFilename === 'string', `${field}.sourceFilename`, 'provide a string');
        if (clip.thumbnail != null) requireField(Boolean(safeLocalUrl(clip.thumbnail)), `${field}.thumbnail`, 'use a relative image path inside the review folder');
        if (clip.colorPending != null) requireField(typeof clip.colorPending === 'boolean', `${field}.colorPending`, 'use true or false');
        if (clip.volume != null) requireField(finite(clip.volume) && clip.volume >= 0, `${field}.volume`, 'provide a nonnegative number');
      });
    });
    if (data.waveform != null) {
      requireField(object(data.waveform), 'waveform', 'provide a waveform object or null');
      requireField(finite(data.waveform.step) && data.waveform.step > 0, 'waveform.step', 'provide a positive sample interval in seconds');
      requireField(Array.isArray(data.waveform.peaks) && data.waveform.peaks.length <= 50000 && data.waveform.peaks.every((peak) => finite(peak) && peak >= 0 && peak <= 1), 'waveform.peaks', 'provide at most 50000 numbers from zero to one');
    }
    if (data.notes != null) requireField(Array.isArray(data.notes) && data.notes.every((note) => typeof note === 'string'), 'notes', 'provide an array of text notes');
    return data;
  }

  function renderNotes() {
    const notes = $('project-notes');
    const fragment = document.createDocumentFragment();
    for (const text of project.notes || []) {
      const item = document.createElement('li');
      item.textContent = text;
      fragment.append(item);
    }
    notes.replaceChildren(fragment);
    notes.hidden = !project.notes?.length;
  }

  function updateTime(follow = true) {
    if (!project) return;
    let current = currentSeconds();
    if ($('loop-range').checked && hasRange() && !video.paused && !scrub && (current >= rangeEnd || current < rangeStart)) {
      video.currentTime = rangeStart;
      pendingSeek = null;
      current = rangeStart;
    }
    document.body.dataset.currentSeconds = current.toFixed(3);
    $('time-display').textContent = `${clock(current)} / ${clock(project.duration)}`;
    if (popoutWindow) {
      const pipClock = popoutWindow.document.getElementById('pip-time');
      const pipSeek = popoutWindow.document.getElementById('pip-seek');
      if (pipClock) pipClock.textContent = `${clock(current)} / ${clock(project.duration)}`;
      if (pipSeek && popoutWindow.document.activeElement !== pipSeek) {
        pipSeek.max = String(project.duration);
        pipSeek.value = String(current);
      }
    }
    $('playhead').style.left = `${labelWidth() + current * scale}px`;
    $('playhead').style.visibility = current * scale < scroller.scrollLeft ? 'hidden' : 'visible';
    $('playhead').setAttribute('aria-valuenow', String(current));
    $('playhead').setAttribute('aria-valuetext', clock(current));
    if (!timecodeDirty && document.activeElement !== $('timecode-input')) $('timecode-input').value = clock(current);
    ruler.setAttribute('aria-valuenow', String(current));
    ruler.setAttribute('aria-valuetext', clock(current));
    $('mix-waveform').setAttribute('aria-valuenow', String(current));
    $('mix-waveform').setAttribute('aria-valuetext', clock(current));
    if (follow && !video.paused && !video.ended && !scrub && !pan && performance.now() > manualPanUntil) {
      const x = current * scale;
      const available = scroller.clientWidth - labelWidth();
      if (x > scroller.scrollLeft + available - 25 || x < scroller.scrollLeft) {
        scroller.scrollLeft = Math.max(0, x - available * 0.2);
      }
    }
    syncDraftPreview();
    saveViewState();
  }

  function activePictureAt(second) {
    for (const track of project.tracks) {
      if (track.kind !== 'video' || track.parked) continue;
      for (const clip of track.clips) {
        if (!clip.hidden && clip.start <= second && second < clip.end) return clip;
      }
    }
    return null;
  }

  function syncDraftPreview() {
    if (!project) return;
    const draft = Boolean(editState?.enabled && (editState.operations?.length || renderPending));
    video.muted = draft;
    video.controls = !popoutWindow && !draft;
    stage.dataset.draft = String(draft);
    if (!draft) {
      sourceVideo.pause();
      sourceVideo.hidden = true;
      previewMessage.hidden = true;
      $('preview-label').textContent = 'Current rendered movie';
      return;
    }
    const clip = activePictureAt(Math.min(currentSeconds(), Math.max(0, project.duration - 0.001)));
    const source = clip && editState.sources?.[clip.id];
    const url = source && safeSourceUrl(source.url);
    $('preview-label').textContent = 'Draft picture preview';
    previewMessage.hidden = false;
    if (!url || failedSourceUrls.has(url)) {
      sourceVideo.pause();
      sourceVideo.hidden = true;
      sourceClipId = null;
      previewMessage.textContent = clip ? 'Picture and audio pending a fresh render.' : 'No picture at this position. Audio pending a fresh render.';
      return;
    }
    if (sourceClipId !== clip.id || sourceVideo.src !== url) {
      sourceClipId = clip.id;
      sourceVideo.src = url;
    }
    sourceVideo.hidden = false;
    const sourceTime = clip.sourceStart + (currentSeconds() - clip.start) * clip.speed;
    if (sourceVideo.readyState >= 1 && Number.isFinite(sourceTime) && Math.abs(sourceVideo.currentTime - sourceTime) > (video.paused ? 0.03 : 0.12)) {
      try { sourceVideo.currentTime = Math.max(0, sourceTime); } catch { /* Wait for metadata. */ }
    }
    sourceVideo.playbackRate = clip.speed * Number($('playback-rate').value);
    if (video.paused) sourceVideo.pause();
    else if (sourceVideo.paused) sourceVideo.play().catch(() => {});
    previewMessage.textContent = 'Approximate source picture. Audio and final effects pending a fresh render.';
  }

  function ensureMediaLoaded() {
    if (mediaLoadStarted) return;
    mediaLoadStarted = true;
    video.preload = 'auto';
    video.load();
  }

  function seek(seconds) {
    if (!project) return;
    const target = Math.min(project.duration, Math.max(0, seconds));
    pendingSeek = target;
    if (video.readyState >= 1) {
      video.currentTime = target;
      pendingSeek = null;
    } else {
      ensureMediaLoaded();
    }
    updateTime();
  }

  function revealCurrentTime() {
    const x = currentSeconds() * scale;
    const available = scroller.clientWidth - labelWidth();
    if (x < scroller.scrollLeft || x > scroller.scrollLeft + available) scroller.scrollLeft = Math.max(0, x - available / 2);
  }

  function stepFrame(direction) {
    if (!project) return;
    video.pause();
    seek(currentSeconds() + direction / project.fps);
    revealCurrentTime();
  }

  function goToCut(direction) {
    if (!project) return;
    const current = currentSeconds();
    const target = direction > 0 ? pictureCuts.find((time) => time > current + 0.001) : pictureCuts.slice().reverse().find((time) => time < current - 0.001);
    video.pause();
    seek(target ?? (direction > 0 ? project.duration : 0));
    revealCurrentTime();
  }

  function parseTimecode(value) {
    const parts = value.trim().split(':');
    if (parts.length < 2 || parts.length > 3 || !parts.every((part, index) => index === parts.length - 1 ? /^\d{1,2}(?:\.\d{1,3})?$/.test(part) : /^\d+$/.test(part))) return null;
    const seconds = Number(parts.pop());
    const minutes = Number(parts.pop());
    const hours = parts.length ? Number(parts.pop()) : 0;
    if (seconds >= 60 || (value.trim().split(':').length === 3 && minutes >= 60)) return null;
    const result = hours * 3600 + minutes * 60 + seconds;
    return Number.isFinite(result) ? result : null;
  }

  function jumpToTime(event) {
    event.preventDefault();
    if (!project) return;
    const seconds = parseTimecode($('timecode-input').value);
    if (seconds === null || seconds > project.duration) {
      $('timecode-input').setAttribute('aria-invalid', 'true');
      $('timecode-status').textContent = `Enter a time from 00:00.000 to ${clock(project.duration)}. Use mm:ss.mmm or hh:mm:ss.`;
      $('timecode-input').focus();
      return;
    }
    timecodeDirty = false;
    $('timecode-input').removeAttribute('aria-invalid');
    $('timecode-status').textContent = '';
    video.pause();
    seek(seconds);
    $('timecode-input').value = clock(seconds);
    revealCurrentTime();
  }

  function playbackTick() {
    updateTime();
    if (!video.paused && !video.ended) animation = requestAnimationFrame(playbackTick);
  }

  function syncPlayback() {
    const playing = !video.paused && !video.ended;
    document.body.dataset.playing = String(playing);
    $('play-label').textContent = playing ? 'Pause' : 'Play';
    $('play-button').setAttribute('aria-label', playing ? 'Pause movie' : 'Play movie');
    if (popoutWindow) {
      const button = popoutWindow.document.getElementById('pip-play');
      if (button) button.textContent = playing ? 'Pause' : 'Play';
    }
    cancelAnimationFrame(animation);
    if (playing) playbackTick();
    else updateTime();
  }

  async function togglePlayback() {
    if (!project) return;
    if (!video.paused) video.pause();
    else {
      ensureMediaLoaded();
      if ($('loop-range').checked && hasRange() && (currentSeconds() >= rangeEnd || currentSeconds() < rangeStart)) seek(rangeStart);
      try { await video.play(); }
      catch { fail('The preview could not play. Check that the local preview movie is available, then reload this page.'); }
    }
  }

  function selectClip(clip, track, jump = true) {
    if (selected) clipElements.get(selected.clip.id)?.setAttribute('aria-pressed', 'false');
    selected = { clip, track };
    clipElements.get(clip.id)?.setAttribute('aria-pressed', 'true');
    $('empty-selection').hidden = true;
    $('selected-clip').hidden = false;
    $('selected-clip').dataset.clipId = clip.id;
    $('selected-track').textContent = track.name;
    $('clip-name').textContent = clip.label;
    const removed = editState?.operations?.some((op) => op.type === 'remove' && op.clipId === clip.id);
    $('clip-state').textContent = removed ? 'Removed from this draft. Restore it before applying changes.' : `${track.parked ? 'Parked lane. ' : ''}${clip.hidden ? 'Marked hidden in the timeline.' : 'Included in the timeline.'}`;
    $('clip-edit-time').textContent = `${clock(clip.start)} to ${clock(clip.end)}`;
    $('clip-source-time').textContent = `${clock(clip.sourceStart)} to ${clock(clip.sourceEnd)}`;
    $('clip-duration').textContent = clock(clip.duration);
    $('clip-speed').textContent = `${Number(clip.speed).toFixed(3).replace(/\.?0+$/, '')}x`;
    $('clip-color').textContent = track.kind === 'audio' ? 'Not applicable' : clip.colorPending == null ? 'Not specified' : clip.colorPending ? 'Needs review' : 'No pending adjustment';
    $('clip-id').textContent = `${track.name} / Clip ${track.clips.indexOf(clip) + 1}`;
    $('copy-status').textContent = '';
    $('copy-fallback').hidden = true;
    updateEditInspector();
    if (jump) seek(clip.start);
  }

  function updateEditInspector() {
    const enabled = Boolean(editState?.enabled);
    $('clip-edit-controls').hidden = !enabled || !selected;
    if (!enabled || !selected) return;
    const clip = selected.clip;
    const ops = editState.operations || [];
    const removed = ops.some((op) => op.type === 'remove' && op.clipId === clip.id);
    $('remove-clip').hidden = removed;
    $('restore-clip').hidden = !removed;
    $('trim-form').hidden = removed;
    $('trim-form').querySelector('button[type="submit"]').disabled = editBusy || clip.speed !== 1 || clip.layerId == null;
    $('volume-form').hidden = removed;
    for (const id of ['remove-clip', 'restore-clip', 'mute-clip']) $(id).disabled = editBusy || clip.layerId == null;
    $('volume-form').querySelector('button[type="submit"]').disabled = editBusy || clip.layerId == null;
    const trim = ops.find((op) => op.type === 'trim' && op.clipId === clip.id);
    const volume = ops.find((op) => op.type === 'volume' && op.clipId === clip.id);
    $('trim-start').value = String(trim?.start ?? clip.start);
    $('trim-end').value = String(trim?.end ?? clip.end);
    $('clip-volume').value = String(volume?.volume ?? clip.volume ?? 1);
  }

  function editLabel(op) {
    const label = knownClipNames.get(op.clipId) || op.clipId;
    if (op.type === 'remove') return `Remove ${label}`;
    if (op.type === 'trim') return `Trim ${label} to ${clock(op.start)}–${clock(op.end)}`;
    return `Set ${label} volume to ${Math.round(op.volume * 100)}%`;
  }

  function renderEditUI() {
    const enabled = Boolean(editState?.enabled);
    $('edit-toolbar').hidden = !enabled;
    $('mode-label').textContent = enabled ? 'Tesseract editing' : $('capcut-sync-open').hidden ? 'Review only' : 'CapCut sync available';
    if (!enabled) return;
    const ops = editState.operations || [];
    $('edit-count').textContent = ops.length ? `${ops.length} change${ops.length === 1 ? '' : 's'} queued` : 'No changes queued';
    $('undo-edit').disabled = editBusy || !undoStates.length;
    $('redo-edit').disabled = editBusy || !redoStates.length;
    $('apply-edits').disabled = editBusy || !ops.length;
    const queue = $('queued-edits');
    const fragment = document.createDocumentFragment();
    for (const op of ops) {
      const item = document.createElement('span');
      item.className = 'queued-edit';
      item.textContent = editLabel(op);
      const revert = document.createElement('button');
      revert.type = 'button';
      revert.disabled = editBusy;
      revert.textContent = 'Restore';
      revert.setAttribute('aria-label', `Restore ${editLabel(op)}`);
      revert.addEventListener('click', () => replaceOperations(ops.filter((entry) => entry !== op)));
      item.append(revert);
      fragment.append(item);
    }
    queue.replaceChildren(fragment);
    updateEditInspector();
  }

  async function replaceOperations(operations, history = 'push') {
    if (!editState?.enabled || editBusy) return;
    const previous = editState.operations || [];
    if (JSON.stringify(operations) === JSON.stringify(previous)) return;
    editBusy = true;
    $('edit-status').textContent = 'Saving draft…';
    renderEditUI();
    try {
      const response = await fetch('./edit-draft', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json', 'X-Madison-Edit-Token': editState.csrfToken }, body: JSON.stringify({ revision: editState.revision, operations }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Draft rejected (HTTP ${response.status})`);
      if (history === 'push') { undoStates.push(previous); redoStates = []; }
      if (history === 'undo') { undoStates.pop(); redoStates.push(previous); }
      if (history === 'redo') { redoStates.pop(); undoStates.push(previous); }
      applyEditState(result);
      $('edit-status').textContent = 'Draft saved. Review the picture, then apply changes.';
    } catch (error) {
      $('edit-status').textContent = `${error.message || 'Draft save failed'}. Changes were not applied.`;
    } finally {
      editBusy = false;
      renderEditUI();
    }
  }

  function setClipOperation(operation) {
    if (!selected) return;
    if (!Number.isInteger(selected.clip.layerId)) { $('clip-edit-status').textContent = 'This clip has no Tesseract layer reference, so it cannot be edited here.'; return; }
    operation.layerId = selected.clip.layerId;
    const existing = editState.operations || [];
    const next = existing.filter((item) => !(item.type === operation.type && item.clipId === operation.clipId));
    next.push(operation);
    replaceOperations(next);
  }

  function referenceText() {
    const { clip, track } = selected;
    return [project.title, `Track: ${track.name}`, `Clip: ${clip.label}`, `Source filename: ${clip.sourceFilename || clip.label}`, `Clip ID: ${clip.id}`, clip.layerId == null ? null : `Layer ID: ${clip.layerId}`, `Current position: ${clock(video.currentTime)}`, `Current position seconds: ${video.currentTime}`, `Movie: ${clock(clip.start)} to ${clock(clip.end)}`, `Movie seconds: ${clip.start} to ${clip.end}`, `Source: ${clock(clip.sourceStart)} to ${clock(clip.sourceEnd)}`, `Speed: ${clip.speed}x`, `Hidden: ${clip.hidden ? 'yes' : 'no'}`, `Revision: ${project.revision}`, 'Requested change: '].filter((line) => line !== null).join('\n');
  }

  async function copyReference() {
    if (!selected) return;
    const text = referenceText();
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      $('copy-status').textContent = 'Copied. Paste the reference with your requested change.';
    } catch {
      const fallback = $('copy-fallback');
      fallback.value = text;
      fallback.hidden = false;
      fallback.focus();
      fallback.select();
      $('copy-status').textContent = 'Select and copy the reference below.';
    }
  }

  function renderRange() {
    document.body.dataset.rangeStart = rangeStart === null ? '' : rangeStart.toFixed(3);
    document.body.dataset.rangeEnd = rangeEnd === null ? '' : rangeEnd.toFixed(3);
    $('mark-in').setAttribute('aria-pressed', String(rangeStart !== null));
    $('mark-out').setAttribute('aria-pressed', String(rangeEnd !== null));
    $('clear-range').disabled = rangeStart === null && rangeEnd === null;
    $('loop-range').disabled = !hasRange();
    if (!hasRange()) $('loop-range').checked = false;
    $('range-display').textContent = rangeStart === null && rangeEnd === null ? 'No range marked' : `${rangeStart === null ? 'Set in' : clock(rangeStart)} to ${rangeEnd === null ? 'Set out' : clock(rangeEnd)}`;
    const highlight = $('range-highlight');
    highlight.hidden = !hasRange();
    if (hasRange()) {
      highlight.style.left = `${labelWidth() + rangeStart * scale}px`;
      highlight.style.width = `${Math.max(1, (rangeEnd - rangeStart) * scale)}px`;
    }
  }

  function markRange(edge) {
    if (!project) return;
    const current = clamp(currentSeconds(), 0, project.duration);
    if (edge === 'in') {
      rangeStart = current;
      if (rangeEnd !== null && rangeEnd <= rangeStart) rangeEnd = null;
    } else {
      rangeEnd = current;
      if (rangeStart !== null && rangeStart >= rangeEnd) rangeStart = null;
    }
    renderRange();
    saveViewState();
  }

  async function copyFeedback() {
    if (!project) return;
    const parts = [project.title, `Current position: ${clock(currentSeconds())}`, `Current position seconds: ${currentSeconds()}`];
    parts.push(hasRange() ? `Feedback range: ${clock(rangeStart)} to ${clock(rangeEnd)}` : 'Feedback range: no complete range marked');
    if (rangeStart !== null) parts.push(`Range start seconds: ${rangeStart}`);
    if (rangeEnd !== null) parts.push(`Range end seconds: ${rangeEnd}`);
    if (selected) {
      parts.push(`Lane: ${selected.track.name}`, `Clip: ${selected.clip.label}`, `Source filename: ${selected.clip.sourceFilename || selected.clip.label}`, `Clip ID: ${selected.clip.id}`);
      if (selected.clip.layerId != null) parts.push(`Layer ID: ${selected.clip.layerId}`);
    } else parts.push('Selected clip: none');
    parts.push(`Revision: ${project.revision}`, '', `Requested change: ${$('review-note').value.trim() || '(add your feedback here)'}`);
    const text = parts.join('\n');
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      $('feedback-fallback').hidden = true;
      $('feedback-status').textContent = 'Copied. Paste this feedback into your agent conversation.';
    } catch {
      const fallback = $('feedback-fallback');
      fallback.value = text;
      fallback.hidden = false;
      fallback.focus();
      fallback.select();
      $('feedback-status').textContent = 'Select and copy the feedback below.';
    }
  }

  function renderTracks() {
    const fragment = document.createDocumentFragment();
    clipElements.clear();
    trackElements.clear();
    for (const track of project.tracks) {
      const row = document.createElement('div');
      row.className = `track-row ${track.kind === 'audio' ? 'audio-row' : 'video-row'}`;
      row.dataset.trackId = track.id;
      row.dataset.parked = String(Boolean(track.parked));
      trackElements.set(track.id, row);
      const label = document.createElement('div');
      label.className = 'track-label';
      const name = document.createElement('strong');
      name.textContent = track.name;
      const count = document.createElement('small');
      count.textContent = `${track.clips.length} clips`;
      label.append(name, count);
      const lane = document.createElement('div');
      lane.className = 'track-lane';
      lane.setAttribute('aria-label', track.name);
      for (const clip of track.clips) {
        knownClipNames.set(clip.id, clip.label);
        const draftRemoved = editState?.operations?.some((op) => op.type === 'remove' && op.clipId === clip.id);
        const button = document.createElement('button');
        button.className = `clip${clip.hidden ? ' hidden-clip' : ''}${draftRemoved ? ' removed-clip' : ''}`;
        button.type = 'button';
        button.dataset.clipId = clip.id;
        button.setAttribute('aria-pressed', 'false');
        button.setAttribute('aria-label', `${track.name}, ${clip.label}, ${clock(clip.start)} to ${clock(clip.end)}${draftRemoved ? ', removed in draft' : clip.hidden ? ', hidden' : ''}`);
        button.title = `${clip.label}\n${clock(clip.start)} to ${clock(clip.end)}${clip.hidden ? '\nHidden' : ''}`;
        if (clip.thumbnail && track.kind === 'video') {
          const img = document.createElement('img');
          img.src = safeLocalUrl(clip.thumbnail);
          img.alt = '';
          img.draggable = false;
          img.loading = 'eager';
          img.addEventListener('error', () => { img.dataset.failed = 'true'; img.hidden = true; });
          button.append(img);
        }
        const text = document.createElement('span');
        text.className = 'clip-label';
        text.textContent = clip.label;
        const time = document.createElement('span');
        time.className = clip.hidden ? 'hidden-tag' : 'clip-time';
        time.textContent = draftRemoved ? 'Removed' : clip.hidden ? 'Hidden' : clock(clip.start, false);
        button.append(text, time);
        button.addEventListener('click', () => selectClip(clip, track));
        lane.append(button);
        clipElements.set(clip.id, button);
      }
      row.append(label, lane);
      fragment.append(row);
    }
    $('track-list').replaceChildren(fragment);
    updateLaneVisibility();
  }

  function updateLaneVisibility() {
    if (!project) return;
    const showParked = $('show-parked').checked;
    let visibleLanes = 0;
    let visibleClips = 0;
    let parkedClips = 0;
    for (const track of project.tracks) {
      const visible = !track.parked || showParked;
      trackElements.get(track.id).hidden = !visible;
      if (visible) {
        visibleLanes++;
        visibleClips += track.clips.length;
      }
      if (track.parked) parkedClips += track.clips.length;
    }
    $('project-summary').textContent = `${visibleLanes} lanes / ${visibleClips} clips${parkedClips ? ` / ${parkedClips} parked` : ''}`;
    if (selected?.track.parked && !showParked) {
      clipElements.get(selected.clip.id)?.setAttribute('aria-pressed', 'false');
      selected = null;
      $('selected-clip').hidden = true;
      delete $('selected-clip').dataset.clipId;
      $('empty-selection').hidden = false;
      $('selected-track').textContent = 'Select a clip';
    }
    searchClips();
    saveViewState();
  }

  function renderMixWaveform() {
    const waveform = project.waveform;
    const lane = $('mix-waveform');
    const draftAudio = Boolean(editState?.enabled && (editState.operations?.length || renderPending));
    document.querySelector('.mix-row .track-label strong').textContent = draftAudio ? 'Audio pending' : 'Final mix';
    lane.setAttribute('aria-valuemax', String(project.duration));
    if (draftAudio) {
      const message = document.createElement('span');
      message.className = 'waveform-empty';
      message.textContent = 'Fresh audio waveform pending render';
      lane.replaceChildren(message);
      return;
    }
    if (!waveform?.peaks?.length || !(waveform.step > 0)) {
      const message = document.createElement('span');
      message.className = 'waveform-empty';
      message.textContent = 'No waveform supplied';
      lane.replaceChildren(message);
      return;
    }
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${project.duration} 40`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-hidden', 'true');
    svg.classList.add('mix-waveform-chart');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const top = [];
    const bottom = [];
    const stride = Math.max(1, Math.ceil(waveform.peaks.length / 10000));
    for (let index = 0; index < waveform.peaks.length; index += stride) {
      let value = 0;
      for (let offset = index; offset < Math.min(index + stride, waveform.peaks.length); offset++) value = Math.max(value, waveform.peaks[offset]);
      const x = Math.min(project.duration, index * waveform.step);
      const amplitude = Math.max(0, Math.min(1, Number(value) || 0)) * 18;
      top.push(`${x.toFixed(3)},${(20 - amplitude).toFixed(2)}`);
      bottom.push(`${x.toFixed(3)},${(20 + amplitude).toFixed(2)}`);
    }
    path.setAttribute('d', `M${top.join(' L')} L${bottom.reverse().join(' L')} Z`);
    svg.append(path);
    lane.replaceChildren(svg);
  }

  function renderRuler() {
    const targetInterval = 75 / scale;
    const magnitude = Math.pow(10, Math.floor(Math.log10(targetInterval)));
    const interval = Math.max(0.001, [1, 2, 5, 10].map((factor) => factor * magnitude).find((step) => step >= targetInterval));
    const fragment = document.createDocumentFragment();
    const minorInterval = interval / 5;
    const firstSecond = Math.max(0, (scroller.scrollLeft - 150) / scale);
    const lastSecond = Math.min(project.duration, (scroller.scrollLeft + scroller.clientWidth - labelWidth() + 150) / scale);
    const firstTick = Math.floor(firstSecond / minorInterval);
    const lastTick = Math.floor(lastSecond / minorInterval);
    for (let index = firstTick; index <= lastTick; index++) {
      const second = index * minorInterval;
      const major = index % 5 === 0;
      const tick = document.createElement('span');
      tick.className = `ruler-tick${major ? '' : ' minor'}`;
      tick.style.left = `${second * scale}px`;
      if (major) tick.textContent = clock(second, interval < 1);
      fragment.append(tick);
    }
    ruler.replaceChildren(fragment);
  }

  function layoutTimeline() {
    if (!project) return;
    content.style.setProperty('--timeline-width', `${project.duration * scale}px`);
    for (const track of project.tracks) {
      for (const clip of track.clips) {
        const element = clipElements.get(clip.id);
        const width = Math.max(2, clip.duration * scale - 1);
        element.style.left = `${clip.start * scale}px`;
        element.style.width = `${width}px`;
        const thumbnail = element.querySelector('img');
        if (thumbnail) thumbnail.hidden = width < 6 || thumbnail.dataset.failed === 'true';
      }
    }
    renderRuler();
    renderRange();
    updateTime();
    $('zoom-out').disabled = scale <= fitScale + 0.001;
    $('zoom-in').disabled = scale >= maxScale();
    $('zoom-fit').setAttribute('aria-pressed', String(fitMode));
    document.body.dataset.zoom = scale.toFixed(6);
    const ratio = Math.max(1, maxScale() / fitScale);
    $('zoom-slider').value = ratio > 1 ? String(Math.round(Math.log(scale / fitScale) / Math.log(ratio) * 100)) : '0';
    $('zoom-slider').setAttribute('aria-valuetext', `${(scale / fitScale).toFixed(1)} times fit`);
    $('zoom-level').textContent = `${(scale / fitScale).toFixed(1)}x`;
  }

  function fitTimeline() {
    if (!project) return;
    fitScale = calculateFitScale();
    scale = fitScale;
    fitMode = true;
    scroller.scrollLeft = 0;
    layoutTimeline();
  }

  function setZoom(nextScale, pointerX = null) {
    if (!project) return;
    const available = scroller.clientWidth - labelWidth();
    const currentOffset = currentSeconds() * scale - scroller.scrollLeft;
    let anchorOffset;
    if (pointerX !== null) anchorOffset = clamp(pointerX - scroller.getBoundingClientRect().left - labelWidth(), 0, available);
    else anchorOffset = currentOffset >= 0 && currentOffset <= available ? currentOffset : available / 2;
    const anchorSecond = (scroller.scrollLeft + anchorOffset) / scale;
    scale = Math.min(maxScale(), Math.max(fitScale, nextScale));
    fitMode = scale <= fitScale + 0.001;
    manualPanUntil = performance.now() + 1200;
    layoutTimeline();
    scroller.scrollLeft = Math.max(0, anchorSecond * scale - anchorOffset);
    updateTime(false);
  }

  function zoom(multiplier) {
    setZoom(scale * multiplier);
  }

  function scrubToPointer(clientX) {
    const bounds = scroller.getBoundingClientRect();
    if (clientX > bounds.right - 12) scroller.scrollLeft += 18;
    else if (clientX < bounds.left + labelWidth() + 12) scroller.scrollLeft -= 18;
    seek((clientX - ruler.getBoundingClientRect().left) / scale);
  }

  function startScrub(event) {
    if (!project || event.button !== 0 || pan) return;
    event.preventDefault();
    const target = event.currentTarget;
    scrub = { pointerId: event.pointerId, target, resume: !video.paused };
    video.pause();
    target.focus({ preventScroll: true });
    target.setPointerCapture(event.pointerId);
    document.body.dataset.scrubbing = 'true';
    scrubToPointer(event.clientX);
  }

  function moveScrub(event) {
    if (scrub?.pointerId !== event.pointerId) return;
    event.preventDefault();
    scrubToPointer(event.clientX);
  }

  function stopScrub(event) {
    if (scrub?.pointerId !== event.pointerId) return;
    const previous = scrub;
    scrub = null;
    document.body.dataset.scrubbing = 'false';
    if (previous.target.hasPointerCapture(event.pointerId)) previous.target.releasePointerCapture(event.pointerId);
    if (previous.resume && event.type === 'pointerup') video.play().catch(() => fail('The preview could not resume. Press Play to try again.'));
  }

  function searchClips() {
    const query = $('clip-search').value.trim().toLowerCase();
    let matches = 0;
    let parkedMatches = 0;
    for (const track of project.tracks) for (const clip of track.clips) {
      const element = clipElements.get(clip.id);
      const match = `${clip.label} ${clip.sourceFilename || ''} ${clip.id} ${track.name}`.toLowerCase().includes(query);
      element.classList.toggle('search-dim', Boolean(query) && !match);
      element.classList.toggle('search-match', Boolean(query) && match);
      if (match) {
        if (!track.parked || $('show-parked').checked) matches++;
        else parkedMatches++;
      }
    }
    $('search-count').textContent = query ? `${matches} visible${parkedMatches ? ` / ${parkedMatches} parked` : ''}` : '';
  }

  function applyManifest(data, view = null, forceMovieReload = false) {
    const state = view || (project ? captureView() : storedView()) || {};
    const previousSelected = selected;
    const previousSource = video.getAttribute('src');
    const previousRevision = project?.revision;
    project = validateManifest(data);
    selected = null;
    $('project-title').textContent = project.title;
    document.title = `${project.title} | Madison`;
    $('frame-rate').textContent = `${project.fps} fps`;
    $('revision-label').textContent = `Revision ${project.revision || 'current'}`;
    ruler.setAttribute('aria-valuemax', String(project.duration));
    $('playhead').setAttribute('aria-valuemax', String(project.duration));
    pictureCuts = [...new Set([0, project.duration, ...project.tracks.filter((track) => track.kind === 'video' && !track.parked).flatMap((track) => track.clips.filter((clip) => !clip.hidden).flatMap((clip) => [clip.start, clip.end]))])].filter(Number.isFinite).sort((a, b) => a - b);
    $('show-parked').checked = Boolean(state.showParked);
    renderTracks();
    renderMixWaveform();
    renderNotes();
    fitScale = calculateFitScale();
    fitMode = state.fitMode !== false;
    scale = fitMode ? fitScale : clamp(Number(state.scale) || fitScale, fitScale, maxScale());
    setPreviewHeight(Number.isFinite(Number(state.previewHeight)) ? Number(state.previewHeight) : previewHeight);
    rangeStart = Number.isFinite(Number(state.rangeStart)) && state.rangeStart !== null ? clamp(Number(state.rangeStart), 0, project.duration) : null;
    rangeEnd = Number.isFinite(Number(state.rangeEnd)) && state.rangeEnd !== null ? clamp(Number(state.rangeEnd), 0, project.duration) : null;
    if (rangeStart !== null && rangeEnd !== null && rangeEnd <= rangeStart) rangeEnd = null;
    $('loop-range').checked = Boolean(state.loop && hasRange());
    if (typeof state.note === 'string') $('review-note').value = state.note;
    const rate = Number(state.playbackRate);
    if ([0.5, 1, 1.5, 2].includes(rate)) $('playback-rate').value = String(rate);
    video.playbackRate = Number($('playback-rate').value);
    pendingSeek = clamp(Number(state.time) || 0, 0, project.duration);
    layoutTimeline();
    scroller.scrollLeft = Math.max(0, Number(state.scrollLeft) || 0);
    scroller.scrollTop = Math.max(0, Number(state.scrollTop) || 0);
    renderRuler();
    if (state.selectedId) {
      let found = null;
      for (const track of project.tracks) {
        const clip = track.clips.find((entry) => entry.id === state.selectedId);
        if (clip) { found = { clip, track }; break; }
      }
      if (found) selectClip(found.clip, found.track, false);
      else if (previousSelected?.clip.id === state.selectedId && editState?.operations?.some((op) => op.type === 'remove' && op.clipId === state.selectedId)) selectClip(previousSelected.clip, previousSelected.track, false);
    }
    const nextSource = safeLocalUrl(project.videoUrl);
    video.poster = project.posterUrl ? safeLocalUrl(project.posterUrl) : '';
    if (forceMovieReload || previousSource !== nextSource || previousRevision !== project.revision) {
      video.pause();
      video.src = nextSource;
      mediaLoadStarted = false;
      if (state.playing) ensureMediaLoaded();
    } else if (video.readyState >= 1) {
      video.currentTime = pendingSeek;
      pendingSeek = null;
    }
    $('play-button').disabled = false;
    document.body.dataset.loaded = 'true';
    updateTime(false);
    renderEditUI();
    if (state.playing) {
      ensureMediaLoaded();
      video.play().catch(() => { $('refresh-status').textContent = 'Preview paused. Press Play to resume.'; });
    }
    saveViewState();
  }

  function applyEditState(result, view = null) {
    if (!result || result.enabled !== true || !result.manifest || !Array.isArray(result.operations) || typeof result.revision !== 'string') throw new Error('Invalid edit state from server');
    const wasPending = renderPending;
    editState = { ...editState, ...result, csrfToken: result.csrfToken || editState?.csrfToken };
    if (!editState.csrfToken) throw new Error('Editing token unavailable');
    renderPending = Boolean(result.renderPending);
    if (!result.operations.length) {
      baseClips.clear();
      for (const track of result.manifest.tracks || []) for (const clip of track.clips || []) baseClips.set(clip.id, { ...clip });
    }
    applyManifest(result.manifest, view, wasPending && !renderPending);
  }

  async function refreshTimeline(force = false) {
    if (refreshBusy || editBusy) return;
    refreshBusy = true;
    if (force) $('refresh-status').textContent = 'Checking for timeline changes…';
    try {
      const view = project ? captureView() : storedView();
      const response = await fetch('./edit-state', { cache: 'no-store' });
      let rebindMessage = '';
      if (response.ok) {
        const state = await response.json();
        if (state.enabled) {
          if (!editState) {
            try {
              const baseResponse = await fetch('./data.json', { cache: 'no-store' });
              if (baseResponse.ok) {
                const base = validateManifest(await baseResponse.json());
                baseClips.clear();
                for (const track of base.tracks) for (const clip of track.clips) baseClips.set(clip.id, { ...clip });
              }
            } catch { /* The bound session can still provide a draft picture. */ }
          }
          const changed = force || !editState || state.revision !== editState.revision || Boolean(state.renderPending) !== renderPending || state.manifest?.videoUrl !== project?.videoUrl || state.manifest?.revision !== project?.revision;
          if (changed) {
            if (editState && state.revision !== editState.revision && !force) { undoStates = []; redoStates = []; }
            applyEditState(state, view);
          }
          else editState = { ...editState, csrfToken: state.csrfToken || editState.csrfToken };
          if (force) $('refresh-status').textContent = changed ? 'Timeline refreshed at your position.' : 'Timeline is current.';
          return;
        }
        if (state.rebindRequired) rebindMessage = 'Review updated. Reconnect its matching Tesseract project before editing.';
      } else if (response.status !== 404) throw new Error(`Timeline check failed (HTTP ${response.status})`);
      if (editState) { editState = null; renderPending = false; undoStates = []; redoStates = []; }
      const manifestResponse = await fetch('./data.json', { cache: 'no-store' });
      if (!manifestResponse.ok) throw new Error(`Timeline check failed (HTTP ${manifestResponse.status})`);
      const data = validateManifest(await manifestResponse.json());
      const changed = force || !project || project.revision !== data.revision || project.videoUrl !== data.videoUrl || project.duration !== data.duration;
      if (changed) applyManifest(data, view);
      else { renderEditUI(); syncDraftPreview(); }
      if (rebindMessage) $('refresh-status').textContent = rebindMessage;
      else if (force) $('refresh-status').textContent = changed ? 'Timeline refreshed at your position.' : 'Timeline is current.';
    } catch (error) {
      $('refresh-status').textContent = error.message || 'Refresh failed';
      if (!project) { fail('Cannot load the timeline. Start the review server and check the review bundle.'); $('project-title').textContent = 'Timeline unavailable'; }
    } finally { refreshBusy = false; }
  }

  function capcutSyncMessage(message) {
    $('capcut-sync-status').textContent = message;
  }

  function capcutSyncReady() {
    return Boolean(capcutSyncState?.canSync && capcutSyncState.sourceHash && capcutSyncState.csrfToken && !capcutSyncBusy && !editBusy && !editState?.operations?.length && (!capcutSyncState.requiresLossy || $('capcut-sync-lossy').checked));
  }

  function updateCapcutSyncButton() {
    $('capcut-sync-confirm').disabled = !capcutSyncReady();
    $('capcut-sync-open').textContent = capcutSyncBusy ? 'CapCut sync in progress' : 'Sync from CapCut';
  }

  function addCapcutSyncItem(list, value) {
    const item = document.createElement('li');
    item.textContent = String(value);
    list.appendChild(item);
  }

  function renderCapcutSyncReview(state) {
    capcutSyncState = state;
    $('capcut-sync-lossy').checked = false;
    const name = [state.projectName, state.timelineName].filter((part) => typeof part === 'string' && part).join(' · ');
    $('capcut-sync-project').textContent = name || 'Selected CapCut project';
    const diff = state.diff || {};
    const counts = [
      ['added', 'added'], ['removed', 'removed'], ['moved', 'moved'], ['trimmed', 'trimmed'], ['changed', 'changed'],
    ].map(([key, label]) => `${Number(diff[key]) || 0} ${label}`);
    const before = Number(diff.previousDuration);
    const after = Number(diff.currentDuration);
    const duration = diff.previousDuration !== null && diff.previousDuration !== undefined && Number.isFinite(before) && Number.isFinite(after) ? ` Previous length ${clock(before)}; new length ${clock(after)}.` : Number.isFinite(after) ? ` New length ${clock(after)}.` : '';
    $('capcut-sync-summary').textContent = `${counts.join(' · ')}.${duration}`;
    const examples = $('capcut-sync-examples');
    examples.replaceChildren();
    for (const example of Array.isArray(diff.examples) ? diff.examples.slice(0, 8) : []) addCapcutSyncItem(examples, example);
    const warnings = $('capcut-sync-warning-list');
    warnings.replaceChildren();
    const unsupported = Array.isArray(state.unsupported) ? state.unsupported : [];
    const missing = Array.isArray(state.missingMedia) ? state.missingMedia : [];
    if (unsupported.length) {
      const affectedCount = unsupported.reduce((total, issue) => total + (Number.isSafeInteger(issue.count) && issue.count > 0 ? issue.count : 1), 0);
      addCapcutSyncItem(warnings, `${affectedCount} active CapCut effects or settings may change or be lost in Tesseract.`);
      const groups = new Map();
      for (const issue of unsupported) {
        const code = typeof issue.code === 'string' && issue.code ? issue.code.replace(/[_-]+/g, ' ') : 'Other setting';
        const detail = typeof issue.detail === 'string' && issue.detail ? issue.detail : code;
        const group = groups.get(code) || { detail, count: 0 };
        group.count += Number.isSafeInteger(issue.count) && issue.count > 0 ? issue.count : 1;
        groups.set(code, group);
      }
      for (const [, group] of [...groups].sort((a, b) => b[1].count - a[1].count).slice(0, 6)) addCapcutSyncItem(warnings, `${group.detail}: ${group.count} affected item${group.count === 1 ? '' : 's'}`);
      if (groups.size > 6) addCapcutSyncItem(warnings, `${groups.size - 6} more effect or setting types.`);
    }
    if (missing.length) {
      addCapcutSyncItem(warnings, `${missing.length} source media item${missing.length === 1 ? ' is' : 's are'} missing. Sync cannot continue until they are available.`);
      for (const issue of missing.slice(0, 5)) addCapcutSyncItem(warnings, `Missing: ${issue.name || 'clip'}`);
      if (missing.length > 5) addCapcutSyncItem(warnings, `${missing.length - 5} more missing items.`);
    }
    $('capcut-sync-warnings').hidden = warnings.childElementCount === 0;
    $('capcut-sync-lossy-row').hidden = !state.requiresLossy;
    if (editState?.operations?.length) capcutSyncMessage('Apply or undo your queued Madison changes before syncing from CapCut.');
    else if (missing.length) capcutSyncMessage('Sync is blocked until the missing source media is available.');
    else if (!state.canSync) capcutSyncMessage('No new CapCut changes are ready to sync.');
    else if (state.requiresLossy) capcutSyncMessage('Review the warnings and acknowledge them before syncing.');
    else capcutSyncMessage('Ready to sync these saved changes.');
    updateCapcutSyncButton();
  }

  async function fetchCapcutSyncState() {
    const response = await fetch('./capcut-sync-state', { cache: 'no-store' });
    if (!response.ok) throw new Error(`CapCut sync check failed (HTTP ${response.status})`);
    const state = await response.json();
    if (!state || typeof state !== 'object' || state.enabled !== true) throw new Error('CapCut sync is not connected.');
    return state;
  }

  async function checkCapcutSyncAvailability() {
    try {
      const response = await fetch('./capcut-sync-state', { cache: 'no-store' });
      const state = await response.json();
      $('capcut-sync-open').hidden = state?.enabled !== true;
      renderEditUI();
      if (state?.enabled !== true || !response.ok) return;
      if (!capcutSyncBusy) capcutSyncState = state;
      try {
        const response = await fetch('./capcut-sync-status', { cache: 'no-store' });
        if (response.ok && (await response.json()).state === 'running') {
          capcutSyncBusy = true;
          updateCapcutSyncButton();
          pollCapcutSyncStatus();
        }
      } catch { /* A status check can be retried when the review is opened. */ }
    } catch {
      if (!capcutSyncBusy) $('capcut-sync-open').hidden = true;
    }
  }

  async function openCapcutSyncReview() {
    if (!$('capcut-sync-dialog').open) $('capcut-sync-dialog').showModal();
    $('capcut-sync-close').focus();
    if (capcutSyncBusy) { capcutSyncMessage('Checking CapCut sync progress…'); return; }
    capcutSyncState = null;
    $('capcut-sync-project').textContent = '';
    $('capcut-sync-summary').textContent = '';
    $('capcut-sync-examples').replaceChildren();
    $('capcut-sync-warning-list').replaceChildren();
    $('capcut-sync-warnings').hidden = true;
    $('capcut-sync-lossy-row').hidden = true;
    capcutSyncMessage('Checking the saved CapCut project…');
    $('capcut-sync-confirm').disabled = true;
    try {
      const statusResponse = await fetch('./capcut-sync-status', { cache: 'no-store' });
      if (statusResponse.ok && (await statusResponse.json()).state === 'running') {
        capcutSyncBusy = true;
        updateCapcutSyncButton();
        pollCapcutSyncStatus();
        return;
      }
      renderCapcutSyncReview(await fetchCapcutSyncState());
    } catch (error) {
      capcutSyncState = null;
      capcutSyncMessage(error.message || 'Cannot check CapCut changes.');
      updateCapcutSyncButton();
    }
  }

  async function pollCapcutSyncStatus() {
    if (!capcutSyncBusy) return;
    try {
      const response = await fetch('./capcut-sync-status', { cache: 'no-store' });
      if (!response.ok) throw new Error(`Status check failed (HTTP ${response.status})`);
      const status = await response.json();
      if (status.state === 'complete') {
        capcutSyncBusy = false;
        clearTimeout(capcutSyncPoll);
        capcutSyncState = null;
        updateCapcutSyncButton();
        capcutSyncMessage('CapCut sync complete. Reloading the viewer at your position…');
        await refreshTimeline(true);
        if (String(status.document || '').includes('rebind required')) {
          capcutSyncMessage('The new review is ready at your position. Madison editing needs its Tesseract project reconnected.');
        } else {
          capcutSyncMessage(capcutSyncWasLossy ? 'CapCut sync complete. The viewer is updated at your position. Some effects or audio may differ from CapCut.' : 'CapCut sync complete. The viewer is updated at your position.');
        }
        return;
      }
      if (status.state === 'failed') {
        capcutSyncBusy = false;
        clearTimeout(capcutSyncPoll);
        capcutSyncState = null;
        updateCapcutSyncButton();
        capcutSyncMessage(`CapCut sync failed. ${status.error || 'The previous viewer remains available.'}`);
        return;
      }
      const stageNames = { inspecting: 'Checking changes', importing: 'Importing clips', preparing: 'Preparing the preview', activating: 'Updating the viewer' };
      capcutSyncMessage(`${stageNames[status.stage] || 'Syncing from CapCut'}… The previous preview remains available.`);
    } catch {
      capcutSyncMessage('Checking sync progress. The previous viewer remains available.');
    }
    capcutSyncPoll = setTimeout(pollCapcutSyncStatus, 1500);
  }

  async function startCapcutSync() {
    if (!capcutSyncReady()) return;
    const state = capcutSyncState;
    capcutSyncWasLossy = Boolean(state.requiresLossy);
    capcutSyncBusy = true;
    updateCapcutSyncButton();
    capcutSyncMessage('Starting CapCut sync…');
    try {
      const response = await fetch('./capcut-sync-start', {
        method: 'POST', cache: 'no-store', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Madison-Edit-Token': state.csrfToken },
        body: JSON.stringify({ expectedSourceHash: state.sourceHash, allowLossy: Boolean(state.requiresLossy && $('capcut-sync-lossy').checked) }),
      });
      if (response.status === 409) throw new Error('The saved CapCut project changed or sync is not ready. Close and reopen this panel to review it again.');
      if (!response.ok) throw new Error(`CapCut sync could not start (HTTP ${response.status}). Close and reopen this panel to try again.`);
      const result = await response.json();
      if (result.state !== 'running') throw new Error('Sync did not start. Review the project again.');
      pollCapcutSyncStatus();
    } catch (error) {
      capcutSyncBusy = false;
      capcutSyncState = null;
      updateCapcutSyncButton();
      capcutSyncMessage(error.message || 'CapCut sync could not start.');
    }
  }

  async function commitEdits() {
    if (!editState?.enabled || editBusy || !editState.operations?.length) return;
    editBusy = true;
    renderEditUI();
    $('edit-status').textContent = 'Applying changes to Tesseract…';
    try {
      const response = await fetch('./edit-commit', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json', 'X-Madison-Edit-Token': editState.csrfToken }, body: JSON.stringify({ revision: editState.revision }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Apply failed (HTTP ${response.status})`);
      undoStates = [];
      redoStates = [];
      applyEditState({ ...result, enabled: true, operations: result.operations || [] });
      $('edit-status').textContent = `Saved to Tesseract. ${result.renderPending ? 'Fresh picture and audio are pending.' : 'Preview is current.'}`;
    } catch (error) { $('edit-status').textContent = `${error.message || 'Apply failed'}. The queued edits remain available.`; }
    finally { editBusy = false; renderEditUI(); }
  }

  async function openPopout() {
    if (!hasDocumentPip) {
      if (!hasNativePip) return;
      if (stage.dataset.draft === 'true' && sourceVideo.hidden) { $('popout-status').textContent = 'Picture preview is pending a fresh render.'; return; }
      const target = stage.dataset.draft === 'true' && !sourceVideo.hidden ? sourceVideo : video;
      if (target.readyState < 1) { $('popout-status').textContent = 'Play the preview first, then pop it out.'; return; }
      try {
        await target.requestPictureInPicture();
        $('popout-status').textContent = 'Browser popout is open. Its controls may dim on hover.';
      } catch { $('popout-status').textContent = 'This browser could not open picture in picture.'; }
      return;
    }
    if (popoutWindow) { popoutWindow.focus(); return; }
    try {
      const pip = await window.documentPictureInPicture.requestWindow({ width: 540, height: 400 });
      popoutWindow = pip;
      popoutHost = stage.parentElement;
      const style = pip.document.createElement('style');
      style.textContent = 'html,body{margin:0;height:100%;background:#101317;color:#f2f5f8;font:13px system-ui}body{display:flex;flex-direction:column}.video-stage{position:relative;flex:1;min-height:0;background:#07090b;display:flex;align-items:center;justify-content:center}.video-stage video{display:block;width:100%;height:100%;object-fit:contain}.video-stage #source-video{position:absolute;inset:0;background:#07090b}.video-stage[data-draft="true"] #review-video{visibility:hidden}.draft-preview-message{position:absolute;left:8px;right:8px;bottom:8px;padding:5px 8px;background:#142027ee;color:#fff;border-radius:4px;pointer-events:none}video[hidden],p[hidden]{display:none!important}.pip-controls{display:flex;align-items:center;gap:8px;padding:8px;background:#1b222b}.pip-controls button{background:#315b56;color:#fff;border:1px solid #588b83;border-radius:4px;padding:6px 12px;cursor:pointer}.pip-controls input{flex:1;min-width:0;accent-color:#79d6cc}.pip-controls output{font-variant-numeric:tabular-nums;white-space:nowrap}';
      pip.document.head.append(style);
      pip.document.body.append(stage);
      const controls = pip.document.createElement('div');
      controls.className = 'pip-controls';
      const play = pip.document.createElement('button');
      play.id = 'pip-play';
      play.type = 'button';
      play.textContent = video.paused ? 'Play' : 'Pause';
      play.addEventListener('click', togglePlayback);
      const seekInput = pip.document.createElement('input');
      seekInput.id = 'pip-seek';
      seekInput.type = 'range';
      seekInput.min = '0';
      seekInput.max = String(project.duration);
      seekInput.step = '0.001';
      seekInput.value = String(currentSeconds());
      seekInput.setAttribute('aria-label', 'Movie position');
      seekInput.addEventListener('input', () => seek(Number(seekInput.value)));
      const time = pip.document.createElement('output');
      time.id = 'pip-time';
      time.textContent = `${clock(currentSeconds())} / ${clock(project.duration)}`;
      controls.append(play, seekInput, time);
      pip.document.body.append(controls);
      video.controls = false;
      pip.addEventListener('pagehide', () => {
        if (popoutHost) popoutHost.append(stage);
        popoutHost = null;
        popoutWindow = null;
        $('popout-status').textContent = '';
        syncDraftPreview();
      }, { once: true });
      $('popout-status').textContent = 'Preview is in its own window.';
    } catch { $('popout-status').textContent = 'This browser could not open the preview window.'; }
  }

  $('play-button').addEventListener('click', togglePlayback);
  $('refresh-timeline').addEventListener('click', () => refreshTimeline(true));
  $('capcut-sync-open').addEventListener('click', openCapcutSyncReview);
  $('capcut-sync-close').addEventListener('click', () => $('capcut-sync-dialog').close());
  $('capcut-sync-confirm').addEventListener('click', startCapcutSync);
  $('capcut-sync-lossy').addEventListener('change', updateCapcutSyncButton);
  $('remove-clip').addEventListener('click', () => { if (selected) setClipOperation({ type: 'remove', clipId: selected.clip.id }); });
  $('restore-clip').addEventListener('click', () => { if (selected) replaceOperations(editState.operations.filter((op) => !(op.type === 'remove' && op.clipId === selected.clip.id))); });
  $('trim-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!selected) return;
    const start = Number($('trim-start').value);
    const end = Number($('trim-end').value);
    const base = baseClips.get(selected.clip.id) || selected.clip;
    if (base.speed !== 1 || selected.clip.speed !== 1) { $('clip-edit-status').textContent = 'This clip uses changing speed. Trim it in Tesseract.'; return; }
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < base.start || end <= start || end > base.end) { $('clip-edit-status').textContent = 'Enter a start and end inside the original clip.'; return; }
    const sourceStart = base.sourceStart + start - base.start;
    const sourceEnd = base.sourceEnd - (base.end - end);
    if (sourceStart < 0 || sourceEnd <= sourceStart) { $('clip-edit-status').textContent = 'That trim is outside the source footage.'; return; }
    $('clip-edit-status').textContent = '';
    setClipOperation({ type: 'trim', clipId: selected.clip.id, start, end, sourceStart, sourceEnd });
  });
  $('volume-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!selected) return;
    const volume = Number($('clip-volume').value);
    if (!Number.isFinite(volume) || volume < 0 || volume > 2) { $('clip-edit-status').textContent = 'Enter a volume from 0 to 2.'; return; }
    $('clip-edit-status').textContent = '';
    setClipOperation({ type: 'volume', clipId: selected.clip.id, volume });
  });
  $('mute-clip').addEventListener('click', () => { if (selected) setClipOperation({ type: 'volume', clipId: selected.clip.id, volume: 0 }); });
  $('undo-edit').addEventListener('click', () => { if (undoStates.length) replaceOperations(undoStates.at(-1), 'undo'); });
  $('redo-edit').addEventListener('click', () => { if (redoStates.length) replaceOperations(redoStates.at(-1), 'redo'); });
  $('apply-edits').addEventListener('click', commitEdits);
  video.disablePictureInPicture = hasDocumentPip;
  sourceVideo.disablePictureInPicture = hasDocumentPip;
  if (hasDocumentPip || hasNativePip) $('popout-preview').hidden = false;
  else { $('popout-preview').hidden = false; $('popout-preview').disabled = true; $('popout-preview').textContent = 'Popout unavailable in this browser'; }
  $('popout-preview').addEventListener('click', openPopout);
  for (const element of [video, sourceVideo]) element.addEventListener('leavepictureinpicture', () => { $('popout-status').textContent = ''; });
  $('copy-reference').addEventListener('click', copyReference);
  $('zoom-in').addEventListener('click', () => zoom(1.7));
  $('zoom-out').addEventListener('click', () => zoom(1 / 1.7));
  $('zoom-fit').addEventListener('click', fitTimeline);
  $('zoom-slider').addEventListener('input', () => {
    if (project) setZoom(fitScale * Math.pow(maxScale() / fitScale, Number($('zoom-slider').value) / 100));
  });
  $('frame-back').addEventListener('click', () => stepFrame(-1));
  $('frame-forward').addEventListener('click', () => stepFrame(1));
  $('previous-cut').addEventListener('click', () => goToCut(-1));
  $('next-cut').addEventListener('click', () => goToCut(1));
  $('timecode-form').addEventListener('submit', jumpToTime);
  $('timecode-input').addEventListener('input', () => {
    timecodeDirty = true;
    $('timecode-input').removeAttribute('aria-invalid');
    $('timecode-status').textContent = '';
  });
  $('playback-rate').addEventListener('change', () => { video.playbackRate = Number($('playback-rate').value); syncDraftPreview(); saveViewState(); });
  $('review-note').addEventListener('input', saveViewState);
  $('mark-in').addEventListener('click', () => markRange('in'));
  $('mark-out').addEventListener('click', () => markRange('out'));
  $('clear-range').addEventListener('click', () => { rangeStart = null; rangeEnd = null; renderRange(); saveViewState(); });
  $('loop-range').addEventListener('change', () => {
    if ($('loop-range').checked && hasRange() && (currentSeconds() >= rangeEnd || currentSeconds() < rangeStart)) seek(rangeStart);
  });
  $('copy-feedback').addEventListener('click', copyFeedback);
  $('clip-search').addEventListener('input', () => { if (project) searchClips(); });
  $('show-parked').addEventListener('change', updateLaneVisibility);
  addEventListener('pagehide', saveViewNow);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') saveViewNow(); });
  for (const element of [ruler, $('mix-waveform'), $('playhead')]) {
    element.addEventListener('pointerdown', startScrub);
    element.addEventListener('pointermove', moveScrub);
    element.addEventListener('pointerup', stopScrub);
    element.addEventListener('pointercancel', stopScrub);
    element.addEventListener('lostpointercapture', stopScrub);
    element.addEventListener('keydown', (event) => {
      if (project && (event.key === 'Home' || event.key === 'End')) {
        event.preventDefault();
        video.pause();
        seek(event.key === 'Home' ? 0 : project.duration);
        revealCurrentTime();
      }
    });
  }
  scroller.addEventListener('wheel', (event) => {
    if (!project) return;
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? scroller.clientHeight : 1);
      setZoom(scale * Math.exp(-clamp(delta, -500, 500) * 0.003), event.clientX);
    } else if (event.shiftKey) {
      event.preventDefault();
      manualPanUntil = performance.now() + 1500;
      const delta = event.deltaX || event.deltaY;
      scroller.scrollLeft += delta * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? scroller.clientWidth : 1);
    }
  }, { passive: false });
  scroller.addEventListener('scroll', () => {
    updateTime(false);
    if (rulerAnimation !== null) return;
    rulerAnimation = requestAnimationFrame(() => {
      rulerAnimation = null;
      if (project) renderRuler();
    });
  }, { passive: true });
  scroller.addEventListener('pointerdown', (event) => {
    if (event.button !== 1 || !project) return;
    event.preventDefault();
    pan = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: scroller.scrollLeft, top: scroller.scrollTop };
    scroller.setPointerCapture(event.pointerId);
    document.body.dataset.panning = 'true';
  });
  scroller.addEventListener('pointermove', (event) => {
    if (pan?.pointerId !== event.pointerId) return;
    event.preventDefault();
    manualPanUntil = performance.now() + 1500;
    scroller.scrollLeft = pan.left + pan.x - event.clientX;
    scroller.scrollTop = pan.top + pan.y - event.clientY;
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) scroller.addEventListener(name, (event) => {
    if (pan?.pointerId !== event.pointerId) return;
    pan = null;
    document.body.dataset.panning = 'false';
    if (scroller.hasPointerCapture(event.pointerId)) scroller.releasePointerCapture(event.pointerId);
  });
  scroller.addEventListener('auxclick', (event) => { if (event.button === 1) event.preventDefault(); });
  const divider = $('preview-divider');
  divider.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    dividerDrag = { pointerId: event.pointerId, y: event.clientY, height: previewHeight };
    divider.focus({ preventScroll: true });
    divider.setPointerCapture(event.pointerId);
    document.body.dataset.resizing = 'true';
  });
  divider.addEventListener('pointermove', (event) => {
    if (dividerDrag?.pointerId !== event.pointerId) return;
    setPreviewHeight(dividerDrag.height + event.clientY - dividerDrag.y);
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) divider.addEventListener(name, (event) => {
    if (dividerDrag?.pointerId !== event.pointerId) return;
    dividerDrag = null;
    document.body.dataset.resizing = 'false';
    if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
  });
  divider.addEventListener('dblclick', resetPreviewHeight);
  divider.addEventListener('keydown', (event) => {
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const bounds = previewBounds();
    if (event.key === 'Home') setPreviewHeight(bounds.min);
    else if (event.key === 'End') setPreviewHeight(bounds.max);
    else setPreviewHeight(previewHeight + (event.key === 'ArrowDown' ? 1 : -1) * (event.shiftKey ? 48 : 16));
  });
  addEventListener('resize', () => setPreviewHeight(previewHeight));
  document.addEventListener('keydown', (event) => {
    if (!project || event.defaultPrevented || /INPUT|TEXTAREA|SELECT|VIDEO/.test(event.target.tagName) || event.target.isContentEditable || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.code === 'Space' && event.target.tagName !== 'BUTTON') {
      event.preventDefault();
      togglePlayback();
    }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      if (event.target.closest('.zoom-controls')) return;
      event.preventDefault();
      video.pause();
      const delta = event.shiftKey ? 5 : 1 / project.fps;
      seek((pendingSeek ?? video.currentTime) + (event.key === 'ArrowRight' ? delta : -delta));
      revealCurrentTime();
    }
    if (event.key.toLowerCase() === 'i' || event.key.toLowerCase() === 'o') {
      event.preventDefault();
      markRange(event.key.toLowerCase() === 'i' ? 'in' : 'out');
    }
  });
  video.addEventListener('loadedmetadata', () => {
    mediaLoadStarted = true;
    $('play-button').disabled = false;
    video.playbackRate = Number($('playback-rate').value);
    if (pendingSeek !== null) seek(pendingSeek);
    updateTime();
  });
  sourceVideo.addEventListener('loadedmetadata', syncDraftPreview);
  sourceVideo.addEventListener('error', () => { if (sourceVideo.currentSrc) failedSourceUrls.add(sourceVideo.currentSrc); if (stage.dataset.draft === 'true') { sourceVideo.hidden = true; previewMessage.textContent = 'Source preview unavailable. Picture and audio pending a fresh render.'; } });
  video.addEventListener('error', () => {
    $('play-button').disabled = true;
    fail('The preview movie could not be loaded. Check videoUrl, confirm that the media file exists, and use a video format supported by your browser. The timeline remains available.');
  });
  ['play', 'pause', 'ended'].forEach((event) => video.addEventListener(event, syncPlayback));
  video.addEventListener('ended', () => {
    if ($('loop-range').checked && hasRange()) {
      seek(rangeStart);
      video.play().catch(() => fail('The preview could not repeat the range. Press Play to try again.'));
    }
  });
  ['timeupdate', 'seeked'].forEach((event) => video.addEventListener(event, updateTime));
  new ResizeObserver(() => {
    if (!project) return;
    if (fitMode) fitTimeline();
    else {
      fitScale = calculateFitScale();
      scale = clamp(scale, fitScale, maxScale());
      layoutTimeline();
    }
  }).observe(scroller);

  async function init() { await refreshTimeline(); await checkCapcutSyncAvailability(); }
  resetPreviewHeight();
  init();
  setInterval(() => { if (project && document.visibilityState === 'visible') refreshTimeline(); }, 5000);
})();
