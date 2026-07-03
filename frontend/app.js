const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const propertiesPanel = document.getElementById('properties');
const statusText = document.getElementById('statusText');
const statusMeta = document.getElementById('statusMeta');
const coordReadout = document.getElementById('coordReadout');
const measureReadout = document.getElementById('measureReadout');
const toolHint = document.getElementById('toolHint');
const toast = document.getElementById('toast');
const loadSelect = document.getElementById('loadSelect');
const loadBtn = document.getElementById('loadBtn');
const assignStoryBtn = document.getElementById('assignStoryBtn');
const newBtn = document.getElementById('newBtn');
const deleteBtn = document.getElementById('deleteBtn');
const saveBtn = document.getElementById('saveBtn');
const undoBtn = document.getElementById('undoBtn');
const redoBtn = document.getElementById('redoBtn');
const snapToggle = document.getElementById('snapToggle');
const orthoToggle = document.getElementById('orthoToggle');
const gridSizeInput = document.getElementById('gridSizeInput');
const bgRemoveBtn = document.getElementById('bgRemoveBtn');
const jsonModal = document.getElementById('jsonModal');
const jsonEditor = document.getElementById('jsonEditor');
const jsonApplyBtn = document.getElementById('jsonApplyBtn');
const jsonResetBtn = document.getElementById('jsonResetBtn');
const jsonCloseBtn = document.getElementById('jsonCloseBtn');
const zoomInBtn = document.getElementById('zoomInBtn');
const zoomOutBtn = document.getElementById('zoomOutBtn');
const zoomFitBtn = document.getElementById('zoomFitBtn');
const zoomResetBtn = document.getElementById('zoomResetBtn');
const zoomLevel = document.getElementById('zoomLevel');
const previewBtn = document.getElementById('previewBtn');
const previewModal = document.getElementById('previewModal');
const previewImg = document.getElementById('previewImg');
const previewStatus = document.getElementById('previewStatus');
const previewRefreshBtn = document.getElementById('previewRefreshBtn');
const previewSaveRefreshBtn = document.getElementById('previewSaveRefreshBtn');
const previewCloseBtn = document.getElementById('previewCloseBtn');
const previewAutoRefresh = document.getElementById('previewAutoRefresh');
const helpBtn = document.getElementById('helpBtn');
const helpModal = document.getElementById('helpModal');
const helpCloseBtn = document.getElementById('helpCloseBtn');

const toolButtons = Array.from(document.querySelectorAll('.tool-button'));
const floorTabs = Array.from(document.querySelectorAll('.tab'));

const state = {
  tool: 'select',
  storyId: 'floor1',
  floorId: 'floor1',
  floorplans: {
    floor1: null,
    floor2: null,
  },
  storyAssignments: {
    floor1: null,
    floor2: null,
  },
  snapToGrid: true,
  orthogonalSnap: true,
  gridSize: 25,
  view: { x: 40, y: 40, scale: 1 },
  viewport: { width: 0, height: 0, dpr: 1 },
  drawing: null,
  selected: null,
  multiSelected: [],
  selectionBox: null,
  dragging: null,
  // Undo/redo stacks are kept per floorplan id so switching floors never
  // applies one floor's history to another.
  histories: {},
  dirtyFloors: new Set(),
  spacePressed: false,

  // Background Image State
  backgroundImage: null,
  background: { x: 0, y: 0, scale: 1.0, opacity: 0.5 },
  haStates: {},
  haPollInterval: null,
};

const storyLabels = {
  floor1: 'First Story',
  floor2: 'Second Story',
};

const TOOL_HINTS = {
  select: '<b>Select</b> — drag items to move • drag empty space to box-select • Shift+drag a wall to stretch it',
  wall: '<b>Wall</b> — click to add points • <b>Enter</b> or double-click to finish • <b>Esc</b> removes last point',
  door: '<b>Door</b> — press and drag across a wall opening, then release',
  sensor: '<b>Sensor</b> — click to place, then bind an entity in the side panel',
  thermostat: '<b>Thermostat</b> — click to place, then bind entities in the side panel',
  room_label: '<b>Room Label</b> — click to place text',
  stairwell: '<b>Stairwell</b> — click polygon points • <b>Enter</b> or double-click to close',
  scale: '<b>Scale</b> — click two points, then enter the real-world distance in meters',
  erase: '<b>Erase</b> — click an item to delete it',
};

const TOOL_KEYS = {
  v: 'select',
  w: 'wall',
  d: 'door',
  s: 'sensor',
  t: 'thermostat',
  r: 'room_label',
  a: 'stairwell',
  c: 'scale',
  e: 'erase',
};

const MIN_ZOOM = 0.1;
const MAX_ZOOM = 8;

// Defaults are kept in sync with backend/models.py so a floorplan created in
// the editor renders the same as one created server-side.
const defaultRender = () => ({
  temp_range_f: { min: 60, max: 80 },
  overlay_alpha: 0.6,
  scale_min_mode: 'absolute',
  scale_max_mode: 'absolute',
  auto_crop: true,
  crop_padding: 30,
  exterior_margin: 20,
  show_walls: true,
  show_labels: true,
  show_legend: true,
  legend_colors: null,
  show_timestamp: true,
  show_outside_temp: true,
  outside_temp_label: 'Outside',
  outside_temp_entity: '',
  outside_temp_f: null,
  show_chart: false,
  chart_temp_entity: '',
  chart_forecast_entity: '',
  chart_history_hours: 12,
  chart_forecast_hours: 12,
  chart_width: 260,
  chart_height: 80,
  text_font_size: null,
});

const defaultSolver = () => ({
  grid_w: 400,
  grid_h: 250,
  iterations: 500,
  sensor_pull: 1.0,
  wall_resistance: 500000,
  default_passage_resistance: 2,
});

function createDefaultFloorplan(floorId) {
  return {
    version: 1,
    floor_id: floorId,
    canvas: { width: 1600, height: 1000 },
    scale: {
      mode: 'calibrated',
      px_per_meter: 100,
      calibration: { p1: [0, 0], p2: [100, 0], distance_m: 1 },
    },
    walls: [],
    doors: [],
    sensors: [],
    thermostats: [],
    room_labels: [],
    stairwell: null,
    render: defaultRender(),
    solver: defaultSolver(),
  };
}

function deepCopy(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function setStatus(text, isError = false) {
  statusText.textContent = text;
  statusText.classList.toggle('danger', isError);
}

function updateStatusMeta() {
  const storyLabel = storyLabels[state.storyId] || state.storyId;
  statusMeta.textContent = `${storyLabel} • ${state.floorId} • Grid ${state.gridSize}px`;
}

function updateCoordReadout(point) {
  if (!coordReadout) return;
  if (!point) {
    coordReadout.textContent = '';
    return;
  }
  coordReadout.textContent = `X ${point[0].toFixed(1)}  Y ${point[1].toFixed(1)}`;
}

function updateMeasureReadout(text) {
  if (!measureReadout) return;
  measureReadout.textContent = text || '';
}

function formatSegmentMeasure(a, b) {
  const px = pointDistance(a, b);
  const fp = currentFloorplan();
  const pxPerMeter = fp?.scale?.px_per_meter;
  if (pxPerMeter && pxPerMeter > 0) {
    return `${px.toFixed(0)} px • ${(px / pxPerMeter).toFixed(2)} m`;
  }
  return `${px.toFixed(0)} px`;
}

let toastTimer = null;

function showToast(message, variant = 'info') {
  if (!toast) {
    return;
  }
  toast.textContent = message;
  toast.classList.toggle('error', variant === 'error');
  toast.classList.add('visible');
  if (toastTimer) {
    window.clearTimeout(toastTimer);
  }
  toastTimer = window.setTimeout(() => {
    toast.classList.remove('visible');
  }, 3200);
}

function isEditableTarget(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName ? target.tagName.toLowerCase() : '';
  return tag === 'input' || tag === 'textarea' || tag === 'select';
}

function updateBackgroundControls() {
  if (bgRemoveBtn) {
    bgRemoveBtn.disabled = !state.backgroundImage;
  }
}

function updateToolHint() {
  if (!toolHint) return;
  toolHint.innerHTML = TOOL_HINTS[state.tool] || '';
}

// ---------------------------------------------------------------------------
// History / dirty tracking
// ---------------------------------------------------------------------------

function historyFor(floorId = state.floorId) {
  if (!state.histories[floorId]) {
    state.histories[floorId] = { past: [], future: [] };
  }
  return state.histories[floorId];
}

function resetHistory(floorId) {
  state.histories[floorId] = { past: [], future: [] };
  updateHistoryButtons();
}

function updateHistoryButtons() {
  const history = historyFor();
  if (undoBtn) undoBtn.disabled = !history.past.length;
  if (redoBtn) redoBtn.disabled = !history.future.length;
}

function setDirty(floorId, dirty) {
  if (dirty) {
    state.dirtyFloors.add(floorId);
  } else {
    state.dirtyFloors.delete(floorId);
  }
  updateSaveIndicator();
}

function updateSaveIndicator() {
  if (saveBtn) {
    saveBtn.classList.toggle('dirty', state.dirtyFloors.has(state.floorId));
  }
}

function pushHistorySnapshot(snapshot) {
  const history = historyFor();
  history.past.push(snapshot);
  if (history.past.length > 50) {
    history.past.shift();
  }
  history.future = [];
  setDirty(state.floorId, true);
  updateHistoryButtons();
}

function pushHistory() {
  pushHistorySnapshot(deepCopy(currentFloorplan()));
}

function undo() {
  const history = historyFor();
  if (!history.past.length) {
    return;
  }
  const previous = history.past.pop();
  history.future.push(deepCopy(currentFloorplan()));
  setFloorplan(previous);
  setDirty(state.floorId, true);
  updateHistoryButtons();
}

function redo() {
  const history = historyFor();
  if (!history.future.length) {
    return;
  }
  const next = history.future.pop();
  history.past.push(deepCopy(currentFloorplan()));
  setFloorplan(next);
  setDirty(state.floorId, true);
  updateHistoryButtons();
}

function currentFloorplan() {
  return state.floorplans[state.floorId];
}

function setFloorplan(fp) {
  fp.floor_id = state.floorId;
  state.floorplans[state.floorId] = fp;
  state.selected = null;
  state.multiSelected = [];
  state.selectionBox = null;
  state.drawing = null;
  updateMeasureReadout('');
  renderProperties();
  render();
}

function collectHaEntities(fp) {
  const entities = new Set();
  (fp.sensors || []).forEach((sensor) => {
    if (sensor.entity) {
      entities.add(sensor.entity);
    }
  });
  (fp.thermostats || []).forEach((thermo) => {
    if (thermo.temperature_entity) {
      entities.add(thermo.temperature_entity);
    }
    if (thermo.setpoint_entity) {
      entities.add(thermo.setpoint_entity);
    }
    if (thermo.setpoint_low_entity) {
      entities.add(thermo.setpoint_low_entity);
    }
    if (thermo.setpoint_high_entity) {
      entities.add(thermo.setpoint_high_entity);
    }
    if (thermo.mode_entity) {
      entities.add(thermo.mode_entity);
    }
    if (thermo.fan_entity) {
      entities.add(thermo.fan_entity);
    }
  });
  if (fp.render?.outside_temp_entity) {
    entities.add(fp.render.outside_temp_entity);
  }
  if (fp.render?.chart_temp_entity) {
    entities.add(fp.render.chart_temp_entity);
  }
  return Array.from(entities);
}

function readHaState(entity) {
  if (!entity) {
    return null;
  }
  return state.haStates[entity] ?? null;
}

function formatTemperatureFromState(entity) {
  const raw = readHaState(entity);
  if (!raw || raw === 'unknown' || raw === 'unavailable' || raw === 'n/a') {
    return '';
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return '';
  }
  return `${value.toFixed(1)}F`;
}

function saveStoryAssignments() {
  localStorage.setItem('storyAssignments', JSON.stringify(state.storyAssignments));
}

function loadStoryAssignments() {
  try {
    const raw = localStorage.getItem('storyAssignments');
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      state.storyAssignments = {
        floor1: parsed.floor1 ?? null,
        floor2: parsed.floor2 ?? null,
      };
    }
  } catch (error) {
    // Ignore malformed local storage data.
  }
}

function getAssignedFloorplanId(storyId) {
  return state.storyAssignments[storyId] || storyId;
}

async function refreshHaStates() {
  const fp = currentFloorplan();
  if (!fp) {
    return;
  }
  const entities = collectHaEntities(fp);
  if (!entities.length) {
    state.haStates = {};
    render();
    return;
  }
  try {
    const params = new URLSearchParams({ entities: entities.join(',') });
    const response = await fetch(`/api/ha/states?${params.toString()}`);
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    state.haStates = payload.states || {};
    render();
  } catch (error) {
    // Ignore HA fetch errors in the editor preview.
  }
}

async function initialize() {
  loadStoryAssignments();
  updateToolButtons();
  updateToolHint();
  updateFloorTabs();
  snapToggle.checked = state.snapToGrid;
  orthoToggle.checked = state.orthogonalSnap;
  gridSizeInput.value = state.gridSize;
  updateStatusMeta();
  resizeCanvas();
  const availableFloorplans = await fetchFloorplanList();
  if (availableFloorplans.length) {
    const assignments = { ...state.storyAssignments };
    const storyOrder = ['floor1', 'floor2'];
    storyOrder.forEach((storyKey, index) => {
      const assigned = assignments[storyKey];
      if (assigned && availableFloorplans.includes(assigned)) {
        return;
      }
      assignments[storyKey] = availableFloorplans[index] || availableFloorplans[0];
    });
    const changed = storyOrder.some((storyKey) => assignments[storyKey] !== state.storyAssignments[storyKey]);
    if (changed) {
      state.storyAssignments = assignments;
      saveStoryAssignments();
    }
  }
  let initialFloorId = getAssignedFloorplanId(state.storyId);
  if (availableFloorplans.length && !availableFloorplans.includes(initialFloorId)) {
    initialFloorId = availableFloorplans[0];
  }
  await ensureFloorplanLoaded(initialFloorId, { announce: false });
  renderProperties();
  updateBackgroundControls();
  updateHistoryButtons();
  zoomToFit();
  refreshHaStates();
  if (!state.haPollInterval) {
    state.haPollInterval = window.setInterval(refreshHaStates, 15000);
  }
}

