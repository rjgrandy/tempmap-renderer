const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const propertiesPanel = document.getElementById('properties');
const statusText = document.getElementById('statusText');
const loadSelect = document.getElementById('loadSelect');

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
  resizeCanvas();
  fetchFloorplanList();
  renderProperties();
  render();
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
  ctx.font = `${12 / state.view.scale}px sans-serif`;
  (fp.sensors || []).forEach((sensor) => {
    ctx.beginPath();
    ctx.arc(sensor.pos[0], sensor.pos[1], 6 / state.view.scale, 0, Math.PI * 2);
    ctx.fill();
    if (fp.render.show_labels) {
      const label = sensor.label || sensor.entity || '';
      
      const offX = (sensor.label_offset_x || 10) / state.view.scale;
      const offY = (sensor.label_offset_y || -8) / state.view.scale;
      
      ctx.fillText(label, sensor.pos[0] + offX, sensor.pos[1] + offY);
      // Mockup of second line (visual only, real rendering is in backend)
      ctx.fillText("72.0F", sensor.pos[0] + offX, sensor.pos[1] + offY + (14/state.view.scale));
    }
  });
}

function renderThermostats(fp) {
  ctx.strokeStyle = '#f5c542';
  ctx.lineWidth = 2 / state.view.scale;
  ctx.font = `${12 / state.view.scale}px sans-serif`;
  (fp.thermostats || []).forEach((thermo) => {
    ctx.strokeRect(thermo.pos[0] - 7 / state.view.scale, thermo.pos[1] - 7 / state.view.scale, 14 / state.view.scale, 14 / state.view.scale);
    if (fp.render.show_labels) {
      const label = thermo.device_label || 'Thermostat';
      ctx.fillStyle = '#f5c542';
      const offX = (thermo.label_offset_x || 12) / state.view.scale;
      const offY = (thermo.label_offset_y || -8) / state.view.scale;
      
      ctx.fillText(label, thermo.pos[0] + offX, thermo.pos[1] + offY);
      ctx.fillText("72.0 / 74.0", thermo.pos[0] + offX, thermo.pos[1] + offY + (14/state.view.scale));
    }
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
  renderDrawingPreview();
  renderSelection();

  ctx.setTransform(1, 0, 0, 1, 0, 0);
}

function renderProperties() {
  propertiesPanel.innerHTML = '';
  const fp = currentFloorplan();
  const title = document.createElement('h3');
  if (!state.selected) {
    title.textContent = 'Floorplan Settings';
    propertiesPanel.appendChild(title);
    propertiesPanel.appendChild(renderField('Floor ID', fp.floor_id, () => {}));
    propertiesPanel.appendChild(renderField('Canvas', `${fp.canvas.width} x ${fp.canvas.height}`, () => {}));
    propertiesPanel.appendChild(renderField('Scale (px/m)', fp.scale.px_per_meter.toFixed(2), (val) => {
      pushHistory();
      fp.scale.px_per_meter = parseFloat(val) || fp.scale.px_per_meter;
      render();
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
    propertiesPanel.appendChild(renderField('Temperature Entity', item.temperature_entity, (val) => {
      pushHistory();
      item.temperature_entity = val;
    }));
    propertiesPanel.appendChild(renderField('Setpoint Entity', item.setpoint_entity, (val) => {
      pushHistory();
      item.setpoint_entity = val;
    }));
    propertiesPanel.appendChild(renderField('Mode Entity', item.mode_entity || '', (val) => {
      pushHistory();
      item.mode_entity = val;
    }));
    
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
