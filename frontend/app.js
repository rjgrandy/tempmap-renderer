const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const propertiesPanel = document.getElementById('properties');
const statusText = document.getElementById('statusText');
const statusMeta = document.getElementById('statusMeta');
const coordReadout = document.getElementById('coordReadout');
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
  drawing: null,
  selected: null,
  multiSelected: [],
  selectionBox: null,
  dragging: null,
  history: { past: [], future: [] },
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

const defaultRender = () => ({
  temp_range_f: { min: 60, max: 80 },
  overlay_alpha: 0.6,
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
  chart_height: 160,
  text_font_size: null,
});

const defaultSolver = () => ({
  grid_w: 400,
  grid_h: 250,
  iterations: 200,
  sensor_pull: 0.15,
  wall_resistance: 5000,
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
  statusMeta.textContent = `Story: ${storyLabel} • Floorplan: ${state.floorId} • Grid: ${state.gridSize}px`;
}

function updateCoordReadout(point) {
  if (!coordReadout) return;
  if (!point) {
    coordReadout.textContent = '';
    return;
  }
  coordReadout.textContent = `X: ${point[0].toFixed(1)} • Y: ${point[1].toFixed(1)}`;
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

function pushHistory() {
  state.history.past.push(deepCopy(currentFloorplan()));
  if (state.history.past.length > 50) {
    state.history.past.shift();
  }
  state.history.future = [];
}

function undo() {
  if (!state.history.past.length) {
    return;
  }
  const previous = state.history.past.pop();
  state.history.future.push(deepCopy(currentFloorplan()));
  setFloorplan(previous);
}

function redo() {
  if (!state.history.future.length) {
    return;
  }
  const next = state.history.future.pop();
  state.history.past.push(deepCopy(currentFloorplan()));
  setFloorplan(next);
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
  updateFloorTabs();
  snapToggle.checked = state.snapToGrid;
  orthoToggle.checked = state.orthogonalSnap;
  gridSizeInput.value = state.gridSize;
  updateStatusMeta();
  resizeCanvas();
  const availableFloorplans = await fetchFloorplanList();
  let initialFloorId = getAssignedFloorplanId(state.storyId);
  if (availableFloorplans.length && !availableFloorplans.includes(initialFloorId)) {
    initialFloorId = availableFloorplans[0];
    state.storyAssignments[state.storyId] = initialFloorId;
    saveStoryAssignments();
  }
  await ensureFloorplanLoaded(initialFloorId, { announce: false });
  pushHistory();
  renderProperties();
  updateBackgroundControls();
  render();
  refreshHaStates();
  if (!state.haPollInterval) {
    state.haPollInterval = window.setInterval(refreshHaStates, 15000);
  }
}

async function fetchFloorplanList() {
  try {
    setStatus('Loading floorplan list...');
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
    loadSelect.value = state.floorId;
    setStatus('Floorplan list loaded.');
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
    const response = await fetch(`/api/floorplans/${floorId}`);
    if (!response.ok) {
      if (response.status === 404 && allowCreate) {
        state.floorId = floorId;
        setFloorplan(createDefaultFloorplan(floorId));
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
    setStatus(`Saving ${fp.floor_id}...`);
    const response = await fetch(`/api/floorplans/${fp.floor_id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fp),
    });
    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `Failed to save ${fp.floor_id}`);
    }
    await response.json();
    setStatus(`Saved ${fp.floor_id}.`);
    updateStatusMeta();
    showToast(`Saved ${fp.floor_id}.`);
    fetchFloorplanList();
  } catch (error) {
    setStatus(error.message || 'Unable to save floorplan.', true);
    showToast(error.message || 'Unable to save floorplan.', 'error');
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
    updateStoryAssignmentsForRename(oldId, trimmed);
    setFloorplan(payload);
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
  const assignedFloorplanId = getAssignedFloorplanId(storyId);
  ensureFloorplanLoaded(assignedFloorplanId);
}

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = rect.height;
  render();
}

function worldToScreen(point) {
  return [point[0] * state.view.scale + state.view.x, point[1] * state.view.scale + state.view.y];
}

function screenToWorld(x, y) {
  return [(x - state.view.x) / state.view.scale, (y - state.view.y) / state.view.scale];
}

function snapPoint(point, reference = null) {
  if (!state.snapToGrid) {
    return point;
  }
  const snapped = [
    Math.round(point[0] / state.gridSize) * state.gridSize,
    Math.round(point[1] / state.gridSize) * state.gridSize,
  ];
  if (reference && state.orthogonalSnap) {
    const dx = Math.abs(snapped[0] - reference[0]);
    const dy = Math.abs(snapped[1] - reference[1]);
    if (dx < dy) {
      snapped[0] = reference[0];
    } else {
      snapped[1] = reference[1];
    }
  }
  return snapped;
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

function resolveSnapPoint(point, reference = null, exclude = []) {
  if (!state.snapToGrid) {
    return point;
  }
  let snapped = snapPoint(point, reference);
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

function ensureId(prefix) {
  return `${prefix}_${Math.random().toString(36).slice(2, 8)}`;
}

function renderGrid(worldBounds) {
  const [left, top, right, bottom] = worldBounds;
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
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
  ctx.strokeStyle = '#4ea1ff';
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
  ctx.fillStyle = '#ffffff';
  (fp.sensors || []).forEach((sensor) => {
    const baseFontSize = sensor.font_size || 12;
    const fontSize = baseFontSize / state.view.scale;
    ctx.font = `${fontSize}px sans-serif`;
    ctx.beginPath();
    ctx.arc(sensor.pos[0], sensor.pos[1], 6 / state.view.scale, 0, Math.PI * 2);
    ctx.fill();
    if (fp.render.show_labels) {
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
  const fp = currentFloorplan();
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
  ctx.strokeStyle = '#4ea1ff';
  ctx.lineWidth = 1 / state.view.scale;
  ctx.setLineDash([6 / state.view.scale, 4 / state.view.scale]);
  ctx.strokeRect(left, top, width, height);
  ctx.restore();
}

function renderDrawingPreview() {
  if (!state.drawing) return;
  ctx.strokeStyle = '#4ea1ff';
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
  } else if (state.drawing.type === 'door' && state.drawing.start) {
    ctx.beginPath();
    ctx.moveTo(state.drawing.start[0], state.drawing.start[1]);
    if (state.drawing.previewPoint) {
      ctx.lineTo(state.drawing.previewPoint[0], state.drawing.previewPoint[1]);
    }
    ctx.stroke();
  }
}

function render() {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#10131c';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const worldLeft = (-state.view.x) / state.view.scale;
  const worldTop = (-state.view.y) / state.view.scale;
  const worldRight = (canvas.width - state.view.x) / state.view.scale;
  const worldBottom = (canvas.height - state.view.y) / state.view.scale;

  ctx.setTransform(state.view.scale, 0, 0, state.view.scale, state.view.x, state.view.y);

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

  ctx.setTransform(1, 0, 0, 1, 0, 0);
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
  ctx.fillStyle = '#ffffff';
  ctx.font = '12px sans-serif';
  ctx.fillText(`${label}: ${tempValue}`, 16, 20);
}

function renderProperties() {
  propertiesPanel.innerHTML = '';
  const fp = currentFloorplan();
  if (!state.selected) {
    if (state.multiSelected.length) {
      const title = document.createElement('h3');
      title.textContent = `Multiple Items Selected (${state.multiSelected.length})`;
      propertiesPanel.appendChild(title);
      return;
    }
    const title = document.createElement('h3');
    title.textContent = 'Floorplan Settings';
    propertiesPanel.appendChild(title);

    const layoutSection = createSection('Canvas & Scale');
    layoutSection.body.appendChild(renderField('Floor ID', fp.floor_id, (val) => {
      renameFloorplan(val);
    }));
    layoutSection.body.appendChild(renderField('Canvas Width', fp.canvas.width, (val) => {
      pushHistory();
      fp.canvas.width = parseInt(val) || 1600;
    }));
    layoutSection.body.appendChild(renderField('Canvas Height', fp.canvas.height, (val) => {
      pushHistory();
      fp.canvas.height = parseInt(val) || 1000;
    }));
    layoutSection.body.appendChild(renderField('Scale (px/m)', fp.scale.px_per_meter.toFixed(2), (val) => {
      pushHistory();
      fp.scale.px_per_meter = parseFloat(val) || fp.scale.px_per_meter;
      render();
    }));
    propertiesPanel.appendChild(layoutSection.details);

    const textSection = createSection('Render Text', { open: false });
    textSection.body.appendChild(renderField('Text Font Size', fp.render.text_font_size ?? '', (val) => {
      pushHistory();
      if (val === '') {
        fp.render.text_font_size = null;
      } else {
        fp.render.text_font_size = Math.max(8, parseInt(val, 10) || 12);
      }
    }));
    propertiesPanel.appendChild(textSection.details);

    const outsideSection = createSection('Date/Time & Outside', { open: false });
    outsideSection.body.appendChild(renderField('Show Timestamp', fp.render.show_timestamp ? 'true' : 'false', (val) => {
      pushHistory();
      fp.render.show_timestamp = val === 'true';
    }, ['true', 'false']));
    outsideSection.body.appendChild(renderField('Show Outside Temp', fp.render.show_outside_temp ? 'true' : 'false', (val) => {
      pushHistory();
      fp.render.show_outside_temp = val === 'true';
    }, ['true', 'false']));
    outsideSection.body.appendChild(renderField('Outside Label', fp.render.outside_temp_label || 'Outside', (val) => {
      pushHistory();
      fp.render.outside_temp_label = val;
    }));
    outsideSection.body.appendChild(renderField('Outside Temp Entity', fp.render.outside_temp_entity || '', (val) => {
      pushHistory();
      fp.render.outside_temp_entity = val;
      refreshHaStates();
    }));
    outsideSection.body.appendChild(renderField('Outside Temp (F) Fallback', fp.render.outside_temp_f ?? '', (val) => {
      pushHistory();
      fp.render.outside_temp_f = val === '' ? null : parseFloat(val);
    }));
    propertiesPanel.appendChild(outsideSection.details);

    const legendSection = createSection('Color Scale', { open: false });
    legendSection.body.appendChild(renderField('Show Legend', fp.render.show_legend ? 'true' : 'false', (val) => {
      pushHistory();
      fp.render.show_legend = val === 'true';
    }, ['true', 'false']));
    legendSection.body.appendChild(renderField('Legend Colors (hex)', formatLegendColors(fp.render.legend_colors), (val) => {
      pushHistory();
      fp.render.legend_colors = parseLegendColorsInput(val);
    }));
    legendSection.body.appendChild(renderField('Scale Min (F)', fp.render.temp_range_f?.min ?? '', (val) => {
      pushHistory();
      const next = parseFloat(val);
      if (Number.isFinite(next)) {
        fp.render.temp_range_f.min = next;
      }
    }));
    legendSection.body.appendChild(renderField('Scale Max (F)', fp.render.temp_range_f?.max ?? '', (val) => {
      pushHistory();
      const next = parseFloat(val);
      if (Number.isFinite(next)) {
        fp.render.temp_range_f.max = next;
      }
    }));
    propertiesPanel.appendChild(legendSection.details);

    const chartSection = createSection('Temperature Chart', { open: false });
    chartSection.body.appendChild(renderField('Show Chart', fp.render.show_chart ? 'true' : 'false', (val) => {
      pushHistory();
      fp.render.show_chart = val === 'true';
    }, ['true', 'false']));
    chartSection.body.appendChild(renderField('Chart Temp Entity', fp.render.chart_temp_entity || '', (val) => {
      pushHistory();
      fp.render.chart_temp_entity = val;
      refreshHaStates();
    }));
    chartSection.body.appendChild(renderField('Chart Forecast Entity', fp.render.chart_forecast_entity || '', (val) => {
      pushHistory();
      fp.render.chart_forecast_entity = val;
    }));
    chartSection.body.appendChild(renderField('Chart History Hours', fp.render.chart_history_hours ?? 12, (val) => {
      pushHistory();
      fp.render.chart_history_hours = parseFloat(val) || 12;
    }));
    chartSection.body.appendChild(renderField('Chart Forecast Hours', fp.render.chart_forecast_hours ?? 12, (val) => {
      pushHistory();
      fp.render.chart_forecast_hours = parseFloat(val) || 12;
    }));
    chartSection.body.appendChild(renderField('Chart Width (px)', fp.render.chart_width ?? 260, (val) => {
      pushHistory();
      fp.render.chart_width = parseInt(val, 10) || 260;
    }));
    chartSection.body.appendChild(renderField('Chart Height (px)', fp.render.chart_height ?? 80, (val) => {
      pushHistory();
      fp.render.chart_height = parseInt(val, 10) || 80;
    }));
    propertiesPanel.appendChild(chartSection.details);

    if (state.backgroundImage) {
      const bgSection = createSection('Tracing Background', { open: false });
      bgSection.body.appendChild(renderField('Opacity', state.background.opacity, (val) => {
        state.background.opacity = parseFloat(val);
        render();
      }));
      bgSection.body.appendChild(renderField('Scale', state.background.scale, (val) => {
        state.background.scale = parseFloat(val);
        render();
      }));
      bgSection.body.appendChild(renderField('X Position', state.background.x, (val) => {
        state.background.x = parseFloat(val);
        render();
      }));
      bgSection.body.appendChild(renderField('Y Position', state.background.y, (val) => {
        state.background.y = parseFloat(val);
        render();
      }));
      bgSection.body.appendChild(renderActionButton('Remove Background', () => {
        clearBackgroundImage();
      }));
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
  title.textContent = `${state.selected.type.toUpperCase()} Properties`;
  propertiesPanel.appendChild(title);
  const detailSection = createSection('Details');
  if (state.selected.type === 'wall') {
    detailSection.body.appendChild(renderField('Wall ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    detailSection.body.appendChild(renderField('Points', `${item.points.length}`, () => {}));
    if (Number.isInteger(state.selected.vertexIndex)) {
      detailSection.body.appendChild(renderField('Selected Vertex', `${state.selected.vertexIndex + 1}`, () => {}));
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
    }));
    detailSection.body.appendChild(renderField('Manual Open', item.open ? 'true' : 'false', (val) => {
      pushHistory();
      item.open = val === 'true';
    }, ['true', 'false']));
    detailSection.body.appendChild(renderField('Open Resistance', item.open_resistance ?? '', (val) => {
      pushHistory();
      item.open_resistance = val === '' ? null : parseFloat(val);
    }));
    detailSection.body.appendChild(renderField('Closed Resistance', item.closed_resistance ?? '', (val) => {
      pushHistory();
      item.closed_resistance = val === '' ? null : parseFloat(val);
    }));
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
    detailSection.body.appendChild(renderField('Label', item.label || '', (val) => {
      pushHistory();
      item.label = val;
    }));
    detailSection.body.appendChild(renderField('Weight', item.weight.toString(), (val) => {
      pushHistory();
      item.weight = parseFloat(val) || 1.0;
    }));
    detailSection.body.appendChild(renderField('Font Size', item.font_size || 12, (val) => {
      pushHistory();
      item.font_size = parseInt(val) || 12;
    }));
    detailSection.body.appendChild(renderField('Label Offset X', item.label_offset_x ?? 10, (val) => {
      pushHistory();
      item.label_offset_x = parseInt(val) || 0;
    }));
    detailSection.body.appendChild(renderField('Label Offset Y', item.label_offset_y ?? -8, (val) => {
      pushHistory();
      item.label_offset_y = parseInt(val) || 0;
    }));
    detailSection.body.appendChild(renderField('Label Justification', item.label_align || 'left', (val) => {
      pushHistory();
      item.label_align = val;
    }, ['left', 'center', 'right']));
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
    detailSection.body.appendChild(renderField('Preview Mode (editor only)', item.preview_mode || 'heat_cool', (val) => {
      pushHistory();
      item.preview_mode = val;
    }, ['heat', 'cool', 'heat_cool', 'auto', 'off']));
    detailSection.body.appendChild(renderField('Font Size', item.font_size || 12, (val) => {
      pushHistory();
      item.font_size = parseInt(val) || 12;
    }));
    detailSection.body.appendChild(renderField('Label Offset X', item.label_offset_x ?? 12, (val) => {
      pushHistory();
      item.label_offset_x = parseInt(val) || 0;
    }));
    detailSection.body.appendChild(renderField('Label Offset Y', item.label_offset_y ?? -8, (val) => {
      pushHistory();
      item.label_offset_y = parseInt(val) || 0;
    }));
    detailSection.body.appendChild(renderField('Label Justification', item.label_align || 'left', (val) => {
      pushHistory();
      item.label_align = val;
    }, ['left', 'center', 'right']));
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
    detailSection.body.appendChild(renderField('Font Size', item.font_size || 16, (val) => {
      pushHistory();
      item.font_size = parseInt(val) || 16;
    }));
    detailSection.body.appendChild(renderField('Label Offset X', item.label_offset_x ?? 0, (val) => {
      pushHistory();
      item.label_offset_x = parseInt(val) || 0;
    }));
    detailSection.body.appendChild(renderField('Label Offset Y', item.label_offset_y ?? 0, (val) => {
      pushHistory();
      item.label_offset_y = parseInt(val) || 0;
    }));
    detailSection.body.appendChild(renderField('Label Justification', item.label_align || 'left', (val) => {
      pushHistory();
      item.label_align = val;
    }, ['left', 'center', 'right']));
  }
  if (state.selected.type === 'stairwell') {
    detailSection.body.appendChild(renderField('Stairwell ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    detailSection.body.appendChild(renderField('Link To Floor', item.link_to_floor_id, (val) => {
      pushHistory();
      item.link_to_floor_id = val;
    }));
    detailSection.body.appendChild(renderField('Coupling', item.coupling.toString(), (val) => {
      pushHistory();
      item.coupling = parseFloat(val) || 0.05;
    }));
  }
  propertiesPanel.appendChild(detailSection.details);
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

function renderActionButton(labelText, onClick) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field';
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = labelText;
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

function setTool(tool) {
  state.tool = tool;
  state.drawing = null;
  updateToolButtons();
  render();
}

function commitWall(points) {
  if (points.length < 2) {
    state.drawing = null;
    return;
  }
  const fp = currentFloorplan();
  pushHistory();
  fp.walls.push({ id: ensureId('wall'), points });
  state.drawing = null;
  render();
}

function commitStairwell(points) {
  if (points.length < 3) {
    state.drawing = null;
    return;
  }
  const fp = currentFloorplan();
  pushHistory();
  fp.stairwell = { id: ensureId('stairwell'), polygon: points, link_to_floor_id: null, coupling: 0.05 };
  state.drawing = null;
  render();
}

function startDoor(point) {
  state.drawing = { type: 'door', start: point };
}

function commitDoor(start, end) {
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
  const input = window.prompt('Enter the distance in meters between the points:', '1');
  const distanceM = parseFloat(input);
  if (!distanceM || distanceM <= 0) {
    setStatus('Scale calibration canceled.', true);
    state.drawing = null;
    return;
  }
  pushHistory();
  fp.scale.mode = 'calibrated';
  fp.scale.calibration = { p1, p2, distance_m: distanceM };
  fp.scale.px_per_meter = distancePx / distanceM;
  state.drawing = null;
  renderProperties();
  render();
  setStatus(`Scale set to ${fp.scale.px_per_meter.toFixed(2)} px/m.`);
}

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
  };
}

function updateMoveDrag(world) {
  if (!state.dragging || !['move', 'label_offset', 'stretch_wall', 'multi_move'].includes(state.dragging.type)) return;
  if (state.dragging.type === 'multi_move') {
    const reference = state.dragging.items[0]?.original?.pos || state.dragging.items[0]?.original?.segment?.[0] || state.dragging.items[0]?.original?.points?.[0] || world;
    const snappedReference = resolveSnapPoint(
      [reference[0] + (world[0] - state.dragging.start[0]), reference[1] + (world[1] - state.dragging.start[1])],
      reference,
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
      const next = resolveSnapPoint([base[0] + delta[0], base[1] + delta[1]], base);
      item.pos = next;
    }
  } else if (resolvedType === 'door') {
    const reference = state.dragging.original.segment[0];
    const snappedReference = resolveSnapPoint([reference[0] + delta[0], reference[1] + delta[1]], reference);
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
      ]);
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
      const snappedReference = resolveSnapPoint([reference[0] + delta[0], reference[1] + delta[1]], reference);
      const snappedDelta = [snappedReference[0] - reference[0], snappedReference[1] - reference[1]];
      item.points = state.dragging.original.points.map((pt) => [pt[0] + snappedDelta[0], pt[1] + snappedDelta[1]]);
    }
  } else if (resolvedType === 'stairwell') {
    const reference = state.dragging.original.polygon[0];
    const snappedReference = resolveSnapPoint([reference[0] + delta[0], reference[1] + delta[1]], reference);
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
  showToast(`Grid size set to ${next}px.`);
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

canvas.addEventListener('mousedown', (event) => {
  const rect = canvas.getBoundingClientRect();
  const world = screenToWorld(event.clientX - rect.left, event.clientY - rect.top);
  const snapped = snapPoint(world, state.drawing?.points?.slice(-1)[0] || state.drawing?.start);

  if (state.spacePressed || event.button === 1) {
    state.dragging = {
      type: 'pan',
      start: [event.clientX, event.clientY],
      origin: { x: state.view.x, y: state.view.y },
    };
    return;
  }

  if (event.button !== 0) return;

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
      state.selected = hit;
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
      state.drawing = { type: 'scale', start: snapped };
      setStatus('Click the second point for scale calibration.');
    } else {
      applyScaleCalibration(state.drawing.start, snapped);
    }
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

  if (state.dragging?.type === 'move') {
    updateMoveDrag(world);
    return;
  }
  if (state.dragging?.type === 'label_offset' || state.dragging?.type === 'stretch_wall' || state.dragging?.type === 'multi_move') {
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
    render();
  } else if (state.drawing?.type === 'door') {
    state.drawing.previewPoint = snapPoint(world, state.drawing.start);
    render();
  } else if (state.drawing?.type === 'scale') {
    state.drawing.previewPoint = world;
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
  if (state.dragging?.type === 'move' || state.dragging?.type === 'label_offset' || state.dragging?.type === 'stretch_wall' || state.dragging?.type === 'multi_move') {
    pushHistory();
    state.dragging = null;
    renderProperties();
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
  if (state.dragging?.type === 'move' || state.dragging?.type === 'label_offset' || state.dragging?.type === 'stretch_wall' || state.dragging?.type === 'multi_move') {
    pushHistory();
    state.dragging = null;
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
  const world = screenToWorld(mouse[0], mouse[1]);
  const zoomFactor = event.deltaY < 0 ? 1.1 : 0.9;
  const newScale = Math.min(4, Math.max(0.25, state.view.scale * zoomFactor));
  state.view.x = mouse[0] - world[0] * newScale;
  state.view.y = mouse[1] - world[1] * newScale;
  state.view.scale = newScale;
  render();
}, { passive: false });

window.addEventListener('resize', resizeCanvas);

window.addEventListener('keydown', (event) => {
  if (isEditableTarget(event.target)) {
    return;
  }
  if (event.code === 'Space') {
    state.spacePressed = true;
  }
  if (event.key === 'Escape') {
    state.drawing = null;
    state.selectionBox = null;
    render();
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && state.selected) {
    event.preventDefault();
    removeSelected();
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    if (event.shiftKey) {
      redo();
    } else {
      undo();
    }
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'y') {
    event.preventDefault();
    redo();
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    saveFloorplan();
  }
});

window.addEventListener('keyup', (event) => {
  if (event.code === 'Space') {
    state.spacePressed = false;
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
      setStatus('Background image loaded. Adjust settings in panel.');
    };
    img.src = event.target.result;
  };
  reader.readAsDataURL(file);
});

initialize();