async function fetchFloorplanList({ quiet = false } = {}) {
  try {
    if (!quiet) {
      setStatus('Loading floorplan list...');
    }
    const response = await fetch('/api/floorplans');
    if (!response.ok) {
      throw new Error(`Failed to load floorplans (${response.status})`);
    }
    const payload = await response.json();
    const ids = payload.floorplans || [];
    const seen = new Set(ids);
    Object.entries(state.floorplans).forEach(([floorId, fp]) => {
      if (fp) {
        seen.add(floorId);
      }
    });
    Object.values(state.storyAssignments).forEach((floorId) => {
      if (floorId) {
        seen.add(floorId);
      }
    });
    const mergedIds = Array.from(seen).sort();
    loadSelect.innerHTML = '';
    mergedIds.forEach((id) => {
      const option = document.createElement('option');
      option.value = id;
      option.textContent = id;
      loadSelect.appendChild(option);
    });
    if (state.floorId && !mergedIds.includes(state.floorId)) {
      const fallback = document.createElement('option');
      fallback.value = state.floorId;
      fallback.textContent = state.floorId;
      loadSelect.appendChild(fallback);
    }
    if (!loadSelect.options.length) {
      const emptyOption = document.createElement('option');
      emptyOption.value = '';
      emptyOption.textContent = 'No floorplans found';
      loadSelect.appendChild(emptyOption);
    }
    const preferredId = getAssignedFloorplanId(state.storyId);
    if (preferredId && mergedIds.includes(preferredId)) {
      loadSelect.value = preferredId;
    } else if (state.floorId) {
      loadSelect.value = state.floorId;
    } else if (mergedIds.length) {
      loadSelect.value = mergedIds[0];
    }
    if (!quiet) {
      setStatus('Floorplan list loaded.');
    }
    updateStatusMeta();
    return mergedIds;
  } catch (error) {
    setStatus(error.message || 'Unable to load floorplans.', true);
    showToast(error.message || 'Unable to load floorplans.', 'error');
    return [];
  }
}

async function loadFloorplan(floorId, { allowCreate = false, announce = true } = {}) {
  try {
    setStatus(`Loading ${floorId}...`);
    const response = await fetch(`/api/floorplans/${encodeURIComponent(floorId)}`);
    if (!response.ok) {
      if (response.status === 404 && allowCreate) {
        state.floorId = floorId;
        setFloorplan(createDefaultFloorplan(floorId));
        resetHistory(floorId);
        setDirty(floorId, false);
        updateFloorTabs();
        loadSelect.value = floorId;
        setStatus(`No saved ${floorId} yet. Started a new floorplan.`);
        updateStatusMeta();
        if (announce) {
          showToast(`No saved ${floorId} yet. Started a new floorplan.`);
        }
        refreshHaStates();
        return;
      }
      throw new Error(`Failed to load ${floorId} (${response.status})`);
    }
    const payload = await response.json();
    state.floorId = floorId;
    setFloorplan(payload);
    resetHistory(floorId);
    setDirty(floorId, false);
    updateFloorTabs();
    loadSelect.value = floorId;
    setStatus(`Loaded ${floorId}.`);
    updateStatusMeta();
    if (announce) {
      showToast(`Loaded ${floorId}.`);
    }
    refreshHaStates();
  } catch (error) {
    if (allowCreate) {
      state.floorId = floorId;
      setFloorplan(createDefaultFloorplan(floorId));
      resetHistory(floorId);
      setDirty(floorId, false);
      updateFloorTabs();
      loadSelect.value = floorId;
      setStatus(`Unable to load ${floorId}. Started a new floorplan instead.`, true);
      updateStatusMeta();
      if (announce) {
        showToast(`Unable to load ${floorId}. Started a new floorplan instead.`, 'error');
      }
      refreshHaStates();
      return;
    }
    setStatus(error.message || `Unable to load ${floorId}.`, true);
    showToast(error.message || `Unable to load ${floorId}.`, 'error');
  }
}

async function saveFloorplan() {
  try {
    const fp = currentFloorplan();
    if (!fp) {
      showToast('Nothing to save yet.', 'error');
      return false;
    }
    setStatus(`Saving ${fp.floor_id}...`);
    const response = await fetch(`/api/floorplans/${encodeURIComponent(fp.floor_id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fp),
    });
    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `Failed to save ${fp.floor_id}`);
    }
    await response.json();
    setDirty(fp.floor_id, false);
    setStatus(`Saved ${fp.floor_id}.`);
    updateStatusMeta();
    showToast(`Saved ${fp.floor_id}.`);
    fetchFloorplanList({ quiet: true });
    return true;
  } catch (error) {
    setStatus(error.message || 'Unable to save floorplan.', true);
    showToast(error.message || 'Unable to save floorplan.', 'error');
    return false;
  }
}

async function deleteFloorplan() {
  const floorId = loadSelect.value || state.floorId;
  if (!floorId) {
    showToast('Select a floorplan to delete.', 'error');
    return;
  }
  if (!window.confirm(`Delete floorplan "${floorId}"? This cannot be undone.`)) {
    return;
  }
  try {
    const response = await fetch(`/api/floorplans/${encodeURIComponent(floorId)}`, { method: 'DELETE' });
    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `Failed to delete ${floorId}`);
    }
    delete state.floorplans[floorId];
    delete state.histories[floorId];
    setDirty(floorId, false);
    Object.entries(state.storyAssignments).forEach(([storyId, assigned]) => {
      if (assigned === floorId) {
        state.storyAssignments[storyId] = null;
      }
    });
    saveStoryAssignments();
    const remaining = await fetchFloorplanList();
    const nextId = remaining.find((id) => id !== floorId) || state.storyId;
    await ensureFloorplanLoaded(nextId, { announce: true });
    showToast(`Deleted ${floorId}.`);
  } catch (error) {
    setStatus(error.message || 'Unable to delete floorplan.', true);
    showToast(error.message || 'Unable to delete floorplan.', 'error');
  }
}

function updateStoryAssignmentsForRename(oldId, newId) {
  let changed = false;
  Object.entries(state.storyAssignments).forEach(([storyId, floorId]) => {
    if (floorId === oldId) {
      state.storyAssignments[storyId] = newId;
      changed = true;
    }
  });
  if (changed) {
    saveStoryAssignments();
  }
}

async function renameFloorplan(newId) {
  const trimmed = newId.trim();
  const oldId = state.floorId;
  if (!trimmed || trimmed === oldId) {
    return;
  }
  try {
    setStatus(`Renaming ${oldId}...`);
    const response = await fetch(`/api/floorplans/${encodeURIComponent(oldId)}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_id: trimmed }),
    });
    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `Unable to rename ${oldId}`);
    }
    const payload = await response.json();
    state.floorId = trimmed;
    state.floorplans[trimmed] = payload;
    delete state.floorplans[oldId];
    if (state.histories[oldId]) {
      state.histories[trimmed] = state.histories[oldId];
      delete state.histories[oldId];
    }
    if (state.dirtyFloors.has(oldId)) {
      state.dirtyFloors.delete(oldId);
      state.dirtyFloors.add(trimmed);
    }
    updateStoryAssignmentsForRename(oldId, trimmed);
    setFloorplan(payload);
    updateSaveIndicator();
    updateStatusMeta();
    showToast(`Renamed ${oldId} to ${trimmed}.`);
    await fetchFloorplanList();
  } catch (error) {
    setStatus(error.message || `Unable to rename ${oldId}.`, true);
    showToast(error.message || `Unable to rename ${oldId}.`, 'error');
    renderProperties();
  }
}

function updateToolButtons() {
  toolButtons.forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tool === state.tool);
  });
}

function updateFloorTabs() {
  floorTabs.forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.floor === state.storyId);
  });
}

async function ensureFloorplanLoaded(floorId, { announce = true } = {}) {
  if (state.floorplans[floorId]) {
    state.floorId = floorId;
    setFloorplan(state.floorplans[floorId]);
    updateFloorTabs();
    if (loadSelect.value) {
      loadSelect.value = floorId;
    }
    setStatus(`Viewing ${floorId}.`);
    updateStatusMeta();
    updateSaveIndicator();
    updateHistoryButtons();
    if (announce) {
      showToast(`Viewing ${floorId}.`);
    }
    refreshHaStates();
    return;
  }
  await loadFloorplan(floorId, { allowCreate: true, announce });
}

function switchStory(storyId) {
  state.storyId = storyId;
  updateFloorTabs();
  const assignedFloorplanId = getAssignedFloorplanId(storyId);
  ensureFloorplanLoaded(assignedFloorplanId);
}

// ---------------------------------------------------------------------------
// Viewport / zoom
// ---------------------------------------------------------------------------

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  state.viewport = { width: rect.width, height: rect.height, dpr };
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  render();
}

function worldToScreen(point) {
  return [point[0] * state.view.scale + state.view.x, point[1] * state.view.scale + state.view.y];
}

function screenToWorld(x, y) {
  return [(x - state.view.x) / state.view.scale, (y - state.view.y) / state.view.scale];
}

function updateZoomLevel() {
  if (zoomLevel) {
    zoomLevel.textContent = `${Math.round(state.view.scale * 100)}%`;
  }
}

function zoomBy(factor, center = null) {
  const vp = state.viewport;
  const point = center || [vp.width / 2, vp.height / 2];
  const world = screenToWorld(point[0], point[1]);
  const newScale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, state.view.scale * factor));
  state.view.x = point[0] - world[0] * newScale;
  state.view.y = point[1] - world[1] * newScale;
  state.view.scale = newScale;
  updateZoomLevel();
  render();
}

function zoomReset() {
  const vp = state.viewport;
  const world = screenToWorld(vp.width / 2, vp.height / 2);
  state.view.scale = 1;
  state.view.x = vp.width / 2 - world[0];
  state.view.y = vp.height / 2 - world[1];
  updateZoomLevel();
  render();
}

function contentBounds() {
  const fp = currentFloorplan();
  if (!fp) return null;
  let minX = 0;
  let minY = 0;
  let maxX = fp.canvas?.width || 1600;
  let maxY = fp.canvas?.height || 1000;
  const extend = (pt) => {
    minX = Math.min(minX, pt[0]);
    minY = Math.min(minY, pt[1]);
    maxX = Math.max(maxX, pt[0]);
    maxY = Math.max(maxY, pt[1]);
  };
  (fp.walls || []).forEach((wall) => wall.points.forEach(extend));
  (fp.doors || []).forEach((door) => door.segment.forEach(extend));
  (fp.sensors || []).forEach((sensor) => extend(sensor.pos));
  (fp.thermostats || []).forEach((thermo) => extend(thermo.pos));
  (fp.room_labels || []).forEach((label) => extend(label.pos));
  if (fp.stairwell) {
    fp.stairwell.polygon.forEach(extend);
  }
  return { minX, minY, maxX, maxY };
}

function zoomToFit() {
  const bounds = contentBounds();
  if (!bounds) return;
  const vp = state.viewport;
  if (!vp.width || !vp.height) return;
  const margin = 48;
  const width = Math.max(1, bounds.maxX - bounds.minX);
  const height = Math.max(1, bounds.maxY - bounds.minY);
  const scale = Math.min(
    MAX_ZOOM,
    Math.max(MIN_ZOOM, Math.min((vp.width - margin * 2) / width, (vp.height - margin * 2) / height)),
  );
  state.view.scale = scale;
  state.view.x = (vp.width - width * scale) / 2 - bounds.minX * scale;
  state.view.y = (vp.height - height * scale) / 2 - bounds.minY * scale;
  updateZoomLevel();
  render();
}

// ---------------------------------------------------------------------------
// Snapping / geometry
// ---------------------------------------------------------------------------

function snapPoint(point, reference = null, { ortho = null } = {}) {
  let next = [point[0], point[1]];
  if (state.snapToGrid) {
    next = [
      Math.round(next[0] / state.gridSize) * state.gridSize,
      Math.round(next[1] / state.gridSize) * state.gridSize,
    ];
  }
  const applyOrtho = ortho === null ? state.orthogonalSnap : ortho;
  if (reference && applyOrtho) {
    const dx = Math.abs(next[0] - reference[0]);
    const dy = Math.abs(next[1] - reference[1]);
    if (dx < dy) {
      next[0] = reference[0];
    } else {
      next[1] = reference[1];
    }
  }
  return next;
}

function collectSnapTargets(exclude = []) {
  const fp = currentFloorplan();
  const excludeSet = new Set(exclude.map((key) => `${key.type}:${key.id}:${key.index ?? ''}`));
  const targets = [];
  (fp.walls || []).forEach((wall) => {
    wall.points.forEach((pt, idx) => {
      if (excludeSet.has(`wall:${wall.id}:${idx}`)) return;
      targets.push({ point: pt });
    });
  });
  (fp.doors || []).forEach((door) => {
    door.segment.forEach((pt, idx) => {
      if (excludeSet.has(`door:${door.id}:${idx}`)) return;
      targets.push({ point: pt });
    });
  });
  return targets;
}

function resolveSnapPoint(point, reference = null, exclude = [], options = {}) {
  let snapped = snapPoint(point, reference, options);
  // Vertex snapping applies even when grid snap is off so walls and doors can
  // always be joined precisely.
  const threshold = 10 / state.view.scale;
  const targets = collectSnapTargets(exclude);
  let best = null;
  targets.forEach(({ point: target }) => {
    const dist = pointDistance(snapped, target);
    if (dist <= threshold && (!best || dist < best.dist)) {
      best = { point: target, dist };
    }
  });
  if (best) {
    snapped = [...best.point];
  }
  return snapped;
}

function distanceToSegment(point, a, b) {
  const [px, py] = point;
  const [x1, y1] = a;
  const [x2, y2] = b;
  const dx = x2 - x1;
  const dy = y2 - y1;
  if (dx === 0 && dy === 0) {
    return Math.hypot(px - x1, py - y1);
  }
  const t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy);
  const clamped = Math.max(0, Math.min(1, t));
  const cx = x1 + clamped * dx;
  const cy = y1 + clamped * dy;
  return Math.hypot(px - cx, py - cy);
}

