const floorSelect = document.getElementById('floorSelect');
const reloadBtn = document.getElementById('reload');
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');

async function fetchFloorplans() {
  const response = await fetch('/api/floorplans');
  const data = await response.json();
  floorSelect.innerHTML = '';
  data.floorplans.forEach((floor) => {
    const option = document.createElement('option');
    option.value = floor;
    option.textContent = floor;
    floorSelect.appendChild(option);
  });
  if (data.floorplans.length) {
    await loadFloorplan(data.floorplans[0]);
  }
}

async function loadFloorplan(floorId) {
  if (!floorId) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  const response = await fetch(`/api/floorplans/${floorId}`);
  const floorplan = await response.json();
  renderFloorplan(floorplan);
  drawLiveOverlay(floorId);
}

function renderFloorplan(fp) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#1b1b1b';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = '#f0f0f0';
  ctx.lineWidth = 2;
  fp.walls.forEach((wall) => {
    ctx.beginPath();
    wall.points.forEach((pt, idx) => {
      if (idx === 0) {
        ctx.moveTo(pt.x, pt.y);
      } else {
        ctx.lineTo(pt.x, pt.y);
      }
    });
    ctx.stroke();
  });
  ctx.fillStyle = '#ffffff';
  fp.sensors.forEach((sensor) => {
    ctx.beginPath();
    ctx.arc(sensor.pos.x, sensor.pos.y, 5, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.strokeStyle = '#f5c542';
  fp.thermostats.forEach((thermo) => {
    ctx.strokeRect(thermo.pos.x - 6, thermo.pos.y - 6, 12, 12);
  });
}

async function drawLiveOverlay(floorId) {
  const img = new Image();
  img.onload = () => {
    ctx.drawImage(img, 0, 0);
  };
  img.src = `/render/live/${floorId}.png?ts=${Date.now()}`;
}

floorSelect.addEventListener('change', () => loadFloorplan(floorSelect.value));
reloadBtn.addEventListener('click', () => loadFloorplan(floorSelect.value));

fetchFloorplans();
