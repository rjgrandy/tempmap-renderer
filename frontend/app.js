const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const propertiesPanel = document.getElementById('properties');
const statusText = document.getElementById('statusText');
const loadSelect = document.getElementById('loadSelect');
const loadBtn = document.getElementById('loadBtn');
const newBtn = document.getElementById('newBtn');
const saveBtn = document.getElementById('saveBtn');
const undoBtn = document.getElementById('undoBtn');
const redoBtn = document.getElementById('redoBtn');
const snapToggle = document.getElementById('snapToggle');
const orthoToggle = document.getElementById('orthoToggle');

const toolButtons = Array.from(document.querySelectorAll('.tool-button'));
const floorTabs = Array.from(document.querySelectorAll('.tab'));

const state = {
  tool: 'select',
  floorId: 'floor1',
  floorplans: {
    floor1: null,
    floor2: null,
  },
  snapToGrid: true,
  orthogonalSnap: true,
  gridSize: 25,
  view: { x: 40, y: 40, scale: 1 },
  drawing: null,
  selected: null,
  dragging: null,
  history: { past: [], future: [] },
  spacePressed: false,

  // Background Image State
  backgroundImage: null,
  background: { x: 0, y: 0, scale: 1.0, opacity: 0.5 },
};

const defaultRender = () => ({
  temp_range_f: { min: 60, max: 80 },
  overlay_alpha: 0.6,
  show_walls: true,
  show_labels: true,
  show_legend: true,
  show_timestamp: true,
  show_outside_temp: true,
  outside_temp_label: 'Outside',
  outside_temp_entity: '',
  outside_temp_f: 72,
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
  state.drawing = null;
  renderProperties();
  render();
}

function initialize() {
  state.floorplans.floor1 = createDefaultFloorplan('floor1');
  state.floorplans.floor2 = createDefaultFloorplan('floor2');
  pushHistory();
  updateToolButtons();
  updateFloorTabs();
  snapToggle.checked = state.snapToGrid;
  orthoToggle.checked = state.orthogonalSnap;
  resizeCanvas();
  fetchFloorplanList();
  renderProperties();
  render();
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
    loadSelect.innerHTML = '';
    ids.forEach((id) => {
      const option = document.createElement('option');
      option.value = id;
      option.textContent = id;
      loadSelect.appendChild(option);
    });
    if (!ids.includes(state.floorId)) {
      const fallback = document.createElement('option');
      fallback.value = state.floorId;
      fallback.textContent = state.floorId;
      loadSelect.appendChild(fallback);
    }
    loadSelect.value = state.floorId;
    setStatus('Floorplan list loaded.');
  } catch (error) {
    setStatus(error.message || 'Unable to load floorplans.', true);
  }
}

async function loadFloorplan(floorId) {
  try {
    setStatus(`Loading ${floorId}...`);
    const response = await fetch(`/api/floorplans/${floorId}`);
    if (!response.ok) {
      throw new Error(`Failed to load ${floorId} (${response.status})`);
    }
    const payload = await response.json();
    state.floorId = floorId;
    setFloorplan(payload);
    updateFloorTabs();
    loadSelect.value = floorId;
    setStatus(`Loaded ${floorId}.`);
  } catch (error) {
    setStatus(error.message || `Unable to load ${floorId}.`, true);
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
    fetchFloorplanList();
  } catch (error) {
    setStatus(error.message || 'Unable to save floorplan.', true);
  }
}

function updateToolButtons() {
  toolButtons.forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tool === state.tool);
  });
}

function updateFloorTabs() {
  floorTabs.forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.floor === state.floorId);
  });
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