function pointInPolygon(point, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const intersect = yi > point[1] !== yj > point[1]
      && point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi + 1e-9) + xi;
    if (intersect) {
      inside = !inside;
    }
  }
  return inside;
}

function pointDistance(p1, p2) {
  return Math.hypot(p1[0] - p2[0], p1[1] - p2[1]);
}

function dedupePoints(points) {
  const out = [];
  points.forEach((pt) => {
    const last = out[out.length - 1];
    if (!last || pointDistance(last, pt) > 0.001) {
      out.push(pt);
    }
  });
  return out;
}

function polylineLength(points) {
  let total = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    total += pointDistance(points[i], points[i + 1]);
  }
  return total;
}

function findWallVertexHit(point, threshold) {
  const fp = currentFloorplan();
  for (const wall of (fp.walls || [])) {
    for (let i = 0; i < wall.points.length; i += 1) {
      if (pointDistance(point, wall.points[i]) <= threshold) {
        return { type: 'wall_vertex', id: wall.id, vertexIndex: i };
      }
    }
  }
  return null;
}

function pointInTextBounds(point, text, fontSizeWorld, align, x, y) {
  if (!text) return false;
  const width = measureTextWidth(fontSizeWorld, text);
  const height = fontSizeWorld;
  let left = x;
  if (align === 'center') {
    left = x - width / 2;
  } else if (align === 'right') {
    left = x - width;
  }
  const padding = 3 / state.view.scale;
  const top = y - height;
  return (
    point[0] >= left - padding
    && point[0] <= left + width + padding
    && point[1] >= top - padding
    && point[1] <= y + padding
  );
}

function labelHitTest(point) {
  const fp = currentFloorplan();
  for (const sensor of (fp.sensors || [])) {
    const lines = getSensorLabelLines(sensor);
    if (!lines.length) continue;
    const baseFontSize = sensor.font_size || 12;
    const fontSizeWorld = baseFontSize / state.view.scale;
    const align = getLabelAlignment(sensor);
    const offX = (sensor.label_offset_x ?? 10) / state.view.scale;
    const offY = (sensor.label_offset_y ?? -8) / state.view.scale;
    const lineHeight = (baseFontSize + 2) / state.view.scale;
    for (let i = 0; i < lines.length; i += 1) {
      const y = sensor.pos[1] + offY + i * lineHeight;
      const x = sensor.pos[0] + offX;
      if (pointInTextBounds(point, lines[i], fontSizeWorld, align, x, y)) {
        return { type: 'sensor', id: sensor.id, target: 'label' };
      }
    }
  }
  for (const thermo of (fp.thermostats || [])) {
    const lines = getThermostatLabelLines(thermo);
    if (!lines.length) continue;
    const baseFontSize = thermo.font_size || 12;
    const fontSizeWorld = baseFontSize / state.view.scale;
    const align = getLabelAlignment(thermo);
    const offX = (thermo.label_offset_x ?? 12) / state.view.scale;
    const offY = (thermo.label_offset_y ?? -8) / state.view.scale;
    const lineHeight = (baseFontSize + 2) / state.view.scale;
    for (let i = 0; i < lines.length; i += 1) {
      const y = thermo.pos[1] + offY + i * lineHeight;
      const x = thermo.pos[0] + offX;
      if (pointInTextBounds(point, lines[i], fontSizeWorld, align, x, y)) {
        return { type: 'thermostat', id: thermo.id, target: 'label' };
      }
    }
  }
  for (const label of (fp.room_labels || [])) {
    if (!label.label) continue;
    const fontSizeWorld = (label.font_size || 16) / state.view.scale;
    const align = getLabelAlignment(label);
    const offX = (label.label_offset_x ?? 0) / state.view.scale;
    const offY = (label.label_offset_y ?? 0) / state.view.scale;
    const x = label.pos[0] + offX;
    const y = label.pos[1] + offY;
    if (pointInTextBounds(point, label.label, fontSizeWorld, align, x, y)) {
      return { type: 'room_label', id: label.id, target: 'label' };
    }
  }
  return null;
}

function hitTest(point) {
  const fp = currentFloorplan();
  const threshold = 10 / state.view.scale;

  const labelHit = labelHitTest(point);
  if (labelHit) {
    return labelHit;
  }

  for (const sensor of (fp.sensors || [])) {
    if (Math.hypot(point[0] - sensor.pos[0], point[1] - sensor.pos[1]) <= threshold) {
      return { type: 'sensor', id: sensor.id };
    }
  }
  for (const thermo of (fp.thermostats || [])) {
    if (Math.hypot(point[0] - thermo.pos[0], point[1] - thermo.pos[1]) <= threshold) {
      return { type: 'thermostat', id: thermo.id };
    }
  }
  for (const label of (fp.room_labels || [])) {
    if (Math.hypot(point[0] - label.pos[0], point[1] - label.pos[1]) <= threshold) {
      return { type: 'room_label', id: label.id };
    }
  }
  for (const door of (fp.doors || [])) {
    if (distanceToSegment(point, door.segment[0], door.segment[1]) <= threshold) {
      return { type: 'door', id: door.id };
    }
  }
  const vertexHit = findWallVertexHit(point, threshold);
  if (vertexHit) {
    return vertexHit;
  }
  for (const wall of (fp.walls || [])) {
    for (let i = 0; i < wall.points.length - 1; i += 1) {
      if (distanceToSegment(point, wall.points[i], wall.points[i + 1]) <= threshold) {
        return { type: 'wall', id: wall.id, segmentIndex: i };
      }
    }
  }
  if (fp.stairwell && pointInPolygon(point, fp.stairwell.polygon)) {
    return { type: 'stairwell', id: fp.stairwell.id };
  }
  return null;
}

function boundsFromPoints(points) {
  const xs = points.map((pt) => pt[0]);
  const ys = points.map((pt) => pt[1]);
  return {
    minX: Math.min(...xs),
    minY: Math.min(...ys),
    maxX: Math.max(...xs),
    maxY: Math.max(...ys),
  };
}

function getItemBounds(type, item) {
  if (type === 'sensor' || type === 'thermostat' || type === 'room_label') {
    const [x, y] = item.pos;
    const size = 10;
    return { minX: x - size, minY: y - size, maxX: x + size, maxY: y + size };
  }
  if (type === 'door') {
    return boundsFromPoints(item.segment);
  }
  if (type === 'wall') {
    return boundsFromPoints(item.points);
  }
  if (type === 'stairwell') {
    return boundsFromPoints(item.polygon);
  }
  return null;
}

function itemIntersectsBox(bounds, box) {
  return !(bounds.maxX < box.minX || bounds.minX > box.maxX || bounds.maxY < box.minY || bounds.minY > box.maxY);
}

function findItemsInBox(box) {
  const fp = currentFloorplan();
  const hits = [];
  const items = [
    ...(fp.sensors || []).map((item) => ({ type: 'sensor', item })),
    ...(fp.thermostats || []).map((item) => ({ type: 'thermostat', item })),
    ...(fp.room_labels || []).map((item) => ({ type: 'room_label', item })),
    ...(fp.doors || []).map((item) => ({ type: 'door', item })),
    ...(fp.walls || []).map((item) => ({ type: 'wall', item })),
  ];
  if (fp.stairwell) {
    items.push({ type: 'stairwell', item: fp.stairwell });
  }
  items.forEach(({ type, item }) => {
    const bounds = getItemBounds(type, item);
    if (bounds && itemIntersectsBox(bounds, box)) {
      hits.push({ type, id: item.id });
    }
  });
  return hits;
}

function findById(type, id) {
  const fp = currentFloorplan();
  if (type === 'wall') {
    return (fp.walls || []).find((wall) => wall.id === id);
  }
  if (type === 'door') {
    return (fp.doors || []).find((door) => door.id === id);
  }
  if (type === 'sensor') {
    return (fp.sensors || []).find((sensor) => sensor.id === id);
  }
  if (type === 'thermostat') {
    return (fp.thermostats || []).find((thermo) => thermo.id === id);
  }
  if (type === 'room_label') {
    return (fp.room_labels || []).find((label) => label.id === id);
  }
  if (type === 'stairwell') {
    return fp.stairwell;
  }
  return null;
}

function removeSelected() {
  const fp = currentFloorplan();
  if (!state.selected && !state.multiSelected.length) {
    return;
  }
  pushHistory();
  const targets = state.multiSelected.length
    ? state.multiSelected
    : [state.selected];
  targets.forEach(({ type, id }) => {
    if (type === 'wall') {
      fp.walls = (fp.walls || []).filter((wall) => wall.id !== id);
    } else if (type === 'door') {
      fp.doors = (fp.doors || []).filter((door) => door.id !== id);
    } else if (type === 'sensor') {
      fp.sensors = (fp.sensors || []).filter((sensor) => sensor.id !== id);
    } else if (type === 'thermostat') {
      fp.thermostats = (fp.thermostats || []).filter((thermo) => thermo.id !== id);
    } else if (type === 'room_label') {
      fp.room_labels = (fp.room_labels || []).filter((label) => label.id !== id);
    } else if (type === 'stairwell') {
      fp.stairwell = null;
    }
  });
  state.selected = null;
  state.multiSelected = [];
  renderProperties();
  render();
}

function duplicateSelection() {
  const fp = currentFloorplan();
  const targets = state.multiSelected.length
    ? state.multiSelected
    : (state.selected ? [state.selected] : []);
  if (!targets.length) {
    return;
  }
  const offset = state.gridSize;
  const created = [];
  const snapshot = deepCopy(fp);
  targets.forEach(({ type, id }) => {
    const item = findById(type, id);
    if (!item) return;
    const copy = deepCopy(item);
    copy.id = ensureId(type);
    if (type === 'sensor' || type === 'thermostat' || type === 'room_label') {
      copy.pos = [copy.pos[0] + offset, copy.pos[1] + offset];
      fp[type === 'room_label' ? 'room_labels' : `${type}s`].push(copy);
    } else if (type === 'door') {
      copy.segment = copy.segment.map((pt) => [pt[0] + offset, pt[1] + offset]);
      fp.doors.push(copy);
    } else if (type === 'wall') {
      copy.points = copy.points.map((pt) => [pt[0] + offset, pt[1] + offset]);
      fp.walls.push(copy);
    } else {
      // Only one stairwell is supported per floor.
      return;
    }
    created.push({ type, id: copy.id });
  });
  if (!created.length) {
    showToast('Nothing to duplicate.', 'error');
    return;
  }
  pushHistorySnapshot(snapshot);
  state.multiSelected = created.length > 1 ? created : [];
  state.selected = created.length === 1 ? created[0] : null;
  renderProperties();
  render();
  showToast(`Duplicated ${created.length} item${created.length > 1 ? 's' : ''}.`);
}

let lastNudgeAt = 0;

function nudgeSelection(dx, dy) {
  const targets = state.multiSelected.length
    ? state.multiSelected
    : (state.selected ? [state.selected] : []);
  if (!targets.length) {
    return false;
  }
  const now = Date.now();
  if (now - lastNudgeAt > 600) {
    pushHistory();
  }
  lastNudgeAt = now;
  targets.forEach(({ type, id }) => {
    const item = findById(type, id);
    if (!item) return;
    if (type === 'sensor' || type === 'thermostat' || type === 'room_label') {
      item.pos = [item.pos[0] + dx, item.pos[1] + dy];
    } else if (type === 'door') {
      item.segment = item.segment.map((pt) => [pt[0] + dx, pt[1] + dy]);
    } else if (type === 'wall') {
      item.points = item.points.map((pt) => [pt[0] + dx, pt[1] + dy]);
    } else if (type === 'stairwell') {
      item.polygon = item.polygon.map((pt) => [pt[0] + dx, pt[1] + dy]);
    }
  });
  render();
  return true;
}

function ensureId(prefix) {
  return `${prefix}_${Math.random().toString(36).slice(2, 8)}`;
}

// ---------------------------------------------------------------------------
// Canvas rendering
// ---------------------------------------------------------------------------

function renderGrid(worldBounds) {
  const [left, top, right, bottom] = worldBounds;
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.045)';
  ctx.lineWidth = 1 / state.view.scale;
  const startX = Math.floor(left / state.gridSize) * state.gridSize;
  const endX = Math.ceil(right / state.gridSize) * state.gridSize;
  const startY = Math.floor(top / state.gridSize) * state.gridSize;
  const endY = Math.ceil(bottom / state.gridSize) * state.gridSize;
  for (let x = startX; x <= endX; x += state.gridSize) {
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
  }
  for (let y = startY; y <= endY; y += state.gridSize) {
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
  }
}

function renderCanvasExtent(fp) {
  const width = fp.canvas?.width || 1600;
  const height = fp.canvas?.height || 1000;
  ctx.save();
  ctx.fillStyle = 'rgba(255, 255, 255, 0.018)';
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = 'rgba(90, 167, 255, 0.35)';
  ctx.lineWidth = 1.5 / state.view.scale;
  ctx.setLineDash([8 / state.view.scale, 6 / state.view.scale]);
  ctx.strokeRect(0, 0, width, height);
  ctx.restore();
}

function renderWalls(fp) {
  ctx.strokeStyle = '#f0f0f0';
  ctx.lineWidth = 4 / state.view.scale;
  (fp.walls || []).forEach((wall) => {
    if (wall.points.length < 2) return;
    ctx.beginPath();
    wall.points.forEach((pt, idx) => {
      if (idx === 0) {
        ctx.moveTo(pt[0], pt[1]);
      } else {
        ctx.lineTo(pt[0], pt[1]);
      }
    });
    ctx.stroke();
  });
}

function renderDoors(fp) {
  ctx.strokeStyle = '#5aa7ff';
  ctx.lineWidth = 5 / state.view.scale;
  (fp.doors || []).forEach((door) => {
    ctx.beginPath();
    ctx.moveTo(door.segment[0][0], door.segment[0][1]);
    ctx.lineTo(door.segment[1][0], door.segment[1][1]);
    ctx.stroke();
  });
}

function getLabelAlignment(item) {
  return item.label_align || 'left';
}

function measureTextWidth(fontSize, text) {
  ctx.save();
  ctx.font = `${fontSize}px sans-serif`;
  const width = ctx.measureText(text).width;
  ctx.restore();
  return width;
}

