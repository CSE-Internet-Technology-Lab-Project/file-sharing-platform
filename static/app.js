/**
 * File Sharing Platform — Dashboard JS
 * Polls /api/status every 2s and renders nodes, files summary, and live events.
 */

const POLL_INTERVAL = 2000;
let lastEventCount = 0;

function getEventTypeClass(type) {
  if (type.includes('heartbeat')) return 'heartbeat';
  if (type.includes('stored'))    return 'stored';
  if (type.includes('replicated')) return 'replicated';
  if (type.includes('down'))      return 'down';
  if (type.includes('completed')) return 'completed';
  if (type.includes('failed'))   return 'failed';
  return '';
}

function truncate(str, max) {
  if (typeof str !== 'string') str = JSON.stringify(str);
  return str.length > max ? str.slice(0, max) + '…' : str;
}

async function refresh() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();

    // Connection indicator
    const dot = document.getElementById('conn-dot');
    const label = document.getElementById('conn-label');
    dot.className = 'status-dot connected';
    label.textContent = 'Connected';

    // ── Summary cards ──
    const nodesUp = data.nodes.filter(n => n.status === 'up').length;
    document.getElementById('nodes-up-count').textContent = nodesUp;
    document.getElementById('files-total-count').textContent = data.files.total;
    document.getElementById('files-available-count').textContent = data.files.available;
    document.getElementById('files-degraded-count').textContent = data.files.degraded;
    document.getElementById('nodes-badge').textContent = data.nodes.length;

    // ── Nodes table ──
    const tbody = document.getElementById('nodes-tbody');
    if (data.nodes.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty-msg">Waiting for nodes…</td></tr>';
    } else {
      tbody.innerHTML = data.nodes.map(n => `
        <tr>
          <td><span class="node-name">${n.node_id}</span></td>
          <td>
            <span class="status-chip ${n.status}">
              <span class="chip-dot"></span>${n.status}
            </span>
          </td>
          <td>${n.active}</td>
          <td>${n.disk_free_mb.toLocaleString()} MB</td>
          <td>
            ${n.status === 'up'
              ? `<button class="btn btn-kill" onclick="act('${n.node_id}','kill')">⏻ Kill</button>`
              : `<button class="btn btn-revive" onclick="act('${n.node_id}','revive')">↺ Revive</button>`
            }
          </td>
        </tr>
      `).join('');
    }

    // ── Events list ──
    const events = (data.recent_events || []).slice().reverse();
    const evList = document.getElementById('events-list');
    if (events.length === 0) {
      evList.innerHTML = '<li class="empty-msg">No events yet…</li>';
    } else {
      // Filter out heartbeats for readability (show last 3 max)
      const filtered = [];
      let heartbeatCount = 0;
      for (const e of events) {
        if (e.type === 'node.heartbeat') {
          heartbeatCount++;
          if (heartbeatCount <= 2) filtered.push(e);
        } else {
          filtered.push(e);
        }
      }
      evList.innerHTML = filtered.map(e => {
        const cls = getEventTypeClass(e.type);
        const payload = truncate(JSON.stringify(e.payload), 80);
        return `<li><span class="event-type ${cls}">${e.type}</span> <span class="event-payload">${payload}</span></li>`;
      }).join('');
    }

    lastEventCount = events.length;
  } catch (err) {
    const dot = document.getElementById('conn-dot');
    const label = document.getElementById('conn-label');
    dot.className = 'status-dot error';
    label.textContent = 'Disconnected';
  }
}

async function act(nodeId, action) {
  try {
    await fetch(`/admin/nodes/${nodeId}/${action}`, { method: 'POST' });
    // Immediate refresh
    await refresh();
  } catch (e) {
    console.error('admin action failed', e);
  }
}

// Start polling
setInterval(refresh, POLL_INTERVAL);
refresh();