function hitTest(point) {
  const fp = currentFloorplan();
  const threshold = 10 / state.view.scale;
  
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
  for (const wall of (fp.walls || [])) {
    for (let i = 0; i < wall.points.length - 1; i += 1) {
      if (distanceToSegment(point, wall.points[i], wall.points[i + 1]) <= threshold) {
        return { type: 'wall', id: wall.id };
      }
    }
  }
  if (fp.stairwell && pointInPolygon(point, fp.stairwell.polygon)) {
    return { type: 'stairwell', id: fp.stairwell.id };
  }
  return null;
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
  if (!state.selected) {
    return;
  }
  pushHistory();
  const { type, id } = state.selected;
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
  state.selected = null;
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

function renderSensors(fp) {
  ctx.fillStyle = '#ffffff';
  (fp.sensors || []).forEach((sensor) => {
    const fontSize = (sensor.font_size || 12) / state.view.scale;
    ctx.font = `${fontSize}px sans-serif`;
    ctx.beginPath();
    ctx.arc(sensor.pos[0], sensor.pos[1], 6 / state.view.scale, 0, Math.PI * 2);
    ctx.fill();
    if (fp.render.show_labels) {
      const label = sensor.label || sensor.entity || '';
      
      const offX = (sensor.label_offset_x || 10) / state.view.scale;
      const offY = (sensor.label_offset_y || -8) / state.view.scale;
      
      ctx.fillText(label, sensor.pos[0] + offX, sensor.pos[1] + offY);
      // Mockup of second line (visual only, real rendering is in backend)
      ctx.fillText('72.0F', sensor.pos[0] + offX, sensor.pos[1] + offY + ((sensor.font_size + 2) / state.view.scale));
    }
  });
}

function renderThermostats(fp) {
  ctx.strokeStyle = '#f5c542';
  ctx.lineWidth = 2 / state.view.scale;
  (fp.thermostats || []).forEach((thermo) => {
    const fontSize = (thermo.font_size || 12) / state.view.scale;
    ctx.font = `${fontSize}px sans-serif`;
    ctx.strokeRect(thermo.pos[0] - 7 / state.view.scale, thermo.pos[1] - 7 / state.view.scale, 14 / state.view.scale, 14 / state.view.scale);
    if (fp.render.show_labels) {
      const label = thermo.device_label || 'Thermostat';
      ctx.fillStyle = '#f5c542';
      const offX = (thermo.label_offset_x || 12) / state.view.scale;
      const offY = (thermo.label_offset_y || -8) / state.view.scale;

      const mode = (thermo.preview_mode || 'heat_cool').toLowerCase();
      const tempLine = '72.0F';
      const setpointLine = mode === 'heat'
        ? '68.0F'
        : mode === 'cool'
          ? '74.0F'
          : '68.0F / 74.0F';

      ctx.fillText(label, thermo.pos[0] + offX, thermo.pos[1] + offY);
      ctx.fillText(
        `${tempLine} / ${setpointLine}${mode ? ` (${mode})` : ''}`,
        thermo.pos[0] + offX,
        thermo.pos[1] + offY + ((thermo.font_size + 2) / state.view.scale),
      );
    }
  });
}

function renderRoomLabels(fp) {
  ctx.fillStyle = '#ffffff';
  (fp.room_labels || []).forEach((label) => {
    if (!label.label) return;
    const fontSize = (label.font_size || 16) / state.view.scale;
    ctx.font = `${fontSize}px sans-serif`;
    const offX = (label.label_offset_x || 0) / state.view.scale;
    const offY = (label.label_offset_y || 0) / state.view.scale;
    ctx.fillText(label.label, label.pos[0] + offX, label.pos[1] + offY);
  });
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
  if (!state.selected) return;
  const fp = currentFloorplan();
  const item = findById(state.selected.type, state.selected.id);
  if (!item) return;
  ctx.strokeStyle = '#ff6b6b';
  ctx.lineWidth = 2 / state.view.scale;
  if (state.selected.type === 'sensor' || state.selected.type === 'thermostat') {
    const pos = item.pos;
    ctx.strokeRect(pos[0] - 10 / state.view.scale, pos[1] - 10 / state.view.scale, 20 / state.view.scale, 20 / state.view.scale);
  } else if (state.selected.type === 'room_label') {
    const pos = item.pos;
    ctx.strokeRect(pos[0] - 12 / state.view.scale, pos[1] - 12 / state.view.scale, 24 / state.view.scale, 24 / state.view.scale);
  } else if (state.selected.type === 'door') {
    ctx.beginPath();
    ctx.moveTo(item.segment[0][0], item.segment[0][1]);
    ctx.lineTo(item.segment[1][0], item.segment[1][1]);
    ctx.stroke();
  } else if (state.selected.type === 'wall') {
    ctx.beginPath();
    item.points.forEach((pt, idx) => {
      if (idx === 0) {
        ctx.moveTo(pt[0], pt[1]);
      } else {
        ctx.lineTo(pt[0], pt[1]);
      }
    });
    ctx.stroke();
  } else if (state.selected.type === 'stairwell') {
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
  if (fp.render.outside_temp_f !== null && fp.render.outside_temp_f !== undefined) {
    const outsideTemp = Number(fp.render.outside_temp_f);
    if (Number.isFinite(outsideTemp)) {
      tempValue = `${outsideTemp.toFixed(1)}F`;
    }
  } else if (fp.render.outside_temp_entity) {
    tempValue = 'n/a';
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
  const title = document.createElement('h3');
  if (!state.selected) {
    title.textContent = 'Floorplan Settings';
    propertiesPanel.appendChild(title);
    propertiesPanel.appendChild(renderField('Floor ID', fp.floor_id, () => {}));
    // NEW: Editable Canvas Dimensions
    propertiesPanel.appendChild(renderField('Canvas Width', fp.canvas.width, (val) => {
      pushHistory();
      fp.canvas.width = parseInt(val) || 1600;
      // Note: Changing canvas size changes the grid density relative to geometry
      // but preserves the geometry coordinates.
    }));
    propertiesPanel.appendChild(renderField('Canvas Height', fp.canvas.height, (val) => {
      pushHistory();
      fp.canvas.height = parseInt(val) || 1000;
    }));
    propertiesPanel.appendChild(renderField('Scale (px/m)', fp.scale.px_per_meter.toFixed(2), (val) => {
      pushHistory();
      fp.scale.px_per_meter = parseFloat(val) || fp.scale.px_per_meter;
      render();
    }));

    const outsideTitle = document.createElement('h3');
    outsideTitle.textContent = 'Outside Temperature';
    outsideTitle.style.marginTop = '20px';
    propertiesPanel.appendChild(outsideTitle);
    propertiesPanel.appendChild(renderField('Show Outside Temp', fp.render.show_outside_temp ? 'true' : 'false', (val) => {
      pushHistory();
      fp.render.show_outside_temp = val === 'true';
    }, ['true', 'false']));
    propertiesPanel.appendChild(renderField('Outside Label', fp.render.outside_temp_label || 'Outside', (val) => {
      pushHistory();
      fp.render.outside_temp_label = val;
    }));
    propertiesPanel.appendChild(renderField('Outside Temp Entity', fp.render.outside_temp_entity || '', (val) => {
      pushHistory();
      fp.render.outside_temp_entity = val;
    }));
    propertiesPanel.appendChild(renderField('Outside Temp (F)', fp.render.outside_temp_f ?? '', (val) => {
      pushHistory();
      fp.render.outside_temp_f = val === '' ? null : parseFloat(val);
    }));

    if (state.backgroundImage) {
      const bgTitle = document.createElement('h3');
      bgTitle.textContent = 'Tracing Background';
      bgTitle.style.marginTop = '20px';
      propertiesPanel.appendChild(bgTitle);

      propertiesPanel.appendChild(renderField('Opacity', state.background.opacity, (val) => {
        state.background.opacity = parseFloat(val);
        render();
      }));
      propertiesPanel.appendChild(renderField('Scale', state.background.scale, (val) => {
        state.background.scale = parseFloat(val);
        render();
      }));
      propertiesPanel.appendChild(renderField('X Position', state.background.x, (val) => {
        state.background.x = parseFloat(val);
        render();
      }));
      propertiesPanel.appendChild(renderField('Y Position', state.background.y, (val) => {
        state.background.y = parseFloat(val);
        render();
      }));
    }
    return;
  }
  const item = findById(state.selected.type, state.selected.id);
  if (!item) return;
  title.textContent = `${state.selected.type.toUpperCase()} Properties`;
  propertiesPanel.appendChild(title);
  if (state.selected.type === 'wall') {
    propertiesPanel.appendChild(renderField('Wall ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    propertiesPanel.appendChild(renderField('Points', `${item.points.length}`, () => {}));
  }
  if (state.selected.type === 'door') {
    propertiesPanel.appendChild(renderField('Door ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    propertiesPanel.appendChild(renderField('Entity ID', item.entity_id || '', (val) => {
      pushHistory();
      item.entity_id = val;
    }));
    propertiesPanel.appendChild(renderField('Open Values', item.mapping.open_values.join(','), (val) => {
      pushHistory();
      item.mapping.open_values = val.split(',').map((v) => v.trim()).filter(Boolean);
    }));
    propertiesPanel.appendChild(renderField('Closed Values', item.mapping.closed_values.join(','), (val) => {
      pushHistory();
      item.mapping.closed_values = val.split(',').map((v) => v.trim()).filter(Boolean);
    }));
    propertiesPanel.appendChild(renderField('Unknown As', item.mapping.unknown_as, (val) => {
      pushHistory();
      item.mapping.unknown_as = val;
    }));
    propertiesPanel.appendChild(renderField('Manual Open', item.open ? 'true' : 'false', (val) => {
      pushHistory();
      item.open = val === 'true';
    }, ['true', 'false']));
    propertiesPanel.appendChild(renderField('Open Resistance', item.open_resistance ?? '', (val) => {
      pushHistory();
      item.open_resistance = val === '' ? null : parseFloat(val);
    }));
    propertiesPanel.appendChild(renderField('Closed Resistance', item.closed_resistance ?? '', (val) => {
      pushHistory();
      item.closed_resistance = val === '' ? null : parseFloat(val);
    }));
  }
  if (state.selected.type === 'sensor') {
    propertiesPanel.appendChild(renderField('Sensor ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    propertiesPanel.appendChild(renderField('Entity ID', item.entity || '', (val) => {
      pushHistory();
      item.entity = val;
    }));
    propertiesPanel.appendChild(renderField('Label', item.label || '', (val) => {
      pushHistory();
      item.label = val;
    }));
    propertiesPanel.appendChild(renderField('Weight', item.weight.toString(), (val) => {
      pushHistory();
      item.weight = parseFloat(val) || 1.0;
    }));
    
    // NEW: Font Size & Position
    propertiesPanel.appendChild(renderField('Font Size', item.font_size || 12, (val) => {
        pushHistory();
        item.font_size = parseInt(val) || 12;
    }));
    propertiesPanel.appendChild(renderField('Label Offset X', item.label_offset_x || 10, (val) => {
        pushHistory();
        item.label_offset_x = parseInt(val) || 0;
    }));
    propertiesPanel.appendChild(renderField('Label Offset Y', item.label_offset_y || -8, (val) => {
        pushHistory();
        item.label_offset_y = parseInt(val) || 0;
    }));
  }
  if (state.selected.type === 'thermostat') {
    propertiesPanel.appendChild(renderField('Thermostat ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    propertiesPanel.appendChild(renderField('Device Label', item.device_label || '', (val) => {
      pushHistory();
      item.device_label = val;
    }));
    propertiesPanel.appendChild(renderField('Temperature Entity', item.temperature_entity || '', (val) => {
      pushHistory();
      item.temperature_entity = val;
    }));
    propertiesPanel.appendChild(renderField('Setpoint Entity', item.setpoint_entity || '', (val) => {
      pushHistory();
      item.setpoint_entity = val;
    }));
    propertiesPanel.appendChild(renderField('Setpoint Low Entity', item.setpoint_low_entity || '', (val) => {
      pushHistory();
      item.setpoint_low_entity = val;
    }));
    propertiesPanel.appendChild(renderField('Setpoint High Entity', item.setpoint_high_entity || '', (val) => {
      pushHistory();
      item.setpoint_high_entity = val;
    }));
    propertiesPanel.appendChild(renderField('Mode Entity', item.mode_entity || '', (val) => {
      pushHistory();
      item.mode_entity = val;
    }));
    propertiesPanel.appendChild(renderField('Preview Mode', item.preview_mode || 'heat_cool', (val) => {
      pushHistory();
      item.preview_mode = val;
    }, ['heat', 'cool', 'heat_cool', 'auto', 'off']));
    
    // NEW: Font Size & Position
    propertiesPanel.appendChild(renderField('Font Size', item.font_size || 12, (val) => {
        pushHistory();
        item.font_size = parseInt(val) || 12;
    }));
    propertiesPanel.appendChild(renderField('Label Offset X', item.label_offset_x || 12, (val) => {
        pushHistory();
        item.label_offset_x = parseInt(val) || 0;
    }));
    propertiesPanel.appendChild(renderField('Label Offset Y', item.label_offset_y || -8, (val) => {
        pushHistory();
        item.label_offset_y = parseInt(val) || 0;
    }));
  }
  if (state.selected.type === 'room_label') {
    propertiesPanel.appendChild(renderField('Room Label ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    propertiesPanel.appendChild(renderField('Label', item.label || '', (val) => {
      pushHistory();
      item.label = val;
    }));
    propertiesPanel.appendChild(renderField('Font Size', item.font_size || 16, (val) => {
      pushHistory();
      item.font_size = parseInt(val) || 16;
    }));
    propertiesPanel.appendChild(renderField('Label Offset X', item.label_offset_x || 0, (val) => {
      pushHistory();
      item.label_offset_x = parseInt(val) || 0;
    }));
    propertiesPanel.appendChild(renderField('Label Offset Y', item.label_offset_y || 0, (val) => {
      pushHistory();
      item.label_offset_y = parseInt(val) || 0;
    }));
  }
  if (state.selected.type === 'stairwell') {
    propertiesPanel.appendChild(renderField('Stairwell ID', item.id, (val) => {
      pushHistory();
      item.id = val;
    }));
    propertiesPanel.appendChild(renderField('Link To Floor', item.link_to_floor_id, (val) => {
      pushHistory();
      item.link_to_floor_id = val;
    }));
    propertiesPanel.appendChild(renderField('Coupling', item.coupling.toString(), (val) => {
      pushHistory();
      item.coupling = parseFloat(val) || 0.05;
    }));
  }
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

function beginMoveDrag(hit, startWorld) {
  const item = findById(hit.type, hit.id);
  if (!item) return;
  const dragState = { type: 'move', itemType: hit.type, id: hit.id, start: startWorld };
  if (hit.type === 'sensor' || hit.type === 'thermostat' || hit.type === 'room_label') {
    dragState.original = { pos: [...item.pos] };
  } else if (hit.type === 'door') {
    dragState.original = { segment: item.segment.map((pt) => [...pt]) };
  } else if (hit.type === 'wall') {
    dragState.original = { points: item.points.map((pt) => [...pt]) };
  } else if (hit.type === 'stairwell') {
    dragState.original = { polygon: item.polygon.map((pt) => [...pt]) };
  }
  state.dragging = dragState;
}

function updateMoveDrag(world) {
  if (!state.dragging || state.dragging.type !== 'move') return;
  const delta = [world[0] - state.dragging.start[0], world[1] - state.dragging.start[1]];
  const item = findById(state.dragging.itemType, state.dragging.id);
  if (!item) return;
  if (state.dragging.itemType === 'sensor' || state.dragging.itemType === 'thermostat' || state.dragging.itemType === 'room_label') {
    item.pos = [state.dragging.original.pos[0] + delta[0], state.dragging.original.pos[1] + delta[1]];
  } else if (state.dragging.itemType === 'door') {
    item.segment = state.dragging.original.segment.map((pt) => [pt[0] + delta[0], pt[1] + delta[1]]);
  } else if (state.dragging.itemType === 'wall') {
    item.points = state.dragging.original.points.map((pt) => [pt[0] + delta[0], pt[1] + delta[1]]);
  } else if (state.dragging.itemType === 'stairwell') {
    item.polygon = state.dragging.original.polygon.map((pt) => [pt[0] + delta[0], pt[1] + delta[1]]);
  }
  render();
}

toolButtons.forEach((btn) => {
  btn.addEventListener('click', () => {
    setTool(btn.dataset.tool);
  });
});

floorTabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    state.floorId = tab.dataset.floor;
    state.selected = null;
    state.drawing = null;
    updateFloorTabs();
    renderProperties();
    render();
  });
});