function getSensorLabelLines(sensor) {
  const label = sensor.label || sensor.entity || '';
  const tempValue = formatTemperatureFromState(sensor.entity);
  const tempLine = tempValue || (sensor.entity ? 'n/a' : '');
  return [label, tempLine].filter(Boolean);
}

function getThermostatLabelLines(thermo) {
  const label = thermo.device_label || 'Thermostat';
  const tempValue = formatTemperatureFromState(thermo.temperature_entity);
  const setpointValue = formatTemperatureFromState(thermo.setpoint_entity);
  const setpointLow = formatTemperatureFromState(thermo.setpoint_low_entity);
  const setpointHigh = formatTemperatureFromState(thermo.setpoint_high_entity);
  const modeState = readHaState(thermo.mode_entity);
  const modeLower = modeState ? modeState.toLowerCase() : '';

  let setpointLine = '';
  if (setpointLow && setpointHigh) {
    setpointLine = `${setpointLow} / ${setpointHigh}`;
  } else if (['heat_cool', 'auto'].includes(modeLower)) {
    setpointLine = setpointLow || setpointHigh || setpointValue;
  } else if (modeLower === 'heat') {
    setpointLine = setpointValue || setpointLow;
  } else if (modeLower === 'cool') {
    setpointLine = setpointValue || setpointHigh;
  } else {
    setpointLine = setpointValue || setpointLow || setpointHigh;
  }

  let actionLine = '';
  if (modeState) {
    actionLine = modeState.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
  }

  const lines = [label];
  if (tempValue) lines.push(tempValue);
  if (setpointLine) lines.push(setpointLine);
  if (actionLine) lines.push(actionLine);
  return lines.filter(Boolean);
}

function renderSensors(fp) {
  (fp.sensors || []).forEach((sensor) => {
    const baseFontSize = sensor.font_size || 12;
    const fontSize = baseFontSize / state.view.scale;
    ctx.font = `${fontSize}px sans-serif`;
    ctx.fillStyle = '#5ad8a5';
    ctx.beginPath();
    ctx.arc(sensor.pos[0], sensor.pos[1], 6 / state.view.scale, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(90, 216, 165, 0.4)';
    ctx.lineWidth = 2 / state.view.scale;
    ctx.beginPath();
    ctx.arc(sensor.pos[0], sensor.pos[1], 10 / state.view.scale, 0, Math.PI * 2);
    ctx.stroke();
    if (fp.render.show_labels) {
      ctx.fillStyle = '#ffffff';
      const labelLines = getSensorLabelLines(sensor);
      ctx.textAlign = getLabelAlignment(sensor);
      const offX = (sensor.label_offset_x ?? 10) / state.view.scale;
      const offY = (sensor.label_offset_y ?? -8) / state.view.scale;

      labelLines.forEach((line, index) => {
        const lineOffset = index * ((baseFontSize + 2) / state.view.scale);
        ctx.fillText(line, sensor.pos[0] + offX, sensor.pos[1] + offY + lineOffset);
      });
    }
  });
  ctx.textAlign = 'left';
}

function renderThermostats(fp) {
  ctx.strokeStyle = '#f5c542';
  ctx.lineWidth = 2 / state.view.scale;
  (fp.thermostats || []).forEach((thermo) => {
    const baseFontSize = thermo.font_size || 12;
    const fontSize = baseFontSize / state.view.scale;
    ctx.font = `${fontSize}px sans-serif`;
    ctx.strokeRect(thermo.pos[0] - 7 / state.view.scale, thermo.pos[1] - 7 / state.view.scale, 14 / state.view.scale, 14 / state.view.scale);
    if (fp.render.show_labels) {
      ctx.fillStyle = '#f5c542';
      const labelLines = getThermostatLabelLines(thermo);
      ctx.textAlign = getLabelAlignment(thermo);
      const offX = (thermo.label_offset_x ?? 12) / state.view.scale;
      const offY = (thermo.label_offset_y ?? -8) / state.view.scale;

      labelLines.forEach((line, index) => {
        const lineOffset = index * ((baseFontSize + 2) / state.view.scale);
        ctx.fillText(
          line,
          thermo.pos[0] + offX,
          thermo.pos[1] + offY + lineOffset,
        );
      });
    }
  });
  ctx.textAlign = 'left';
}

function renderRoomLabels(fp) {
  ctx.fillStyle = '#ffffff';
  (fp.room_labels || []).forEach((label) => {
    if (!label.label) return;
    const fontSize = (label.font_size || 16) / state.view.scale;
    ctx.font = `${fontSize}px sans-serif`;
    ctx.textAlign = getLabelAlignment(label);
    const offX = (label.label_offset_x ?? 0) / state.view.scale;
    const offY = (label.label_offset_y ?? 0) / state.view.scale;
    ctx.fillText(label.label, label.pos[0] + offX, label.pos[1] + offY);
  });
  ctx.textAlign = 'left';
}

function renderStairwell(fp) {
  if (!fp.stairwell || fp.stairwell.polygon.length < 3) {
    return;
  }
  ctx.fillStyle = 'rgba(140, 100, 255, 0.3)';
  ctx.strokeStyle = 'rgba(140, 100, 255, 0.8)';
  ctx.lineWidth = 2 / state.view.scale;
  ctx.beginPath();
  fp.stairwell.polygon.forEach((pt, idx) => {
    if (idx === 0) {
      ctx.moveTo(pt[0], pt[1]);
    } else {
      ctx.lineTo(pt[0], pt[1]);
    }
  });
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}

function renderSelection() {
  ctx.strokeStyle = '#ff6b6b';
  ctx.lineWidth = 2 / state.view.scale;
  const selections = state.multiSelected.length ? state.multiSelected : (state.selected ? [state.selected] : []);
  selections.forEach((selection) => {
    const item = findById(selection.type, selection.id);
    if (!item) return;
    if (selection.type === 'sensor' || selection.type === 'thermostat') {
      const pos = item.pos;
      ctx.strokeRect(pos[0] - 10 / state.view.scale, pos[1] - 10 / state.view.scale, 20 / state.view.scale, 20 / state.view.scale);
    } else if (selection.type === 'room_label') {
      const pos = item.pos;
      ctx.strokeRect(pos[0] - 12 / state.view.scale, pos[1] - 12 / state.view.scale, 24 / state.view.scale, 24 / state.view.scale);
    } else if (selection.type === 'door') {
      ctx.beginPath();
      ctx.moveTo(item.segment[0][0], item.segment[0][1]);
      ctx.lineTo(item.segment[1][0], item.segment[1][1]);
      ctx.stroke();
    } else if (selection.type === 'wall') {
      ctx.beginPath();
      item.points.forEach((pt, idx) => {
        if (idx === 0) {
          ctx.moveTo(pt[0], pt[1]);
        } else {
          ctx.lineTo(pt[0], pt[1]);
        }
      });
      ctx.stroke();
      const handleSize = 6 / state.view.scale;
      ctx.fillStyle = '#ff6b6b';
      item.points.forEach((pt, idx) => {
        const half = handleSize / 2;
        ctx.fillRect(pt[0] - half, pt[1] - half, handleSize, handleSize);
        if (selection.vertexIndex === idx) {
          ctx.strokeRect(pt[0] - half, pt[1] - half, handleSize, handleSize);
        }
      });
    } else if (selection.type === 'stairwell') {
      ctx.beginPath();
      item.polygon.forEach((pt, idx) => {
        if (idx === 0) {
          ctx.moveTo(pt[0], pt[1]);
        } else {
          ctx.lineTo(pt[0], pt[1]);
        }
      });
      ctx.closePath();
      ctx.stroke();
    }
  });
}

function renderSelectionBox() {
  if (!state.selectionBox) return;
  const { start, end } = state.selectionBox;
  const left = Math.min(start[0], end[0]);
  const top = Math.min(start[1], end[1]);
  const width = Math.abs(start[0] - end[0]);
  const height = Math.abs(start[1] - end[1]);
  ctx.save();
  ctx.strokeStyle = '#5aa7ff';
  ctx.fillStyle = 'rgba(90, 167, 255, 0.06)';
  ctx.lineWidth = 1 / state.view.scale;
  ctx.setLineDash([6 / state.view.scale, 4 / state.view.scale]);
  ctx.fillRect(left, top, width, height);
  ctx.strokeRect(left, top, width, height);
  ctx.restore();
}

function renderDrawingPreview() {
  if (!state.drawing) return;
  ctx.strokeStyle = '#5aa7ff';
  ctx.lineWidth = 2 / state.view.scale;
  if (state.drawing.type === 'wall' || state.drawing.type === 'stairwell') {
    ctx.beginPath();
    state.drawing.points.forEach((pt, idx) => {
      if (idx === 0) {
        ctx.moveTo(pt[0], pt[1]);
      } else {
        ctx.lineTo(pt[0], pt[1]);
      }
    });
    if (state.drawing.previewPoint) {
      ctx.lineTo(state.drawing.previewPoint[0], state.drawing.previewPoint[1]);
    }
    ctx.stroke();
    ctx.fillStyle = '#5aa7ff';
    const dotRadius = 3 / state.view.scale;
    state.drawing.points.forEach((pt) => {
      ctx.beginPath();
      ctx.arc(pt[0], pt[1], dotRadius, 0, Math.PI * 2);
      ctx.fill();
    });
  } else if (state.drawing.type === 'door' && state.drawing.start) {
    ctx.beginPath();
    ctx.moveTo(state.drawing.start[0], state.drawing.start[1]);
    if (state.drawing.previewPoint) {
      ctx.lineTo(state.drawing.previewPoint[0], state.drawing.previewPoint[1]);
    }
    ctx.stroke();
  } else if (state.drawing.type === 'scale' && state.drawing.start) {
    ctx.save();
    ctx.strokeStyle = '#ffc75a';
    ctx.setLineDash([6 / state.view.scale, 4 / state.view.scale]);
    ctx.beginPath();
    ctx.moveTo(state.drawing.start[0], state.drawing.start[1]);
    if (state.drawing.previewPoint) {
      ctx.lineTo(state.drawing.previewPoint[0], state.drawing.previewPoint[1]);
    }
    ctx.stroke();
    ctx.restore();
    ctx.fillStyle = '#ffc75a';
    ctx.beginPath();
    ctx.arc(state.drawing.start[0], state.drawing.start[1], 4 / state.view.scale, 0, Math.PI * 2);
    ctx.fill();
  }
}

function render() {
  const { width: vw, height: vh, dpr } = state.viewport;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#0d1018';
  ctx.fillRect(0, 0, vw, vh);

  const worldLeft = (-state.view.x) / state.view.scale;
  const worldTop = (-state.view.y) / state.view.scale;
  const worldRight = (vw - state.view.x) / state.view.scale;
  const worldBottom = (vh - state.view.y) / state.view.scale;

  ctx.setTransform(dpr * state.view.scale, 0, 0, dpr * state.view.scale, dpr * state.view.x, dpr * state.view.y);

  if (state.backgroundImage) {
    ctx.save();
    ctx.globalAlpha = state.background.opacity;
    ctx.translate(state.background.x, state.background.y);
    ctx.scale(state.background.scale, state.background.scale);
    ctx.drawImage(state.backgroundImage, 0, 0);
    ctx.restore();
  }

  renderGrid([worldLeft, worldTop, worldRight, worldBottom]);
  const fp = currentFloorplan();

  if (!fp) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#8b93a7';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No floorplan loaded. Select one from the dropdown and click Load.', vw / 2, vh / 2);
    ctx.textAlign = 'left';
    return;
  }

  renderCanvasExtent(fp);
  if (fp.render.show_walls) {
    renderWalls(fp);
  }
  renderDoors(fp);
  renderStairwell(fp);
  renderSensors(fp);
  renderThermostats(fp);
  renderRoomLabels(fp);
  renderDrawingPreview();
  renderSelectionBox();
  renderSelection();

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  renderOutsideTemperature(fp);
}

function renderOutsideTemperature(fp) {
  if (!fp.render?.show_outside_temp) {
    return;
  }
  const label = fp.render.outside_temp_label || 'Outside';
  let tempValue = '';
  if (fp.render.outside_temp_entity) {
    tempValue = formatTemperatureFromState(fp.render.outside_temp_entity) || 'n/a';
  } else if (fp.render.outside_temp_f !== null && fp.render.outside_temp_f !== undefined) {
    const outsideTemp = Number(fp.render.outside_temp_f);
    if (Number.isFinite(outsideTemp)) {
      tempValue = `${outsideTemp.toFixed(1)}F`;
    }
  }
  if (!tempValue) {
    return;
  }
  ctx.fillStyle = '#8b93a7';
  ctx.font = '12px sans-serif';
  ctx.fillText(`${label}: ${tempValue}`, 16, 20);
}

// ---------------------------------------------------------------------------
// Properties panel
// ---------------------------------------------------------------------------

const TYPE_LABELS = {
  wall: 'Wall',
  door: 'Door',
  sensor: 'Sensor',
  thermostat: 'Thermostat',
  room_label: 'Room Label',
  stairwell: 'Stairwell',
};

