const DAY = 24 * 60 * 60 * 1000;
const HOUR = DAY / 24;
const dateFormat = new Intl.DateTimeFormat('en-US', {
  month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'
});
const monthFormat = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' });

function timeTicks(start, end, width) {
  const ticks = [];
  const span = end - start;
  if (span >= 60 * DAY) {
    const first = new Date(start);
    let time = Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), 1);
    while (time <= end) {
      if (time >= start) ticks.push(time);
      const date = new Date(time);
      time = Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
    }
  } else {
    const steps = span <= 2 * DAY ? [HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR] : [DAY, 2 * DAY, 7 * DAY, 14 * DAY];
    const step = steps.find(value => span / value <= width / 65) || steps.at(-1);
    for (let time = Math.ceil(start / step) * step; time <= end; time += step) ticks.push(time);
  }
  return ticks;
}

export function renderVisibilityChart(parent, visibilityData, signal) {
  const hourly = [];
  const byDate = new Map();
  for (const [key, value] of Object.entries(visibilityData).sort()) {
    const date = key.slice(0, 10);
    const time = Date.parse(`${date}T${key.slice(11, 13)}:00:00Z`);
    if (!Number.isFinite(time)) continue;
    if (!byDate.has(date)) byDate.set(date, []);
    if (value.visibility_ft == null || !Number.isFinite(value.visibility_ft)) continue;
    hourly.push({ time, vis: value.visibility_ft, kind: 'Snapshot' });
    byDate.get(date).push(value.visibility_ft);
  }
  if (!hourly.length) return;
  const dates = [...byDate.keys()];
  const fullStart = Date.parse(`${dates[0]}T00:00:00Z`);
  const fullEnd = Date.parse(`${dates.at(-1)}T00:00:00Z`) + DAY;
  const daily = [];
  for (let time = fullStart; time < fullEnd; time += DAY) {
    const values = byDate.get(new Date(time).toISOString().slice(0, 10)) || [];
    daily.push({
      time: time + 12 * HOUR, count: values.length, kind: 'Daily average',
      vis: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
    });
  }

  const section = document.createElement('section');
  section.className = 'visibility-chart';
  section.innerHTML = `
    <h2>La Jolla Pier Visibility Trend</h2>
    <div class="chart-toolbar" role="group" aria-label="Chart date range"></div>
    <div class="chart-plot">
      <canvas tabindex="0" role="img" aria-label="Interactive underwater visibility chart, in feet"></canvas>
      <div class="chart-tooltip" hidden aria-hidden="true"></div>
    </div>
    <div class="chart-legend">
      <label><input type="checkbox" checked name="hourly"><span class="chart-key hourly"></span>Hourly estimates</label>
      <label><input type="checkbox" checked name="daily"><span class="chart-key"></span>Daily average</label>
    </div>
    <div class="chart-announcement" role="status" aria-live="polite"></div>`;
  parent.appendChild(section);
  const toolbar = section.querySelector('.chart-toolbar');
  const canvas = section.querySelector('canvas');
  const tooltip = section.querySelector('.chart-tooltip');
  const announcement = section.querySelector('.chart-announcement');
  const hourlyToggle = section.querySelector('[name="hourly"]');
  const dailyToggle = section.querySelector('[name="daily"]');
  const ctx = canvas.getContext('2d');
  const pad = { left: 47, right: 16, top: 18, bottom: 52 };
  let start = fullStart, end = fullEnd;
  let width = 0, height = 0, frame = 0, hovered = null, drag = null;

  function button(label, onClick) {
    const element = document.createElement('button');
    element.type = 'button';
    element.textContent = label;
    element.addEventListener('click', onClick);
    toolbar.appendChild(element);
    return element;
  }
  const presets = [['All', null], ['1 year', 365], ['90 days', 90], ['30 days', 30]].map(([label, days]) => {
    const from = days ? Math.max(fullStart, fullEnd - days * DAY) : fullStart;
    return { from, button: button(label, () => setWindow(from, fullEnd)) };
  });
  const zoomIn = button('+', () => zoom(0.5));
  zoomIn.setAttribute('aria-label', 'Zoom in');
  const zoomOut = button('−', () => zoom(2));
  zoomOut.setAttribute('aria-label', 'Zoom out');
  const zoomControls = document.createElement('span');
  zoomControls.className = 'chart-zoom';
  zoomControls.append(zoomIn, zoomOut);
  toolbar.appendChild(zoomControls);
  const range = document.createElement('span');
  range.className = 'chart-range';
  toolbar.appendChild(range);

  function setWindow(from, to) {
    const span = Math.max(DAY, Math.min(fullEnd - fullStart, to - from));
    start = Math.max(fullStart, Math.min(fullEnd - span, from));
    end = start + span;
    hovered = null;
    tooltip.hidden = true;
    range.textContent = `${dateFormat.format(start)} – ${dateFormat.format(end - 1)}`;
    for (const preset of presets) {
      preset.button.setAttribute('aria-pressed', String(Math.abs(preset.from - start) < 1 && end === fullEnd));
    }
    zoomIn.disabled = span <= DAY;
    zoomOut.disabled = span >= fullEnd - fullStart;
    scheduleDraw();
  }

  function zoom(factor, fraction = 0.5) {
    const span = Math.max(DAY, Math.min(fullEnd - fullStart, (end - start) * factor));
    const anchor = start + (end - start) * fraction;
    setWindow(anchor - span * fraction, anchor + span * (1 - fraction));
  }

  function x(time) { return pad.left + (time - start) / (end - start) * (width - pad.left - pad.right); }
  function y(vis) { return height - pad.bottom - vis / 40 * (height - pad.top - pad.bottom); }
  function color(vis) { return vis >= 20 ? '#18853f' : vis >= 12 ? '#087bb5' : '#c76a16'; }
  function points() {
    return [...(hourlyToggle.checked ? hourly : []), ...(dailyToggle.checked ? daily.filter(point => point.vis != null) : [])];
  }
  function scheduleDraw() {
    if (!frame) frame = requestAnimationFrame(draw);
  }

  function draw() {
    frame = 0;
    const rect = canvas.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.font = '12px Arial';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let value = 0; value <= 40; value += 5) {
      ctx.strokeStyle = '#e6eaed';
      ctx.beginPath();
      ctx.moveTo(pad.left, y(value));
      ctx.lineTo(width - pad.right, y(value));
      ctx.stroke();
      if (value % 10 === 0) {
        ctx.fillStyle = '#555';
        ctx.fillText(`${value} ft`, pad.left - 8, y(value));
      }
    }
    const ticks = timeTicks(start, end, width - pad.left - pad.right);
    const monthly = end - start >= 60 * DAY;
    const labelStep = Math.max(1, Math.ceil(ticks.length * (monthly ? 42 : 60) / (width - pad.left - pad.right)));
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ticks.forEach((time, index) => {
      ctx.strokeStyle = '#edf0f2';
      ctx.beginPath();
      ctx.moveTo(x(time), pad.top);
      ctx.lineTo(x(time), height - pad.bottom + 5);
      ctx.stroke();
      if (index % labelStep) return;
      const date = new Date(time);
      const month = monthFormat.format(date);
      const label = monthly ? month : end - start <= 2 * DAY ? `${String(date.getUTCHours()).padStart(2, '0')}:00` : `${month} ${date.getUTCDate()}`;
      const labelX = Math.max(pad.left + 12, Math.min(width - pad.right - 12, x(time)));
      ctx.fillStyle = '#444';
      ctx.fillText(label, labelX, height - pad.bottom + 10);
      ctx.fillStyle = '#777';
      ctx.fillText(monthly ? date.getUTCFullYear() : end - start <= 2 * DAY ? `${month} ${date.getUTCDate()}` : '', labelX, height - pad.bottom + 26);
    });

    ctx.save();
    ctx.beginPath();
    ctx.rect(pad.left, pad.top, width - pad.left - pad.right, height - pad.top - pad.bottom);
    ctx.clip();
    if (hourlyToggle.checked) {
      ctx.globalAlpha = 0.5;
      for (const point of hourly) {
        if (point.time < start || point.time > end) continue;
        ctx.beginPath();
        ctx.arc(x(point.time), y(point.vis), 2.5, 0, 2 * Math.PI);
        ctx.fillStyle = color(point.vis);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    if (dailyToggle.checked) {
      ctx.strokeStyle = '#222';
      ctx.lineWidth = 2;
      ctx.beginPath();
      let connected = false;
      for (const point of daily) {
        if (point.vis == null) { connected = false; continue; }
        if (connected) ctx.lineTo(x(point.time), y(point.vis));
        else ctx.moveTo(x(point.time), y(point.vis));
        connected = true;
      }
      ctx.stroke();
      for (const point of daily) {
        if (point.vis == null || point.time < start || point.time > end) continue;
        ctx.beginPath();
        ctx.arc(x(point.time), y(point.vis), 2, 0, 2 * Math.PI);
        ctx.fillStyle = '#222';
        ctx.fill();
      }
    }
    if (hovered) {
      ctx.strokeStyle = '#536e7b';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x(hovered.time), pad.top);
      ctx.lineTo(x(hovered.time), height - pad.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(x(hovered.time), y(hovered.vis), 5, 0, 2 * Math.PI);
      ctx.fillStyle = '#fff';
      ctx.fill();
      ctx.strokeStyle = '#173643';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    ctx.restore();
    if (hovered) positionTooltip();
  }

  function positionTooltip() {
    tooltip.style.left = `${Math.max(4, Math.min(width - tooltip.offsetWidth - 4, x(hovered.time) + 12))}px`;
    tooltip.style.top = `${Math.max(4, y(hovered.vis) - tooltip.offsetHeight - 12)}px`;
  }

  function select(point, announce = false) {
    if (point === hovered) return;
    hovered = point;
    tooltip.hidden = !point;
    if (point) {
      const date = new Date(point.time);
      const title = document.createElement('div');
      title.textContent = dateFormat.format(date) + (point.kind === 'Snapshot' ? ` · ${String(date.getUTCHours()).padStart(2, '0')}:00 PT` : '');
      const value = document.createElement('strong');
      value.textContent = `${Number.isInteger(point.vis) ? point.vis : point.vis.toFixed(1)} ft`;
      const detail = document.createElement('div');
      detail.textContent = point.kind + (point.count ? ` · ${point.count} readings` : '');
      tooltip.replaceChildren(title, value, detail);
      positionTooltip();
      if (announce) announcement.textContent = `${title.textContent}: ${value.textContent}, ${detail.textContent}`;
    }
    scheduleDraw();
  }

  function hitTest(event) {
    const rect = canvas.getBoundingClientRect();
    const px = event.clientX - rect.left, py = event.clientY - rect.top;
    if (px < pad.left || px > width - pad.right || py < pad.top || py > height - pad.bottom) return null;
    let nearest = null, distance = (event.pointerType === 'touch' ? 28 : 16) ** 2;
    for (const point of points()) {
      if (point.time < start || point.time > end) continue;
      const squared = (x(point.time) - px) ** 2 + (y(point.vis) - py) ** 2;
      if (squared < distance) { nearest = point; distance = squared; }
    }
    return nearest;
  }

  canvas.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    canvas.focus({ preventScroll: true });
    drag = { x: event.clientX, y: event.clientY, start, end, moved: false };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (drag) {
      const delta = event.clientX - drag.x;
      if (Math.abs(delta) > 5 && Math.abs(delta) > Math.abs(event.clientY - drag.y)) drag.moved = true;
      if (drag.moved) {
        const offset = -delta / (width - pad.left - pad.right) * (drag.end - drag.start);
        setWindow(drag.start + offset, drag.end + offset);
        canvas.style.cursor = 'grabbing';
        return;
      }
    }
    select(hitTest(event));
  });
  canvas.addEventListener('pointerup', event => {
    if (drag && !drag.moved) select(hitTest(event), true);
    drag = null;
    canvas.style.cursor = 'crosshair';
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointercancel', () => { drag = null; canvas.style.cursor = 'crosshair'; select(null); });
  canvas.addEventListener('pointerleave', event => { if (!drag && event.pointerType !== 'touch') select(null); });
  canvas.addEventListener('wheel', event => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const fraction = Math.max(0, Math.min(1, (event.clientX - canvas.getBoundingClientRect().left - pad.left) / (width - pad.left - pad.right)));
    zoom(Math.exp(Math.max(-100, Math.min(100, event.deltaY)) * 0.005), fraction);
  }, { passive: false });
  canvas.addEventListener('keydown', event => {
    if (['+', '=', '-', '0', 'ArrowLeft', 'ArrowRight'].includes(event.key)) event.preventDefault();
    if (event.key === '+' || event.key === '=') zoom(0.5);
    if (event.key === '-') zoom(2);
    if (event.key === '0') setWindow(fullStart, fullEnd);
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const direction = event.key === 'ArrowRight' ? 1 : -1;
      if (event.shiftKey) {
        const delta = direction * (end - start) / 4;
        setWindow(start + delta, end + delta);
      } else {
        const visible = points().filter(point => point.time >= start && point.time < end).sort((a, b) => a.time - b.time);
        let index = visible.indexOf(hovered);
        if (index < 0) index = direction > 0 ? -1 : visible.length;
        select(visible[Math.max(0, Math.min(visible.length - 1, index + direction))] || null, true);
      }
    }
  });
  for (const toggle of [hourlyToggle, dailyToggle]) {
    toggle.addEventListener('change', () => { select(null); scheduleDraw(); });
  }
  const observer = new ResizeObserver(scheduleDraw);
  observer.observe(canvas);
  signal.addEventListener('abort', () => { observer.disconnect(); cancelAnimationFrame(frame); }, { once: true });
  setWindow(fullStart, fullEnd);
}