loadBtn.addEventListener('click', () => {
  if (!loadSelect.value) return;
  loadFloorplan(loadSelect.value);
});

newBtn.addEventListener('click', () => {
  pushHistory();
  setFloorplan(createDefaultFloorplan(state.floorId));
  setStatus(`Created new ${state.floorId}.`);
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
});

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
      state.selected = hit;
      renderProperties();
      beginMoveDrag(hit, world);
    } else {
      state.selected = null;
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
  if (state.dragging?.type === 'move') {
    pushHistory();
    state.dragging = null;
    renderProperties();
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
  if (state.dragging?.type === 'move') {
    pushHistory();
    state.dragging = null;
  }
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
  if (event.code === 'Space') {
    state.spacePressed = true;
  }
  if (event.key === 'Escape') {
    state.drawing = null;
    render();
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && state.selected) {
    removeSelected();
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
    if (event.shiftKey) {
      redo();
    } else {
      undo();
    }
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'y') {
    redo();
  }
});

window.addEventListener('keyup', (event) => {
  if (event.code === 'Space') {
    state.spacePressed = false;
  }
});

// Background Upload Handlers
const bgUploadBtn = document.getElementById('bgUploadBtn');
const bgUploadInput = document.getElementById('bgUpload');

bgUploadBtn.addEventListener('click', () => {
  bgUploadInput.click();
});

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
      setStatus('Background image loaded. Adjust settings in panel.');
    };
    img.src = event.target.result;
  };
  reader.readAsDataURL(file);
});

initialize();