function renderProperties() {
  propertiesPanel.innerHTML = '';
  const fp = currentFloorplan();
  if (!fp) {
    return;
  }
  if (!state.selected) {
    if (state.multiSelected.length) {
      const title = document.createElement('h3');
      title.textContent = `${state.multiSelected.length} Items Selected`;
      propertiesPanel.appendChild(title);
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.textContent = 'Drag to move all items together. Press Delete to remove them, or Ctrl+D to duplicate.';
      propertiesPanel.appendChild(hint);
      propertiesPanel.appendChild(renderActionButton('Delete Selected Items', () => {
        removeSelected();
      }, { danger: true }));
      return;
    }
    const title = document.createElement('h3');
    title.textContent = 'Floorplan Settings';
    propertiesPanel.appendChild(title);
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = 'Click an item on the canvas to edit it, or adjust the floor-wide settings below.';
    propertiesPanel.appendChild(hint);

    const layoutSection = createSection('Canvas & Scale');
    layoutSection.body.appendChild(renderField('Floor ID', fp.floor_id, (val) => {
      renameFloorplan(val);
    }));
    layoutSection.body.appendChild(renderNumberField('Canvas Width (px)', fp.canvas.width, (val) => {
      pushHistory();
      fp.canvas.width = parseInt(val, 10) || 1600;
    }));
    layoutSection.body.appendChild(renderNumberField('Canvas Height (px)', fp.canvas.height, (val) => {
      pushHistory();
      fp.canvas.height = parseInt(val, 10) || 1000;
    }));
    layoutSection.body.appendChild(renderNumberField('Scale (px per meter)', Number(fp.scale.px_per_meter.toFixed(2)), (val) => {
      pushHistory();
      fp.scale.px_per_meter = parseFloat(val) || fp.scale.px_per_meter;
      render();
    }, { step: 0.01 }));
    propertiesPanel.appendChild(layoutSection.details);

    const displaySection = createSection('Display', { open: false });
    displaySection.body.appendChild(renderCheckboxField('Show Walls', fp.render.show_walls ?? true, (val) => {
      pushHistory();
      fp.render.show_walls = val;
    }));
    displaySection.body.appendChild(renderCheckboxField('Show Labels', fp.render.show_labels ?? true, (val) => {
      pushHistory();
      fp.render.show_labels = val;
    }));
    displaySection.body.appendChild(renderCheckboxField('Show Timestamp', fp.render.show_timestamp ?? true, (val) => {
      pushHistory();
      fp.render.show_timestamp = val;
    }));
    displaySection.body.appendChild(renderRangeField('Heatmap Overlay Alpha', fp.render.overlay_alpha ?? 0.6, (val) => {
      pushHistory();
      fp.render.overlay_alpha = val;
    }, { min: 0, max: 1, step: 0.05 }));
    displaySection.body.appendChild(renderCheckboxField('Auto Crop Output', fp.render.auto_crop ?? true, (val) => {
      pushHistory();
      fp.render.auto_crop = val;
    }));
    displaySection.body.appendChild(renderNumberField('Crop Padding (px)', fp.render.crop_padding ?? 30, (val) => {
      pushHistory();
      fp.render.crop_padding = parseInt(val, 10) || 0;
    }));
    displaySection.body.appendChild(renderNumberField('Exterior Margin (px)', fp.render.exterior_margin ?? 20, (val) => {
      pushHistory();
      fp.render.exterior_margin = parseInt(val, 10) || 0;
    }));
    displaySection.body.appendChild(renderNumberField('Text Font Size (blank = auto)', fp.render.text_font_size ?? '', (val) => {
      pushHistory();
      if (val === '') {
        fp.render.text_font_size = null;
      } else {
        fp.render.text_font_size = Math.max(8, parseInt(val, 10) || 12);
      }
    }, { allowEmpty: true }));
    propertiesPanel.appendChild(displaySection.details);

    const outsideSection = createSection('Outside Temperature', { open: false });
    outsideSection.body.appendChild(renderCheckboxField('Show Outside Temp', fp.render.show_outside_temp ?? true, (val) => {
      pushHistory();
      fp.render.show_outside_temp = val;
    }));
    outsideSection.body.appendChild(renderField('Outside Label', fp.render.outside_temp_label || 'Outside', (val) => {
      pushHistory();
      fp.render.outside_temp_label = val;
    }));
    outsideSection.body.appendChild(renderField('Outside Temp Entity', fp.render.outside_temp_entity || '', (val) => {
      pushHistory();
      fp.render.outside_temp_entity = val;
      refreshHaStates();
    }));
    outsideSection.body.appendChild(renderNumberField('Outside Temp (F) Fallback', fp.render.outside_temp_f ?? '', (val) => {
      pushHistory();
      fp.render.outside_temp_f = val === '' ? null : parseFloat(val);
    }, { allowEmpty: true, step: 0.1 }));
    propertiesPanel.appendChild(outsideSection.details);

    const legendSection = createSection('Color Scale', { open: false });
    legendSection.body.appendChild(renderCheckboxField('Show Legend', fp.render.show_legend ?? true, (val) => {
      pushHistory();
      fp.render.show_legend = val;
    }));
    legendSection.body.appendChild(renderField('Legend Colors (hex, comma-separated)', formatLegendColors(fp.render.legend_colors), (val) => {
      pushHistory();
      fp.render.legend_colors = parseLegendColorsInput(val);
    }));
    legendSection.body.appendChild(renderNumberField('Scale Min (F)', fp.render.temp_range_f?.min ?? '', (val) => {
      pushHistory();
      const next = parseFloat(val);
      if (Number.isFinite(next)) {
        fp.render.temp_range_f.min = next;
      }
    }, { step: 0.5 }));
    legendSection.body.appendChild(renderNumberField('Scale Max (F)', fp.render.temp_range_f?.max ?? '', (val) => {
      pushHistory();
      const next = parseFloat(val);
      if (Number.isFinite(next)) {
        fp.render.temp_range_f.max = next;
      }
    }, { step: 0.5 }));
    legendSection.body.appendChild(renderField('Scale Min Mode', fp.render.scale_min_mode || 'absolute', (val) => {
      pushHistory();
      fp.render.scale_min_mode = val;
    }, ['absolute', 'relative']));
    legendSection.body.appendChild(renderField('Scale Max Mode', fp.render.scale_max_mode || 'absolute', (val) => {
      pushHistory();
      fp.render.scale_max_mode = val;
    }, ['absolute', 'relative']));
    propertiesPanel.appendChild(legendSection.details);

    const chartSection = createSection('Temperature Chart', { open: false });
    chartSection.body.appendChild(renderCheckboxField('Show Chart', fp.render.show_chart ?? false, (val) => {
      pushHistory();
      fp.render.show_chart = val;
    }));
    chartSection.body.appendChild(renderField('Chart Temp Entity', fp.render.chart_temp_entity || '', (val) => {
      pushHistory();
      fp.render.chart_temp_entity = val;
      refreshHaStates();
    }));
    chartSection.body.appendChild(renderField('Chart Forecast Entity', fp.render.chart_forecast_entity || '', (val) => {
      pushHistory();
      fp.render.chart_forecast_entity = val;
    }));
    chartSection.body.appendChild(renderNumberField('Chart History Hours', fp.render.chart_history_hours ?? 12, (val) => {
      pushHistory();
      fp.render.chart_history_hours = parseFloat(val) || 12;
    }, { step: 0.5 }));
    chartSection.body.appendChild(renderNumberField('Chart Forecast Hours', fp.render.chart_forecast_hours ?? 12, (val) => {
      pushHistory();
      fp.render.chart_forecast_hours = parseFloat(val) || 12;
    }, { step: 0.5 }));
    chartSection.body.appendChild(renderNumberField('Chart Width (px)', fp.render.chart_width ?? 260, (val) => {
      pushHistory();
      fp.render.chart_width = parseInt(val, 10) || 260;
    }));
    chartSection.body.appendChild(renderNumberField('Chart Height (px)', fp.render.chart_height ?? 80, (val) => {
      pushHistory();
      fp.render.chart_height = parseInt(val, 10) || 80;
    }));
    propertiesPanel.appendChild(chartSection.details);

    const solverSection = createSection('Heat Solver', { open: false });
    const solverHint = document.createElement('div');
    solverHint.className = 'hint';
    solverHint.textContent = 'Advanced: controls how temperatures diffuse between sensors in the rendered heatmap.';
    solverSection.body.appendChild(solverHint);
    fp.solver = fp.solver || defaultSolver();
    solverSection.body.appendChild(renderNumberField('Grid Width (cells)', fp.solver.grid_w ?? 400, (val) => {
      pushHistory();
      fp.solver.grid_w = parseInt(val, 10) || 400;
    }));
    solverSection.body.appendChild(renderNumberField('Grid Height (cells)', fp.solver.grid_h ?? 250, (val) => {
      pushHistory();
      fp.solver.grid_h = parseInt(val, 10) || 250;
    }));
    solverSection.body.appendChild(renderNumberField('Iterations', fp.solver.iterations ?? 500, (val) => {
      pushHistory();
      fp.solver.iterations = parseInt(val, 10) || 500;
    }));
    solverSection.body.appendChild(renderNumberField('Sensor Pull', fp.solver.sensor_pull ?? 1.0, (val) => {
      pushHistory();
      fp.solver.sensor_pull = parseFloat(val) || 1.0;
    }, { step: 0.05 }));
    solverSection.body.appendChild(renderNumberField('Wall Resistance', fp.solver.wall_resistance ?? 500000, (val) => {
      pushHistory();
      fp.solver.wall_resistance = parseFloat(val) || 500000;
    }));
    solverSection.body.appendChild(renderNumberField('Passage Resistance', fp.solver.default_passage_resistance ?? 2, (val) => {
      pushHistory();
      fp.solver.default_passage_resistance = parseFloat(val) || 2;
    }, { step: 0.1 }));
    propertiesPanel.appendChild(solverSection.details);

    if (state.backgroundImage) {
      const bgSection = createSection('Tracing Background', { open: true });
      bgSection.body.appendChild(renderRangeField('Opacity', state.background.opacity, (val) => {
        state.background.opacity = val;
        render();
      }, { min: 0, max: 1, step: 0.05, live: true }));
      bgSection.body.appendChild(renderNumberField('Scale', state.background.scale, (val) => {
        state.background.scale = parseFloat(val) || 1;
        render();
      }, { step: 0.05 }));
      bgSection.body.appendChild(renderNumberField('X Position', state.background.x, (val) => {
        state.background.x = parseFloat(val) || 0;
        render();
      }));
      bgSection.body.appendChild(renderNumberField('Y Position', state.background.y, (val) => {
        state.background.y = parseFloat(val) || 0;
        render();
      }));
      bgSection.body.appendChild(renderActionButton('Remove Background', () => {
        clearBackgroundImage();
      }, { danger: true }));
      propertiesPanel.appendChild(bgSection.details);
    }

    const jsonSection = createSection('Raw JSON', { open: false });
    jsonSection.body.appendChild(renderActionButton('Open JSON Editor', () => {
      openJsonEditor();
    }));
    propertiesPanel.appendChild(jsonSection.details);
    return;
  }
  const item = findById(state.selected.type, state.selected.id);
  if (!item) return;
  const title = document.createElement('h3');
  const badge = document.createElement('span');
  badge.className = 'type-badge';
  badge.textContent = TYPE_LABELS[state.selected.type] || state.selected.type;
  title.appendChild(badge);
  title.appendChild(document.createTextNode('Properties'));
  propertiesPanel.appendChild(title);
  const detailSection = createSection('Details');
  if (state.selected.type === 'wall') {
    detailSection.body.appendChild(renderField('Wall ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    detailSection.body.appendChild(renderReadonlyField('Points', `${item.points.length}`));
    const fpScale = fp.scale?.px_per_meter;
    const lengthPx = polylineLength(item.points);
    const lengthText = fpScale && fpScale > 0
      ? `${lengthPx.toFixed(0)} px • ${(lengthPx / fpScale).toFixed(2)} m`
      : `${lengthPx.toFixed(0)} px`;
    detailSection.body.appendChild(renderReadonlyField('Length', lengthText));
    if (Number.isInteger(state.selected.vertexIndex)) {
      detailSection.body.appendChild(renderReadonlyField('Selected Vertex', `${state.selected.vertexIndex + 1}`));
      detailSection.body.appendChild(renderActionButton('Split Wall at Selected Vertex', () => {
        splitWallAtVertex(item.id, state.selected.vertexIndex);
      }));
    }
    detailSection.body.appendChild(renderActionButton('Merge Touching Wall', () => {
      mergeWallWithNeighbor(item.id);
    }));
  }
  if (state.selected.type === 'door') {
    detailSection.body.appendChild(renderField('Door ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    detailSection.body.appendChild(renderField('Entity ID', item.entity_id || '', (val) => {
      pushHistory();
      item.entity_id = val;
    }));
    detailSection.body.appendChild(renderField('Open Values', item.mapping.open_values.join(','), (val) => {
      pushHistory();
      item.mapping.open_values = val.split(',').map((v) => v.trim()).filter(Boolean);
    }));
    detailSection.body.appendChild(renderField('Closed Values', item.mapping.closed_values.join(','), (val) => {
      pushHistory();
      item.mapping.closed_values = val.split(',').map((v) => v.trim()).filter(Boolean);
    }));
    detailSection.body.appendChild(renderField('Unknown As', item.mapping.unknown_as, (val) => {
      pushHistory();
      item.mapping.unknown_as = val;
    }, ['open', 'closed']));
    detailSection.body.appendChild(renderCheckboxField('Manually Open (no entity)', !!item.open, (val) => {
      pushHistory();
      item.open = val;
    }));
    detailSection.body.appendChild(renderNumberField('Open Resistance', item.open_resistance ?? '', (val) => {
      pushHistory();
      item.open_resistance = val === '' ? null : parseFloat(val);
    }, { allowEmpty: true }));
    detailSection.body.appendChild(renderNumberField('Closed Resistance', item.closed_resistance ?? '', (val) => {
      pushHistory();
      item.closed_resistance = val === '' ? null : parseFloat(val);
    }, { allowEmpty: true }));
  }
  if (state.selected.type === 'sensor') {
    detailSection.body.appendChild(renderField('Sensor ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    detailSection.body.appendChild(renderField('Entity ID', item.entity || '', (val) => {
      pushHistory();
      item.entity = val;
      refreshHaStates();
    }));
    if (item.entity) {
      detailSection.body.appendChild(renderReadonlyField('Current Value', formatTemperatureFromState(item.entity) || 'n/a'));
    }
    detailSection.body.appendChild(renderField('Label', item.label || '', (val) => {
      pushHistory();
      item.label = val;
    }));
    detailSection.body.appendChild(renderNumberField('Weight', item.weight ?? 1.0, (val) => {
      pushHistory();
      item.weight = parseFloat(val) || 1.0;
    }, { step: 0.1 }));
    appendLabelStyleFields(detailSection.body, item, { defaultOffsetX: 10, defaultOffsetY: -8, defaultFontSize: 12 });
  }
  if (state.selected.type === 'thermostat') {
    detailSection.body.appendChild(renderField('Thermostat ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    detailSection.body.appendChild(renderField('Device Label', item.device_label || '', (val) => {
      pushHistory();
      item.device_label = val;
    }));
    detailSection.body.appendChild(renderField('Temperature Entity', item.temperature_entity || '', (val) => {
      pushHistory();
      item.temperature_entity = val;
      refreshHaStates();
    }));
    if (item.temperature_entity) {
      detailSection.body.appendChild(renderReadonlyField('Current Value', formatTemperatureFromState(item.temperature_entity) || 'n/a'));
    }
    detailSection.body.appendChild(renderField('Setpoint Entity', item.setpoint_entity || '', (val) => {
      pushHistory();
      item.setpoint_entity = val;
      refreshHaStates();
    }));
    detailSection.body.appendChild(renderField('Setpoint Low Entity', item.setpoint_low_entity || '', (val) => {
      pushHistory();
      item.setpoint_low_entity = val;
      refreshHaStates();
    }));
    detailSection.body.appendChild(renderField('Setpoint High Entity', item.setpoint_high_entity || '', (val) => {
      pushHistory();
      item.setpoint_high_entity = val;
      refreshHaStates();
    }));
    detailSection.body.appendChild(renderField('Mode Entity', item.mode_entity || '', (val) => {
      pushHistory();
      item.mode_entity = val;
      refreshHaStates();
    }));
    detailSection.body.appendChild(renderField('Fan Entity', item.fan_entity || '', (val) => {
      pushHistory();
      item.fan_entity = val;
      refreshHaStates();
    }));
    detailSection.body.appendChild(renderField('Preview Mode (editor only)', item.preview_mode || 'heat_cool', (val) => {
      pushHistory();
      item.preview_mode = val;
    }, ['heat', 'cool', 'heat_cool', 'auto', 'off']));
    appendLabelStyleFields(detailSection.body, item, { defaultOffsetX: 12, defaultOffsetY: -8, defaultFontSize: 12 });
  }
  if (state.selected.type === 'room_label') {
    detailSection.body.appendChild(renderField('Room Label ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    detailSection.body.appendChild(renderField('Label', item.label || '', (val) => {
      pushHistory();
      item.label = val;
    }));
    appendLabelStyleFields(detailSection.body, item, { defaultOffsetX: 0, defaultOffsetY: 0, defaultFontSize: 16 });
  }
  if (state.selected.type === 'stairwell') {
    detailSection.body.appendChild(renderField('Stairwell ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    detailSection.body.appendChild(renderField('Link To Floor', item.link_to_floor_id || '', (val) => {
      pushHistory();
      item.link_to_floor_id = val || null;
    }));
    detailSection.body.appendChild(renderNumberField('Coupling', item.coupling ?? 0.05, (val) => {
      pushHistory();
      item.coupling = parseFloat(val) || 0.05;
    }, { step: 0.01 }));
  }
  propertiesPanel.appendChild(detailSection.details);

  const actionsSection = createSection('Actions');
  if (state.selected.type !== 'stairwell') {
    actionsSection.body.appendChild(renderActionButton('Duplicate (Ctrl+D)', () => {
      duplicateSelection();
    }));
  }
  actionsSection.body.appendChild(renderActionButton('Delete (Del)', () => {
    removeSelected();
  }, { danger: true }));
  propertiesPanel.appendChild(actionsSection.details);
}

