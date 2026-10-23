const chat = document.getElementById('chat');
const form = document.getElementById('chatForm');
const question = document.getElementById('question');
const trace = document.getElementById('trace');
const sourceUsed = document.getElementById('sourceUsed');
const costDisplay = document.getElementById('costDisplay');
const tokensDisplay = document.getElementById('tokensDisplay');
const piiDisplay = document.getElementById('piiDisplay');

function escapeHtml(s = '') {
  return s.replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
}

function formatText(s = '') {
  return escapeHtml(s).replace(/\n/g, '<br>');
}

function addMessage(role, text, source = '', citations = [], cost = 0, tokens = 0, pii = false, runId = '', modelUsed = '') {
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;

  let metaHtml = '';
  if (role === 'assistant' && (source || cost > 0 || pii || runId || modelUsed)) {
    metaHtml = `
      <div class="meta-row">
        ${modelUsed ? `<span class="pill" style="font-size:10px; padding:2px 6px; border-color:#38485d; color:#9cb1c9;">⚡ ${escapeHtml(modelUsed.replace('gemini/', '').replace('groq/', ''))}</span>` : ''}
        ${source ? `<span class="answer-source">Source: ${escapeHtml(source)}</span>` : ''}
        ${cost > 0 ? `<span class="cost-tag">$${cost.toFixed(6)} (${tokens} tok)</span>` : ''}
        ${pii ? `<span class="pii-tag">🔒 PII Masked</span>` : ''}
        ${runId ? `
          <div class="feedback-wrap" data-run-id="${escapeHtml(runId)}">
            <button class="feedback-btn" onclick="sendFeedback('${escapeHtml(runId)}', 1, this)">👍 Helpful</button>
            <button class="feedback-btn" onclick="sendFeedback('${escapeHtml(runId)}', 0, this)">👎 Weak</button>
          </div>
        ` : ''}
      </div>
    `;
  }

  const citeHtml = citations && citations.length ? `
    <div class="citations">
      <strong>Verified References</strong><br>
      ${citations.map(c => c.url ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a>` : escapeHtml(c.title)).join('<br>')}
    </div>
  ` : '';

  wrap.innerHTML = `
    <div class="avatar">HR</div>
    <div class="bubble">
      ${formatText(text)}
      ${citeHtml}
      ${metaHtml}
    </div>
  `;
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
}

function renderTrace(items = []) {
  trace.innerHTML = items && items.length
    ? items.map(x => `<div class="trace-item">${escapeHtml(x)}</div>`).join('')
    : '<div class="empty">No trace available.</div>';
}

async function sendFeedback(runId, score, btn) {
  try {
    const parent = btn.parentElement;
    parent.querySelectorAll('button').forEach(b => b.disabled = true);
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId, score: score, comment: score ? 'Helpful' : 'Needs improvement' })
    });
    btn.style.color = score ? '#7ee6b1' : '#f08a8a';
    btn.textContent = score ? '✓ Recorded' : '✓ Noted';
  } catch (e) {
    console.error('Feedback error:', e);
  }
}

async function askAgent(q) {
  addMessage('user', q);
  question.value = '';
  renderTrace(['Evaluating Guardrails & Presidio PII...', 'Routing through LiteLLM Gateway...']);
  sourceUsed.textContent = 'Running';
  costDisplay.textContent = 'Calculating...';

  const btn = form.querySelector('button');
  btn.disabled = true;

  const selectedModel = document.getElementById('modelSelector')?.value || 'gemini/gemini-2.5-flash';

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-User-Role': 'employee',
        'X-User-Id': 'employee-001'
      },
      body: JSON.stringify({ question: q, model: selectedModel })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Request failed');

    addMessage(
      'assistant',
      data.answer,
      data.source_used,
      data.citations || [],
      data.cost_usd || 0,
      data.tokens_used || 0,
      data.pii_detected || false,
      data.langsmith_run_id || '',
      data.model_used || selectedModel
    );

    renderTrace(data.trace || []);
    sourceUsed.textContent = data.source_used;
    costDisplay.textContent = `$${(data.cost_usd || 0).toFixed(6)}`;
    tokensDisplay.textContent = data.tokens_used || 0;
    piiDisplay.textContent = data.pii_detected ? 'Masked' : 'None';

  } catch (e) {
    addMessage('assistant', `System Error: ${e.message}`);
    renderTrace(['Execution stopped due to error']);
    sourceUsed.textContent = 'Error';
    costDisplay.textContent = '$0.000000';
  } finally {
    btn.disabled = false;
  }
}

form.addEventListener('submit', e => {
  e.preventDefault();
  const q = question.value.trim();
  if (q) askAgent(q);
});

document.querySelectorAll('.example').forEach(b => {
  b.addEventListener('click', () => askAgent(b.textContent.trim()));
});

// Modal Logic: Upload
const uploadModal = document.getElementById('uploadModal');
document.getElementById('openUpload').onclick = () => uploadModal.classList.remove('hidden');
document.getElementById('closeUpload').onclick = () => uploadModal.classList.add('hidden');

document.getElementById('uploadBtn').onclick = async () => {
  const file = document.getElementById('fileInput').files[0];
  const key = document.getElementById('adminKey').value;
  const status = document.getElementById('uploadStatus');

  if (!file) {
    status.textContent = 'Please choose a file first.';
    return;
  }

  status.textContent = 'Chunking and indexing into Pinecone...';
  const fd = new FormData();
  fd.append('file', file);

  try {
    const r = await fetch('/api/ingest', {
      method: 'POST',
      headers: {
        'X-Admin-Key': key,
        'X-User-Role': 'hr_admin'
      },
      body: fd
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Upload failed');
    status.textContent = `Success: Indexed ${d.file} (${d.chunks} chunks).`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
};

// Modal Logic: Audit Trail
const auditModal = document.getElementById('auditModal');
document.getElementById('openAudit').onclick = async () => {
  auditModal.classList.remove('hidden');
  const tbody = document.getElementById('auditRows');
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#778598;">Loading audit records...</td></tr>';

  try {
    const res = await fetch('/api/audit?limit=25', {
      headers: {
        'X-User-Role': 'hr_admin',
        'X-Admin-Key': 'enterprise-hr-super-secret-admin-key'
      }
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Could not fetch audit records');

    if (!data.logs || !data.logs.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">No audit records found yet.</td></tr>';
      return;
    }

    tbody.innerHTML = data.logs.map(log => `
      <tr>
        <td>${new Date(log.created_at).toLocaleTimeString()}</td>
        <td>${escapeHtml(log.question.substring(0, 45))}${log.question.length > 45 ? '...' : ''}</td>
        <td><span class="answer-source">${escapeHtml(log.source_used)}</span></td>
        <td>$${(log.cost_usd || 0).toFixed(6)}</td>
        <td>${log.pii_detected ? '🔒 Yes' : 'No'}</td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:#f08a8a; text-align:center;">Error: ${escapeHtml(e.message)}</td></tr>`;
  }
};

document.getElementById('closeAudit').onclick = () => auditModal.classList.add('hidden');