function appendLabelStyleFields(body, item, { defaultOffsetX, defaultOffsetY, defaultFontSize }) {
  body.appendChild(renderNumberField('Font Size', item.font_size || defaultFontSize, (val) => {
    pushHistory();
    item.font_size = parseInt(val, 10) || defaultFontSize;
  }));
  body.appendChild(renderNumberField('Label Offset X', item.label_offset_x ?? defaultOffsetX, (val) => {
    pushHistory();
    item.label_offset_x = parseInt(val, 10) || 0;
  }));
  body.appendChild(renderNumberField('Label Offset Y', item.label_offset_y ?? defaultOffsetY, (val) => {
    pushHistory();
    item.label_offset_y = parseInt(val, 10) || 0;
  }));
  body.appendChild(renderField('Label Justification', item.label_align || 'left', (val) => {
    pushHistory();
    item.label_align = val;
  }, ['left', 'center', 'right']));
}

function renderField(labelText, value, onChange, options = null) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field';
  const label = document.createElement('label');
  label.textContent = labelText;
  let input;
  if (options) {
    input = document.createElement('select');
    options.forEach((optionValue) => {
      const option = document.createElement('option');
      option.value = optionValue;
      option.textContent = optionValue;
      input.appendChild(option);
    });
    input.value = value;
  } else {
    input = document.createElement('input');
    input.value = value;
  }
  input.addEventListener('change', (event) => {
    onChange(event.target.value);
    render();
  });
  wrapper.appendChild(label);
  wrapper.appendChild(input);
  return wrapper;
}

function renderReadonlyField(labelText, value) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field';
  const label = document.createElement('label');
  label.textContent = labelText;
  const input = document.createElement('input');
  input.value = value;
  input.readOnly = true;
  input.tabIndex = -1;
  input.style.opacity = '0.7';
  wrapper.appendChild(label);
  wrapper.appendChild(input);
  return wrapper;
}

function renderNumberField(labelText, value, onChange, { min = null, max = null, step = 1, allowEmpty = false } = {}) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field';
  const label = document.createElement('label');
  label.textContent = labelText;
  const input = document.createElement('input');
  input.type = 'number';
  input.value = value;
  if (min !== null) input.min = min;
  if (max !== null) input.max = max;
  input.step = step;
  input.addEventListener('change', (event) => {
    const raw = event.target.value;
    if (raw === '' && !allowEmpty) {
      event.target.value = value;
      return;
    }
    onChange(raw);
    render();
  });
  wrapper.appendChild(label);
  wrapper.appendChild(input);
  return wrapper;
}

function renderCheckboxField(labelText, checked, onChange) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field checkbox-field';
  const label = document.createElement('label');
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = !!checked;
  input.addEventListener('change', (event) => {
    onChange(event.target.checked);
    render();
  });
  label.appendChild(input);
  label.appendChild(document.createTextNode(labelText));
  wrapper.appendChild(label);
  return wrapper;
}

function renderRangeField(labelText, value, onChange, { min = 0, max = 1, step = 0.05, live = false } = {}) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field range-field';
  const label = document.createElement('label');
  label.textContent = labelText;
  const row = document.createElement('div');
  row.className = 'range-row';
  const input = document.createElement('input');
  input.type = 'range';
  input.min = min;
  input.max = max;
  input.step = step;
  input.value = value;
  const readout = document.createElement('span');
  readout.className = 'range-value';
  readout.textContent = Number(value).toFixed(2);
  input.addEventListener('input', (event) => {
    const next = parseFloat(event.target.value);
    readout.textContent = next.toFixed(2);
    if (live) {
      onChange(next);
    }
  });
  input.addEventListener('change', (event) => {
    const next = parseFloat(event.target.value);
    readout.textContent = next.toFixed(2);
    if (!live) {
      onChange(next);
      render();
    }
  });
  row.appendChild(input);
  row.appendChild(readout);
  wrapper.appendChild(label);
  wrapper.appendChild(row);
  return wrapper;
}

function renderActionButton(labelText, onClick, { danger = false } = {}) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field';
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = labelText;
  if (danger) {
    button.classList.add('danger-btn');
  }
  button.addEventListener('click', onClick);
  wrapper.appendChild(button);
  return wrapper;
}

function createSection(title, { open = true } = {}) {
  const details = document.createElement('details');
  details.open = open;
  const summary = document.createElement('summary');
  summary.textContent = title;
  details.appendChild(summary);
  const body = document.createElement('div');
  body.className = 'section-body';
  details.appendChild(body);
  return { details, body };
}

function normalizeFloorplan(value) {
  const base = createDefaultFloorplan(state.floorId);
  const merged = {
    ...base,
    ...value,
    canvas: { ...base.canvas, ...(value?.canvas || {}) },
    scale: { ...base.scale, ...(value?.scale || {}) },
    render: { ...base.render, ...(value?.render || {}) },
    solver: { ...base.solver, ...(value?.solver || {}) },
  };
  merged.floor_id = state.floorId;
  merged.walls = Array.isArray(value?.walls) ? value.walls : [];
  merged.doors = Array.isArray(value?.doors) ? value.doors : [];
  merged.sensors = Array.isArray(value?.sensors) ? value.sensors : [];
  merged.thermostats = Array.isArray(value?.thermostats) ? value.thermostats : [];
  merged.room_labels = Array.isArray(value?.room_labels) ? value.room_labels : [];
  merged.stairwell = value?.stairwell || null;
  return merged;
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------

function openJsonEditor() {
  if (!jsonModal || !jsonEditor) return;
  jsonEditor.value = JSON.stringify(currentFloorplan(), null, 2);
  jsonModal.classList.remove('hidden');
  jsonEditor.focus();
}

function closeJsonEditor() {
  if (!jsonModal) return;
  jsonModal.classList.add('hidden');
}

let previewTimer = null;

function refreshPreview() {
  if (!previewImg) return;
  previewStatus.textContent = 'Rendering...';
  previewStatus.classList.remove('hidden');
  previewImg.src = `/render/live/${encodeURIComponent(state.floorId)}.png?t=${Date.now()}`;
}

function syncPreviewTimer() {
  if (previewTimer) {
    window.clearInterval(previewTimer);
    previewTimer = null;
  }
  if (previewModal && !previewModal.classList.contains('hidden') && previewAutoRefresh?.checked) {
    previewTimer = window.setInterval(refreshPreview, 10000);
  }
}

function openPreview() {
  if (!previewModal) return;
  previewModal.classList.remove('hidden');
  if (state.dirtyFloors.has(state.floorId)) {
    showToast('Preview shows the last saved version. Use "Save & Refresh" to include your changes.');
  }
  refreshPreview();
  syncPreviewTimer();
}

function closePreview() {
  if (!previewModal) return;
  previewModal.classList.add('hidden');
  syncPreviewTimer();
}

function openHelp() {
  if (helpModal) helpModal.classList.remove('hidden');
}

function closeHelp() {
  if (helpModal) helpModal.classList.add('hidden');
}

function formatLegendColors(value) {
  if (!value || !Array.isArray(value) || !value.length) {
    return '';
  }
  return value.join(', ');
}

function parseLegendColorsInput(value) {
  const colors = value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
  return colors.length ? colors : null;
}

// ---------------------------------------------------------------------------
// Drawing tools
// ---------------------------------------------------------------------------

function setTool(tool) {
  state.tool = tool;
  state.drawing = null;
  updateMeasureReadout('');
  updateToolButtons();
  updateToolHint();
  render();
}

function commitWall(points) {
  const cleaned = dedupePoints(points);
  if (cleaned.length < 2) {
    state.drawing = null;
    updateMeasureReadout('');
    render();
    return;
  }
  const fp = currentFloorplan();
  pushHistory();
  fp.walls.push({ id: ensureId('wall'), points: cleaned });
  state.drawing = null;
  updateMeasureReadout('');
  render();
}

function commitStairwell(points) {
  const cleaned = dedupePoints(points);
  if (cleaned.length < 3) {
    state.drawing = null;
    updateMeasureReadout('');
    render();
    return;
  }
  const fp = currentFloorplan();
  pushHistory();
  fp.stairwell = { id: ensureId('stairwell'), polygon: cleaned, link_to_floor_id: null, coupling: 0.05 };
  state.drawing = null;
  updateMeasureReadout('');
  render();
}

function startDoor(point) {
  state.drawing = { type: 'door', start: point };
}

function commitDoor(start, end) {
  if (pointDistance(start, end) < 1) {
    state.drawing = null;
    updateMeasureReadout('');
    setStatus('Door canceled — drag across an opening to draw one.');
    render();
    return;
  }
  const fp = currentFloorplan();
  pushHistory();
  fp.doors.push({
    id: ensureId('door'),
    segment: [start, end],
    entity_id: '',
    mapping: { open_values: ['on', 'open'], closed_values: ['off', 'closed'], unknown_as: 'closed' },
    open: false,
    open_resistance: null,
    closed_resistance: null,
  });
  state.drawing = null;
  updateMeasureReadout('');
  render();
}

function createSensor(point) {
  const fp = currentFloorplan();
  pushHistory();
  const sensor = {
    id: ensureId('sensor'),
    entity: '',
    pos: point,
    label: '',
    weight: 1.0,
    label_offset_x: 10,
    label_offset_y: -8,
    font_size: 12,
    label_align: 'left',
  };
  fp.sensors.push(sensor);
  state.selected = { type: 'sensor', id: sensor.id };
  renderProperties();
  render();
}

function createThermostat(point) {
  const fp = currentFloorplan();
  pushHistory();
  const thermo = {
    id: ensureId('thermostat'),
    pos: point,
    temperature_entity: '',
    setpoint_entity: '',
    setpoint_low_entity: '',
    setpoint_high_entity: '',
    mode_entity: '',
    fan_entity: '',
    device_label: '',
    label_offset_x: 12,
    label_offset_y: -8,
    font_size: 12,
    preview_mode: 'heat_cool',
    label_align: 'left',
  };
  fp.thermostats.push(thermo);
  state.selected = { type: 'thermostat', id: thermo.id };
  renderProperties();
  render();
}

function createRoomLabel(point) {
  const fp = currentFloorplan();
  pushHistory();
  const label = {
    id: ensureId('room_label'),
    pos: point,
    label: 'Room',
    font_size: 16,
    label_offset_x: 0,
    label_offset_y: 0,
    label_align: 'left',
  };
  fp.room_labels.push(label);
  state.selected = { type: 'room_label', id: label.id };
  renderProperties();
  render();
}

function applyScaleCalibration(p1, p2) {
  const fp = currentFloorplan();
  const distancePx = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
  if (distancePx < 1) {
    setStatus('Scale calibration canceled — points are identical.', true);
    state.drawing = null;
    updateMeasureReadout('');
    render();
    return;
  }
  const input = window.prompt('Enter the distance in meters between the points:', '1');
  const distanceM = parseFloat(input);
  if (!distanceM || distanceM <= 0) {
    setStatus('Scale calibration canceled.', true);
    state.drawing = null;
    updateMeasureReadout('');
    render();
    return;
  }
  pushHistory();
  fp.scale.mode = 'calibrated';
  fp.scale.calibration = { p1, p2, distance_m: distanceM };
  fp.scale.px_per_meter = distancePx / distanceM;
  state.drawing = null;
  updateMeasureReadout('');
  renderProperties();
  render();
  setStatus(`Scale set to ${fp.scale.px_per_meter.toFixed(2)} px/m.`);
}

// ---------------------------------------------------------------------------
// Dragging
// ---------------------------------------------------------------------------

function beginMoveDrag(hit, startWorld, options = {}) {
  const resolvedType = hit.type === 'wall_vertex' ? 'wall' : hit.type;
  const item = findById(resolvedType, hit.id);
  if (!item) return;
  const dragType = options.labelOffset ? 'label_offset' : options.stretch ? 'stretch_wall' : 'move';
  const dragState = {
    type: dragType,
    itemType: hit.type,
    id: hit.id,
    start: startWorld,
    segmentIndex: hit.segmentIndex,
    // Snapshot the pre-drag floorplan so the whole drag becomes one undo step.
    snapshot: deepCopy(currentFloorplan()),
  };
  if (resolvedType === 'sensor' || resolvedType === 'thermostat' || resolvedType === 'room_label') {
    dragState.original = { pos: [...item.pos] };
    if (dragType === 'label_offset') {
      dragState.original.offset = {
        x: item.label_offset_x ?? 0,
        y: item.label_offset_y ?? 0,
      };
    }
  } else if (resolvedType === 'door') {
    dragState.original = { segment: item.segment.map((pt) => [...pt]) };
  } else if (resolvedType === 'wall') {
    dragState.original = { points: item.points.map((pt) => [...pt]) };
    if (hit.type === 'wall_vertex') {
      dragState.vertexIndex = hit.vertexIndex;
    }
    if (dragType === 'stretch_wall') {
      const first = item.points[0];
      const last = item.points[item.points.length - 1];
      const distToStart = pointDistance(startWorld, first);
      const distToEnd = pointDistance(startWorld, last);
      dragState.stretchAnchorIndex = distToStart <= distToEnd ? 0 : item.points.length - 1;
    }
  } else if (resolvedType === 'stairwell') {
    dragState.original = { polygon: item.polygon.map((pt) => [...pt]) };
  }
  state.dragging = dragState;
}

function beginMultiDrag(startWorld) {
  const items = state.multiSelected.map((selection) => {
    const item = findById(selection.type, selection.id);
    if (!item) return null;
    if (selection.type === 'sensor' || selection.type === 'thermostat' || selection.type === 'room_label') {
      return { ...selection, original: { pos: [...item.pos] } };
    }
    if (selection.type === 'door') {
      return { ...selection, original: { segment: item.segment.map((pt) => [...pt]) } };
    }
    if (selection.type === 'wall') {
      return { ...selection, original: { points: item.points.map((pt) => [...pt]) } };
    }
    if (selection.type === 'stairwell') {
      return { ...selection, original: { polygon: item.polygon.map((pt) => [...pt]) } };
    }
    return null;
  }).filter(Boolean);
  state.dragging = {
    type: 'multi_move',
    start: startWorld,
    items,
    snapshot: deepCopy(currentFloorplan()),
  };
}

const MOVE_DRAG_TYPES = ['move', 'label_offset', 'stretch_wall', 'multi_move'];

function finishMoveDrag() {
  if (!state.dragging) return;
  const { snapshot } = state.dragging;
  state.dragging = null;
  if (snapshot && JSON.stringify(snapshot) !== JSON.stringify(currentFloorplan())) {
    pushHistorySnapshot(snapshot);
  }
  renderProperties();
}

function updateMoveDrag(world) {
  if (!state.dragging || !MOVE_DRAG_TYPES.includes(state.dragging.type)) return;
  // Moves never apply orthogonal snapping — that would pin items to one axis.
  const moveOpts = { ortho: false };
  if (state.dragging.type === 'multi_move') {
    const reference = state.dragging.items[0]?.original?.pos || state.dragging.items[0]?.original?.segment?.[0] || state.dragging.items[0]?.original?.points?.[0] || world;
    const snappedReference = resolveSnapPoint(
      [reference[0] + (world[0] - state.dragging.start[0]), reference[1] + (world[1] - state.dragging.start[1])],
      reference,
      [],
      moveOpts,
    );
    const delta = [snappedReference[0] - reference[0], snappedReference[1] - reference[1]];
    state.dragging.items.forEach((selection) => {
      const item = findById(selection.type, selection.id);
      if (!item) return;
      if (selection.type === 'sensor' || selection.type === 'thermostat' || selection.type === 'room_label') {
        item.pos = [selection.original.pos[0] + delta[0], selection.original.pos[1] + delta[1]];
      } else if (selection.type === 'door') {
        item.segment = selection.original.segment.map((pt) => [pt[0] + delta[0], pt[1] + delta[1]]);
      } else if (selection.type === 'wall') {
        item.points = selection.original.points.map((pt) => [pt[0] + delta[0], pt[1] + delta[1]]);
      } else if (selection.type === 'stairwell') {
        item.polygon = selection.original.polygon.map((pt) => [pt[0] + delta[0], pt[1] + delta[1]]);
      }
    });
    render();
    return;
  }
  const delta = [world[0] - state.dragging.start[0], world[1] - state.dragging.start[1]];
  const resolvedType = state.dragging.itemType === 'wall_vertex' ? 'wall' : state.dragging.itemType;
  const item = findById(resolvedType, state.dragging.id);
  if (!item) return;
  if (resolvedType === 'sensor' || resolvedType === 'thermostat' || resolvedType === 'room_label') {
    if (state.dragging.type === 'label_offset' && state.dragging.original.offset) {
      item.label_offset_x = Math.round(state.dragging.original.offset.x + delta[0] * state.view.scale);
      item.label_offset_y = Math.round(state.dragging.original.offset.y + delta[1] * state.view.scale);
    } else {
      const base = state.dragging.original.pos;
      const next = resolveSnapPoint([base[0] + delta[0], base[1] + delta[1]], base, [], moveOpts);
      item.pos = next;
    }
  } else if (resolvedType === 'door') {
    const reference = state.dragging.original.segment[0];
    const snappedReference = resolveSnapPoint([reference[0] + delta[0], reference[1] + delta[1]], reference, [], moveOpts);
    const snappedDelta = [snappedReference[0] - reference[0], snappedReference[1] - reference[1]];
    item.segment = state.dragging.original.segment.map((pt) => [pt[0] + snappedDelta[0], pt[1] + snappedDelta[1]]);
  } else if (resolvedType === 'wall') {
    if (state.dragging.itemType === 'wall_vertex') {
      const updated = state.dragging.original.points.map((pt) => [...pt]);
      const base = state.dragging.original.points[state.dragging.vertexIndex];
      const snapped = resolveSnapPoint(
        [base[0] + delta[0], base[1] + delta[1]],
        base,
        [{ type: 'wall', id: item.id, index: state.dragging.vertexIndex }],
        moveOpts,
      );
      updated[state.dragging.vertexIndex] = snapped;
      item.points = updated;
    } else if (state.dragging.type === 'stretch_wall') {
      const originalPoints = state.dragging.original.points;
      const anchorIndex = state.dragging.stretchAnchorIndex ?? 0;
      const anchor = originalPoints[anchorIndex];
      const endIndex = anchorIndex === 0 ? originalPoints.length - 1 : 0;
      const end = originalPoints[endIndex];
      const axis = [end[0] - anchor[0], end[1] - anchor[1]];
      const axisLength = Math.hypot(axis[0], axis[1]) || 1;
      const axisUnit = [axis[0] / axisLength, axis[1] / axisLength];
      const targetEnd = resolveSnapPoint([end[0] + delta[0], end[1] + delta[1]], end, [
        { type: 'wall', id: item.id, index: endIndex },
      ], moveOpts);
      const newLength = Math.max(1, (targetEnd[0] - anchor[0]) * axisUnit[0] + (targetEnd[1] - anchor[1]) * axisUnit[1]);
      const scale = newLength / axisLength;
      item.points = originalPoints.map((pt) => {
        const vec = [pt[0] - anchor[0], pt[1] - anchor[1]];
        const proj = vec[0] * axisUnit[0] + vec[1] * axisUnit[1];
        const perp = [vec[0] - proj * axisUnit[0], vec[1] - proj * axisUnit[1]];
        return [
          anchor[0] + axisUnit[0] * proj * scale + perp[0],
          anchor[1] + axisUnit[1] * proj * scale + perp[1],
        ];
      });
    } else {
      const reference = state.dragging.original.points[0];
      const snappedReference = resolveSnapPoint([reference[0] + delta[0], reference[1] + delta[1]], reference, [], moveOpts);
      const snappedDelta = [snappedReference[0] - reference[0], snappedReference[1] - reference[1]];
      item.points = state.dragging.original.points.map((pt) => [pt[0] + snappedDelta[0], pt[1] + snappedDelta[1]]);
    }
  } else if (resolvedType === 'stairwell') {
    const reference = state.dragging.original.polygon[0];
    const snappedReference = resolveSnapPoint([reference[0] + delta[0], reference[1] + delta[1]], reference, [], moveOpts);
    const snappedDelta = [snappedReference[0] - reference[0], snappedReference[1] - reference[1]];
    item.polygon = state.dragging.original.polygon.map((pt) => [pt[0] + snappedDelta[0], pt[1] + snappedDelta[1]]);
  }
  render();
}

function mergeWallWithNeighbor(wallId) {
  const fp = currentFloorplan();
  const wall = (fp.walls || []).find((item) => item.id === wallId);
  if (!wall) return;
  const threshold = 10 / state.view.scale;
  let best = null;
  (fp.walls || []).forEach((other) => {
    if (other.id === wall.id) return;
    const combos = [
      { a: wall.points[wall.points.length - 1], b: other.points[0], mode: 'end-start' },
      { a: wall.points[wall.points.length - 1], b: other.points[other.points.length - 1], mode: 'end-end' },
      { a: wall.points[0], b: other.points[0], mode: 'start-start' },
      { a: wall.points[0], b: other.points[other.points.length - 1], mode: 'start-end' },
    ];
    combos.forEach((combo) => {
      const distance = pointDistance(combo.a, combo.b);
      if (distance <= threshold && (!best || distance < best.distance)) {
        best = { other, mode: combo.mode, distance };
      }
    });
  });
  if (!best) {
    showToast('No touching wall found to merge.', 'error');
    return;
  }
  pushHistory();
  let mergedPoints = [];
  if (best.mode === 'end-start') {
    mergedPoints = [...wall.points, ...best.other.points.slice(1)];
  } else if (best.mode === 'end-end') {
    mergedPoints = [...wall.points, ...best.other.points.slice(0, -1).reverse()];
  } else if (best.mode === 'start-start') {
    mergedPoints = [...wall.points.slice().reverse(), ...best.other.points.slice(1)];
  } else if (best.mode === 'start-end') {
    mergedPoints = [...best.other.points, ...wall.points.slice(1)];
  }
  fp.walls = (fp.walls || []).filter((item) => item.id !== wall.id && item.id !== best.other.id);
  const newWall = { id: ensureId('wall'), points: mergedPoints };
  fp.walls.push(newWall);
  state.selected = { type: 'wall', id: newWall.id };
  renderProperties();
  render();
}

function splitWallAtVertex(wallId, vertexIndex) {
  const fp = currentFloorplan();
  const wall = (fp.walls || []).find((item) => item.id === wallId);
  if (!wall) return;
  if (vertexIndex <= 0 || vertexIndex >= wall.points.length - 1) {
    showToast('Select a middle vertex to split the wall.', 'error');
    return;
  }
  pushHistory();
  const leftPoints = wall.points.slice(0, vertexIndex + 1);
  const rightPoints = wall.points.slice(vertexIndex);
  fp.walls = (fp.walls || []).filter((item) => item.id !== wall.id);
  const leftWall = { id: ensureId('wall'), points: leftPoints };
  const rightWall = { id: ensureId('wall'), points: rightPoints };
  fp.walls.push(leftWall, rightWall);
  state.selected = { type: 'wall', id: rightWall.id, vertexIndex: 0 };
  renderProperties();
  render();
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

toolButtons.forEach((btn) => {
  btn.addEventListener('click', () => {
    setTool(btn.dataset.tool);
  });
});

floorTabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    const floorId = tab.dataset.floor;
    switchStory(floorId);
  });
});

loadBtn.addEventListener('click', () => {
  if (!loadSelect.value) return;
  loadFloorplan(loadSelect.value);
});

assignStoryBtn.addEventListener('click', () => {
  if (!loadSelect.value) return;
  const storyLabel = storyLabels[state.storyId] || state.storyId;
  state.storyAssignments[state.storyId] = loadSelect.value;
  saveStoryAssignments();
  setStatus(`Assigned ${loadSelect.value} to ${storyLabel}.`);
  updateStatusMeta();
  showToast(`Assigned ${loadSelect.value} to ${storyLabel}.`);
  ensureFloorplanLoaded(loadSelect.value);
});

newBtn.addEventListener('click', () => {
  pushHistory();
  setFloorplan(createDefaultFloorplan(state.floorId));
  setStatus(`Created new ${state.floorId}.`);
  updateStatusMeta();
  showToast(`Created new ${state.floorId}.`);
  refreshHaStates();
});

deleteBtn.addEventListener('click', () => {
  deleteFloorplan();
});

saveBtn.addEventListener('click', () => {
  saveFloorplan();
});

undoBtn.addEventListener('click', () => {
  undo();
});

redoBtn.addEventListener('click', () => {
  redo();
});

snapToggle.addEventListener('change', (event) => {
  state.snapToGrid = event.target.checked;
  render();
});

orthoToggle.addEventListener('change', (event) => {
  state.orthogonalSnap = event.target.checked;
  render();
});

gridSizeInput.addEventListener('change', (event) => {
  const value = Number(event.target.value);
  if (!Number.isFinite(value) || value <= 0) {
    gridSizeInput.value = state.gridSize;
    return;
  }
  const next = Math.min(200, Math.max(5, value));
  state.gridSize = next;
  gridSizeInput.value = next;
  setStatus(`Grid size set to ${next}px.`);
  updateStatusMeta();
  render();
});

if (jsonCloseBtn) {
  jsonCloseBtn.addEventListener('click', () => {
    closeJsonEditor();
  });
}

if (jsonModal) {
  jsonModal.addEventListener('click', (event) => {
    if (event.target === jsonModal) {
      closeJsonEditor();
    }
  });
}

if (jsonApplyBtn && jsonEditor) {
  jsonApplyBtn.addEventListener('click', () => {
    try {
      const parsed = JSON.parse(jsonEditor.value);
      pushHistory();
      const normalized = normalizeFloorplan(parsed);
      setFloorplan(normalized);
      renderProperties();
      render();
      showToast('Raw JSON applied.');
      closeJsonEditor();
    } catch (error) {
      showToast(`Invalid JSON: ${error.message}`, 'error');
    }
  });
}

if (jsonResetBtn && jsonEditor) {
  jsonResetBtn.addEventListener('click', () => {
    jsonEditor.value = JSON.stringify(currentFloorplan(), null, 2);
  });
}

if (previewBtn) {
  previewBtn.addEventListener('click', openPreview);
}
if (previewCloseBtn) {
  previewCloseBtn.addEventListener('click', closePreview);
}
if (previewRefreshBtn) {
  previewRefreshBtn.addEventListener('click', refreshPreview);
}
if (previewSaveRefreshBtn) {
  previewSaveRefreshBtn.addEventListener('click', async () => {
    const saved = await saveFloorplan();
    if (saved) {
      refreshPreview();
    }
  });
}
if (previewAutoRefresh) {
  previewAutoRefresh.addEventListener('change', syncPreviewTimer);
}
if (previewModal) {
  previewModal.addEventListener('click', (event) => {
    if (event.target === previewModal) {
      closePreview();
    }
  });
}
if (previewImg) {
  previewImg.addEventListener('load', () => {
    previewStatus.classList.add('hidden');
  });
  previewImg.addEventListener('error', () => {
    if (previewModal.classList.contains('hidden')) return;
    previewStatus.textContent = 'Render failed. Save the floorplan first, and check that at least one sensor has an entity bound.';
    previewStatus.classList.remove('hidden');
  });
}

if (helpBtn) {
  helpBtn.addEventListener('click', openHelp);
}
if (helpCloseBtn) {
  helpCloseBtn.addEventListener('click', closeHelp);
}
if (helpModal) {
  helpModal.addEventListener('click', (event) => {
    if (event.target === helpModal) {
      closeHelp();
    }
  });
}

if (zoomInBtn) zoomInBtn.addEventListener('click', () => zoomBy(1.2));
if (zoomOutBtn) zoomOutBtn.addEventListener('click', () => zoomBy(1 / 1.2));
if (zoomFitBtn) zoomFitBtn.addEventListener('click', zoomToFit);
if (zoomResetBtn) zoomResetBtn.addEventListener('click', zoomReset);

canvas.addEventListener('mousedown', (event) => {
  const rect = canvas.getBoundingClientRect();
  const world = screenToWorld(event.clientX - rect.left, event.clientY - rect.top);
  const snapped = snapPoint(world, state.drawing?.points?.slice(-1)[0] || state.drawing?.start);

  if (state.spacePressed || event.button === 1) {
    event.preventDefault();
    state.dragging = {
      type: 'pan',
      start: [event.clientX, event.clientY],
      origin: { x: state.view.x, y: state.view.y },
    };
    return;
  }

  if (event.button !== 0) return;
  if (!currentFloorplan()) return;

  if (state.tool === 'select') {
    const hit = hitTest(world);
    if (hit) {
      state.selectionBox = null;
      const isMultiHit = state.multiSelected.some((item) => item.type === hit.type && item.id === hit.id);
      if (!isMultiHit) {
        if (hit.type === 'wall_vertex') {
          state.selected = { type: 'wall', id: hit.id, vertexIndex: hit.vertexIndex };
        } else {
          state.selected = hit;
        }
        state.multiSelected = [];
      }
      renderProperties();
      const labelOffset = hit.target === 'label'
        || (event.shiftKey && (hit.type === 'sensor' || hit.type === 'thermostat' || hit.type === 'room_label'));
      const stretch = event.shiftKey && hit.type === 'wall';
      if (state.multiSelected.length && isMultiHit) {
        beginMultiDrag(world);
      } else {
        beginMoveDrag(hit, world, { labelOffset, stretch });
      }
    } else {
      state.selected = null;
      state.multiSelected = [];
      state.selectionBox = { start: world, end: world };
      renderProperties();
    }
    render();
    return;
  }

  if (state.tool === 'erase') {
    const hit = hitTest(world);
    if (hit) {
      state.selected = hit.type === 'wall_vertex' ? { type: 'wall', id: hit.id } : hit;
      state.multiSelected = [];
      removeSelected();
      renderProperties();
    }
    return;
  }

  if (state.tool === 'wall') {
    if (!state.drawing || state.drawing.type !== 'wall') {
      state.drawing = { type: 'wall', points: [snapped] };
    } else {
      state.drawing.points.push(snapped);
    }
    render();
    return;
  }

  if (state.tool === 'stairwell') {
    if (!state.drawing || state.drawing.type !== 'stairwell') {
      state.drawing = { type: 'stairwell', points: [snapped] };
    } else {
      state.drawing.points.push(snapped);
    }
    render();
    return;
  }

  if (state.tool === 'door') {
    startDoor(snapped);
    render();
    return;
  }

  if (state.tool === 'sensor') {
    createSensor(snapped);
    return;
  }

  if (state.tool === 'thermostat') {
    createThermostat(snapped);
    return;
  }

  if (state.tool === 'room_label') {
    createRoomLabel(snapped);
    return;
  }

  if (state.tool === 'scale') {
    if (!state.drawing || state.drawing.type !== 'scale') {
      state.drawing = { type: 'scale', start: world };
      setStatus('Click the second point for scale calibration.');
    } else {
      applyScaleCalibration(state.drawing.start, world);
    }
    render();
    return;
  }
});

canvas.addEventListener('mousemove', (event) => {
  const rect = canvas.getBoundingClientRect();
  const world = screenToWorld(event.clientX - rect.left, event.clientY - rect.top);
  updateCoordReadout(world);

  if (state.dragging?.type === 'pan') {
    state.view.x = state.dragging.origin.x + (event.clientX - state.dragging.start[0]);
    state.view.y = state.dragging.origin.y + (event.clientY - state.dragging.start[1]);
    render();
    return;
  }

  if (state.dragging && MOVE_DRAG_TYPES.includes(state.dragging.type)) {
    updateMoveDrag(world);
    return;
  }
  if (state.selectionBox) {
    state.selectionBox.end = world;
    render();
    return;
  }

  if (state.drawing?.type === 'wall' || state.drawing?.type === 'stairwell') {
    const reference = state.drawing.points[state.drawing.points.length - 1];
    state.drawing.previewPoint = snapPoint(world, reference);
    updateMeasureReadout(`Segment: ${formatSegmentMeasure(reference, state.drawing.previewPoint)}`);
    render();
  } else if (state.drawing?.type === 'door') {
    state.drawing.previewPoint = snapPoint(world, state.drawing.start);
    updateMeasureReadout(`Door: ${formatSegmentMeasure(state.drawing.start, state.drawing.previewPoint)}`);
    render();
  } else if (state.drawing?.type === 'scale') {
    state.drawing.previewPoint = world;
    updateMeasureReadout(`Distance: ${pointDistance(state.drawing.start, world).toFixed(0)} px`);
    render();
  }
});

canvas.addEventListener('mouseup', (event) => {
  const rect = canvas.getBoundingClientRect();
  const world = screenToWorld(event.clientX - rect.left, event.clientY - rect.top);
  if (state.dragging?.type === 'pan') {
    state.dragging = null;
    return;
  }
  if (state.dragging && MOVE_DRAG_TYPES.includes(state.dragging.type)) {
    finishMoveDrag();
    return;
  }
  if (state.selectionBox) {
    const { start, end } = state.selectionBox;
    const box = {
      minX: Math.min(start[0], end[0]),
      minY: Math.min(start[1], end[1]),
      maxX: Math.max(start[0], end[0]),
      maxY: Math.max(start[1], end[1]),
    };
    const hits = findItemsInBox(box);
    state.selectionBox = null;
    state.multiSelected = hits;
    state.selected = hits.length === 1 ? hits[0] : null;
    renderProperties();
    render();
    return;
  }
  if (state.tool === 'door' && state.drawing?.type === 'door' && state.drawing.start) {
    const snapped = snapPoint(world, state.drawing.start);
    commitDoor(state.drawing.start, snapped);
  }
});

canvas.addEventListener('mouseleave', () => {
  if (state.dragging?.type === 'pan') {
    state.dragging = null;
  }
  if (state.dragging && MOVE_DRAG_TYPES.includes(state.dragging.type)) {
    finishMoveDrag();
  }
  if (state.selectionBox) {
    state.selectionBox = null;
    render();
  }
  updateCoordReadout(null);
});

canvas.addEventListener('dblclick', () => {
  if (state.drawing?.type === 'wall') {
    commitWall(state.drawing.points);
  } else if (state.drawing?.type === 'stairwell') {
    commitStairwell(state.drawing.points);
  }
});

canvas.addEventListener('wheel', (event) => {
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mouse = [event.clientX - rect.left, event.clientY - rect.top];
  zoomBy(event.deltaY < 0 ? 1.1 : 1 / 1.1, mouse);
}, { passive: false });

window.addEventListener('resize', resizeCanvas);

window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (helpModal && !helpModal.classList.contains('hidden')) {
      closeHelp();
      return;
    }
    if (previewModal && !previewModal.classList.contains('hidden')) {
      closePreview();
      return;
    }
    if (jsonModal && !jsonModal.classList.contains('hidden')) {
      closeJsonEditor();
      return;
    }
  }
  if (isEditableTarget(event.target)) {
    return;
  }
  if (event.code === 'Space') {
    state.spacePressed = true;
    event.preventDefault();
  }
  if (event.key === 'Escape') {
    if (state.drawing?.type === 'wall' || state.drawing?.type === 'stairwell') {
      // Step back one point at a time; cancel entirely once empty.
      state.drawing.points.pop();
      if (!state.drawing.points.length) {
        state.drawing = null;
        updateMeasureReadout('');
      }
      render();
      return;
    }
    if (state.drawing) {
      state.drawing = null;
      updateMeasureReadout('');
      render();
      return;
    }
    if (state.selectionBox) {
      state.selectionBox = null;
      render();
      return;
    }
    if (state.selected || state.multiSelected.length) {
      state.selected = null;
      state.multiSelected = [];
      renderProperties();
      render();
    }
    return;
  }
  if (event.key === 'Enter') {
    if (state.drawing?.type === 'wall') {
      commitWall(state.drawing.points);
      return;
    }
    if (state.drawing?.type === 'stairwell') {
      commitStairwell(state.drawing.points);
      return;
    }
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && (state.selected || state.multiSelected.length)) {
    event.preventDefault();
    removeSelected();
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    if (event.shiftKey) {
      redo();
    } else {
      undo();
    }
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'y') {
    event.preventDefault();
    redo();
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    saveFloorplan();
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'd') {
    event.preventDefault();
    duplicateSelection();
    return;
  }
  if (event.metaKey || event.ctrlKey || event.altKey) {
    return;
  }
  const arrowDeltas = {
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
  };
  if (arrowDeltas[event.key]) {
    const step = event.shiftKey ? state.gridSize : 1;
    const [dx, dy] = arrowDeltas[event.key];
    if (nudgeSelection(dx * step, dy * step)) {
      event.preventDefault();
    }
    return;
  }
  if (event.key === '?') {
    openHelp();
    return;
  }
  const key = event.key.toLowerCase();
  if (TOOL_KEYS[key]) {
    setTool(TOOL_KEYS[key]);
    return;
  }
  if (key === 'g') {
    snapToggle.checked = !snapToggle.checked;
    state.snapToGrid = snapToggle.checked;
    setStatus(`Grid snap ${state.snapToGrid ? 'on' : 'off'}.`);
    return;
  }
  if (key === 'o') {
    orthoToggle.checked = !orthoToggle.checked;
    state.orthogonalSnap = orthoToggle.checked;
    setStatus(`Ortho snap ${state.orthogonalSnap ? 'on' : 'off'}.`);
    return;
  }
  if (key === 'p') {
    openPreview();
    return;
  }
  if (key === 'f') {
    zoomToFit();
    return;
  }
  if (key === '0') {
    zoomReset();
    return;
  }
  if (key === '+' || key === '=') {
    zoomBy(1.2);
    return;
  }
  if (key === '-' || key === '_') {
    zoomBy(1 / 1.2);
  }
});

window.addEventListener('keyup', (event) => {
  if (event.code === 'Space') {
    state.spacePressed = false;
  }
});

window.addEventListener('beforeunload', (event) => {
  if (state.dirtyFloors.size) {
    event.preventDefault();
    event.returnValue = '';
  }
});

function clearBackgroundImage() {
  if (!state.backgroundImage) {
    showToast('No background image to remove.', 'error');
    return;
  }
  state.backgroundImage = null;
  state.background = { x: 0, y: 0, scale: 1.0, opacity: 0.5 };
  renderProperties();
  render();
  updateBackgroundControls();
  setStatus('Background image removed.');
  showToast('Background image removed.');
}

// Background Upload Handlers
const bgUploadBtn = document.getElementById('bgUploadBtn');
const bgUploadInput = document.getElementById('bgUpload');

bgUploadBtn.addEventListener('click', () => {
  bgUploadInput.click();
});

if (bgRemoveBtn) {
  bgRemoveBtn.addEventListener('click', () => {
    clearBackgroundImage();
  });
}

bgUploadInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (event) => {
    const img = new Image();
    img.onload = () => {
      state.backgroundImage = img;
      // Reset defaults when loading new image
      state.background = { x: 0, y: 0, scale: 1.0, opacity: 0.5 };
      renderProperties();
      render();
      updateBackgroundControls();
      setStatus('Background image loaded. Adjust it under "Tracing Background" in the panel.');
    };
    img.src = event.target.result;
  };
  reader.readAsDataURL(file);
  // Allow re-uploading the same file later.
  e.target.value = '';
});

initialize();
