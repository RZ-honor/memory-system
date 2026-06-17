/* ═══ State ═════════════════════════════════════════════════════ */
const S = {
  page: 'dashboard',
  stats: null,
  sessions: [],
  memories: [],
  projects: [],
  currentProject: null,
  sessionDetail: null,
};

/* ═══ API ═══════════════════════════════════════════════════════ */
async function api(path, opts) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts?.body ? JSON.stringify(opts.body) : undefined,
  });
  return res.json();
}

/* ═══ Toast ═════════════════════════════════════════════════════ */
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 3000);
  // Pet reacts to toast type
  if (type === 'success') Pet.happy(msg.slice(0, 30));
  else if (type === 'error') Pet.thinking('Something went wrong...');
}

/* ═══ Modal ═════════════════════════════════════════════════════ */
function openModal(html) {
  document.getElementById('modal').innerHTML = html;
  document.getElementById('modalOverlay').classList.add('open');
}
function closeModal() {
  document.getElementById('modalOverlay').classList.remove('open');
}

/* ═══ Navigation ════════════════════════════════════════════════ */
function navigate(page, params) {
  S.page = page;
  S.sessionDetail = null;
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
  updateBreadcrumb(page, params);
  renderPage(page, params);
}

function updateBreadcrumb(page, params) {
  const bc = document.getElementById('breadcrumb');
  const names = {
    dashboard: '总览', sessions: '会话', memories: '记忆库',
    search: '搜索', knowledge: '知识库', fusion: '融合日志', skills: '技能库', modules: '模块', reasoning: '推理链', settings: '设置',
    'session-detail': '会话详情'
  };
  if (page === 'session-detail') {
    bc.innerHTML = `
      <span class="breadcrumb-link" onclick="navigate('sessions')">会话</span>
      <span class="breadcrumb-sep">/</span>
      <span class="breadcrumb-current">${params?.uuid?.slice(0,8) || '详情'}</span>
    `;
  } else {
    bc.innerHTML = `<span class="breadcrumb-current">${names[page] || page}</span>`;
  }
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

function refreshCurrent() {
  renderPage(S.page);
}

/* ═══ Page Router ═══════════════════════════════════════════════ */
async function renderPage(page, params) {
  const c = document.getElementById('content');
  switch (page) {
    case 'dashboard': return renderDashboard(c);
    case 'sessions': return renderSessions(c, params);
    case 'session-detail': return renderSessionDetail(c, params?.uuid);
    case 'cc-session-detail': return renderCCSessionDetail(c, params?.id, params?.project);
    case 'memories': return renderMemories(c);
    case 'search': return renderSearch(c);
    case 'knowledge': return renderKnowledge(c);
    case 'fusion': return renderFusion(c);
    case 'skills': return renderSkills(c);
    case 'modules': return renderModules(c);
    case 'reasoning': return renderReasoningChains(c);
    case 'settings': return renderSettings(c);
  }
}

/* ═══ Dashboard ═════════════════════════════════════════════════ */
async function renderDashboard(c) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const [stats, sessions, health, summary] = await Promise.all([
    api('/api/stats'),
    api('/api/sessions?limit=5'),
    api('/api/health'),
    api('/api/claude-sessions/summary'),
  ]);
  S.stats = stats;

  const ccSessions = summary.recent_sessions || [];
  const projectList = (summary.projects || []).map(p => [p.id, p.session_count]);
  const totalCCSessions = summary.total_sessions || 0;

  // Update sidebar badges
  const setBadge = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  setBadge('sessionCount', stats.total_sessions);
  setBadge('memoryCount', stats.total_memories);
  setBadge('reasoningCount', stats.total_reasoning_chains || 0);
  setBadge('workerStatus', `Worker: ${health.worker_running ? '运行中' : '已停止'}`);
  const statusDot = document.getElementById('statusDot');
  if (statusDot) statusDot.style.background = health.worker_running ? 'var(--success)' : 'var(--danger)';

  const catEntries = Object.entries(stats.by_category || {});
  const typeEntries = Object.entries(stats.by_memory_type || {});

  c.innerHTML = `
    <div class="page-shell">
      <!-- Hero -->
      <div class="page-hero">
        <div>
          <div class="page-title">总览</div>
          <div class="page-subtitle">记忆系统运行状态与数据概览</div>
        </div>
        <div style="display:flex;align-items:center;gap:12px">
          <span class="health-banner ${health.worker_running ? 'success' : 'danger'}">
            <span style="width:8px;height:8px;border-radius:50%;background:${health.worker_running ? 'var(--success)' : 'var(--danger)'};flex-shrink:0"></span>
            <span>${health.worker_running ? '系统正常' : 'Worker 停止'}</span>
          </span>
          <button class="btn btn-secondary btn-sm" onclick="refreshCurrent()">刷新</button>
        </div>
      </div>

      <!-- KPI Banner -->
      <div class="kpi-banner">
        <div class="kpi-pill"><div class="kpi-pill-label">总记忆</div><div class="kpi-pill-value" data-count="${stats.total_memories}">0</div></div>
        <div class="kpi-pill"><div class="kpi-pill-label">总会话</div><div class="kpi-pill-value" data-count="${totalCCSessions}">0</div></div>
        <div class="kpi-pill"><div class="kpi-pill-label">待处理</div><div class="kpi-pill-value" data-count="${stats.pending_queue}">0</div></div>
        <div class="kpi-pill"><div class="kpi-pill-label">融合操作</div><div class="kpi-pill-value" data-count="${stats.fusion_actions}">0</div></div>
      </div>

      <!-- Two-column: Projects + Recent Sessions -->
      <div class="card-grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr)">
        <!-- Projects -->
        <div class="card">
          <div class="card-header"><div class="card-title">项目</div></div>
          ${projectList.length ? `
            <div class="stack-list">
              ${projectList.map(([name, count]) => `
                <div class="stack-item" style="cursor:pointer" onclick="navigate('sessions',{project:'${escHtml(name)}'})">
                  <div style="flex:1;min-width:0">
                    <div style="font-size:13px;font-weight:500;color:var(--text-primary)">${escHtml(name)}</div>
                  </div>
                  <span class="tag tag-accent">${count} 会话</span>
                </div>
              `).join('')}
            </div>
          ` : '<div class="callout">暂无项目数据</div>'}
        </div>

        <!-- Recent Sessions -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">最近会话</div>
            <button class="btn btn-ghost btn-sm" onclick="navigate('sessions')">查看全部</button>
          </div>
          ${ccSessions.length ? `
            <div class="stack-list">
              ${ccSessions.slice(0, 5).map(s => `
                <div class="stack-item" style="cursor:pointer" onclick="openCCSessionDetail('${escHtml(s.session_id)}', '${escHtml(s.project)}')">
                  <div style="flex:1;min-width:0">
                    <div style="font-size:13px;font-weight:500;color:var(--text-primary)">${escHtml(s.project)}</div>
                    <div style="font-size:11px;color:var(--text-tertiary);margin-top:2px">${fmtTime(s.last_ts)}</div>
                  </div>
                  <span style="font-size:12px;color:var(--text-tertiary)">${s.user_msg_count || 0} 消息</span>
                </div>
              `).join('')}
            </div>
          ` : '<div class="callout">暂无会话记录</div>'}
        </div>
      </div>

      <!-- Distribution -->
      <div class="card-grid" style="grid-template-columns: 1fr 1fr">
        <div class="card">
          <div class="card-header"><div class="card-title">分类分布</div></div>
          ${catEntries.length ? `
            <div class="metric-list">
              ${catEntries.map(([k, v]) => `
                <div class="metric-row"><span class="tag tag-accent">${k}</span><span style="font-size:13px;font-weight:600;color:var(--text-primary)">${v}</span></div>
              `).join('')}
            </div>
          ` : '<div class="callout">暂无数据</div>'}
        </div>
        <div class="card">
          <div class="card-header"><div class="card-title">记忆类型</div></div>
          ${typeEntries.length ? `
            <div class="metric-list">
              ${typeEntries.map(([k, v]) => `
                <div class="metric-row"><span class="tag tag-default">${k}</span><span style="font-size:13px;font-weight:600;color:var(--text-primary)">${v}</span></div>
              `).join('')}
            </div>
          ` : '<div class="callout">暂无数据</div>'}
        </div>
      </div>
    </div>
  `;

  // Animate KPI numbers
  requestAnimationFrame(() => {
    document.querySelectorAll('.kpi-pill-value[data-count]').forEach(el => {
      const target = parseInt(el.dataset.count);
      if (target === 0) { el.textContent = '0'; return; }
      const duration = 600, start = performance.now();
      const tick = (now) => {
        const p = Math.min((now - start) / duration, 1);
        el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  });
}

/* ═══ Sessions ══════════════════════════════════════════════════ */
async function renderSessions(c, params) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const filterProject = params?.project;

  // Drill-down: load sessions for one project only
  if (filterProject) {
    const ccData = await api(`/api/claude-sessions?limit=500&project=${encodeURIComponent(filterProject)}`);
    const projSessions = ccData.sessions || [];
    c.innerHTML = `
      <div class="page-shell">
        <div class="page-hero">
          <div>
            <div class="page-title"><span style="cursor:pointer;color:var(--accent)" onclick="navigate('sessions')">会话</span> <span style="color:var(--text-tertiary);font-weight:400">/</span> ${escHtml(filterProject)}</div>
            <div class="page-subtitle">${projSessions.length} 个会话</div>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="navigate('sessions')">← 返回项目列表</button>
        </div>
        ${projSessions.length ? `
          <div class="card">
            <div class="table-wrap">
              <table>
                <thead><tr><th>会话 ID</th><th>用户消息</th><th>消息数</th><th>开始时间</th><th>最后活跃</th><th>首条消息</th></tr></thead>
                <tbody>
                  ${projSessions.map(s => `
                    <tr class="clickable" onclick="openCCSessionDetail('${escHtml(s.session_id)}', '${escHtml(s.project)}')">
                      <td><code style="font-size:11px">${escHtml(s.session_id.slice(0,12))}...</code></td>
                      <td>${s.user_msg_count || 0}</td>
                      <td>${(s.user_msg_count || 0) + (s.assistant_msg_count || 0)}</td>
                      <td>${fmtTime(s.first_ts)}</td>
                      <td>${fmtTime(s.last_ts)}</td>
                      <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-tertiary);font-size:12px">${escHtml(s.first_user_msg || '--')}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>
        ` : '<div class="callout">该项目暂无会话记录</div>'}
      </div>
    `;
    return;
  }

  // Default: show project list
  const [projData, summary] = await Promise.all([
    api('/api/claude-projects'),
    api('/api/claude-sessions/summary?recent=20'),
  ]);
  const projects = projData.projects || [];
  const totalSessions = projData.total || 0;

  const recentByProject = {};
  for (const s of (summary.recent_sessions || [])) {
    if (!recentByProject[s.project] || (s.last_ts || '') > (recentByProject[s.project] || '')) {
      recentByProject[s.project] = s.last_ts;
    }
  }

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">会话</div>
          <div class="page-subtitle">${projects.length} 个项目 · ${totalSessions} 个会话</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn btn-primary btn-sm" onclick="batchExtractCC()" id="batchExtractBtn">批量提取记忆</button>
        </div>
      </div>
      ${projects.length ? `
        <div class="card">
          <div class="table-wrap">
            <table>
              <thead><tr><th>项目</th><th>会话数</th><th>最近活跃</th></tr></thead>
              <tbody>
                ${projects.map(p => `
                  <tr class="clickable" onclick="navigate('sessions', {project:'${escHtml(p.id)}'})">
                    <td><span style="color:var(--text-primary);font-weight:500">${escHtml(p.id)}</span></td>
                    <td>${p.session_count}</td>
                    <td>${fmtTime(recentByProject[p.id]) || '--'}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      ` : '<div class="callout">暂无会话项目</div>'}
    </div>
  `;
}

/* ═══ Session Detail ═════════════════════════════════════════════ */
async function openSessionDetail(uuid) {
  navigate('session-detail', { uuid });
}

async function openCCSessionDetail(sessionId, project) {
  navigate('cc-session-detail', { id: sessionId, project });
}

async function renderSessionDetail(c, uuid) {
  if (!uuid) return;
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  S._currentSessionUuid = uuid;

  const data = await api(`/api/sessions/detail?uuid=${uuid}`);
  if (data.error) {
    c.innerHTML = `<div class="page-shell"><div class="callout">${escHtml(data.error)}</div></div>`;
    return;
  }

  const s = data.session;
  const memories = data.memories || [];
  const interactions = data.interactions || [];

  let summary = null;
  try { summary = s.summary ? JSON.parse(s.summary) : null; } catch { summary = null; }

  const toolCounts = {};
  interactions.forEach(i => {
    const t = i.tool_name || i.hook_event || 'other';
    toolCounts[t] = (toolCounts[t] || 0) + 1;
  });
  const toolSummary = Object.entries(toolCounts).sort((a,b) => b[1]-a[1]);

  const summarySections = [
    summary?.request && ['请求', summary.request],
    summary?.investigated && ['调查内容', summary.investigated],
    summary?.learned && ['发现', summary.learned],
    summary?.completed && ['完成事项', summary.completed],
    summary?.next_steps && ['后续步骤', summary.next_steps],
  ].filter(Boolean);

  c.innerHTML = `
    <div class="page-shell">
      <!-- Hero -->
      <div class="page-hero">
        <div>
          <div class="page-title"><span style="cursor:pointer;color:var(--accent)" onclick="navigate('sessions')">会话</span> <span style="color:var(--text-tertiary);font-weight:400">/</span> ${escHtml((s.project || '') + ' · ' + (s.session_uuid || uuid).slice(0, 8))}</div>
          <div class="page-subtitle">${s.status === 'active' ? '活跃' : '已完成'} · ${s.tool_count || 0} 次工具调用 · ${memories.length} 条记忆</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="extractSessionMemories('${escHtml(uuid)}')" id="extractBtn">${memories.length ? '重新提取' : '生成记忆'}</button>
      </div>

      <!-- Metadata grid -->
      <div class="meta-grid">
        <div class="meta-card"><div class="meta-label">项目</div><div class="meta-value">${escHtml(s.project)}</div></div>
        <div class="meta-card"><div class="meta-label">状态</div><div class="meta-value"><span class="tag ${s.status==='active'?'tag-success':'tag-default'}">${s.status==='active'?'活跃':'已完成'}</span></div></div>
        <div class="meta-card"><div class="meta-label">工具调用</div><div class="meta-value">${s.tool_count || 0}</div></div>
        <div class="meta-card"><div class="meta-label">开始时间</div><div class="meta-value">${fmtTime(s.started_at)}</div></div>
        <div class="meta-card"><div class="meta-label">完成时间</div><div class="meta-value">${s.completed_at ? fmtTime(s.completed_at) : '--'}</div></div>
        <div class="meta-card"><div class="meta-label">UUID</div><div class="meta-value" style="font-family:var(--mono);font-size:12px;word-break:break-all">${s.session_uuid || uuid}</div></div>
      </div>

      <!-- User prompt -->
      ${s.user_prompt ? `
        <div class="card">
          <div class="card-header"><div class="card-title">用户请求</div></div>
          <div style="font-size:13px;color:var(--text-secondary);line-height:1.7;padding:12px;background:var(--surface-1);border-radius:var(--radius-md);border:1px solid var(--border)">${escHtml(s.user_prompt)}</div>
        </div>
      ` : ''}

      <!-- Tool distribution -->
      ${toolSummary.length ? `
        <div class="card">
          <div class="card-header"><div class="card-title">工具使用分布</div></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${toolSummary.map(([t, n]) => `<span class="tag tag-default">${escHtml(t)} <b style="margin-left:4px">${n}</b></span>`).join('')}
          </div>
        </div>
      ` : ''}

      <!-- Summary -->
      ${summarySections.length ? `
        <div class="card">
          <div class="card-header"><div class="card-title">会话摘要</div></div>
          <div class="stack-list">
            ${summarySections.map(([label, text]) => `
              <div style="padding:8px 0">
                <div style="font-size:11px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">${label}</div>
                <div style="font-size:13px;color:var(--text-secondary);line-height:1.7">${escHtml(text)}</div>
              </div>
            `).join('')}
          </div>
        </div>
      ` : ''}

      <!-- Structured memories -->
      <div class="card" id="sessionMemoriesSection">
        <div class="card-header">
          <div class="card-title">结构化记忆</div>
          <span class="tag tag-default">${memories.length} 条</span>
        </div>
        <div id="sessionMemoriesList">
          ${memories.length ? memories.map(m => renderMemoryCard(m)).join('') : `
            <div class="callout">
              暂无结构化记忆。点击上方"生成记忆"按钮，AI 将分析工具调用记录并提取结构化观察。
            </div>
          `}
        </div>
      </div>

      <!-- Raw timeline (collapsible) -->
      ${interactions.length ? `
        <div class="card">
          <div class="card-header" style="cursor:pointer" onclick="toggleTimeline()">
            <div class="card-title">原始工具调用记录</div>
            <div style="display:flex;align-items:center;gap:8px">
              <span class="tag tag-default">${interactions.length} 次</span>
              <span id="timelineToggle" style="font-size:12px;color:var(--text-tertiary)">展开 ▾</span>
            </div>
          </div>
          <div id="timelineContent" style="display:none">
            <div class="timeline">
              ${interactions.map(i => renderTimelineItem(i)).join('')}
            </div>
          </div>
        </div>
      ` : ''}
    </div>
  `;
}

function renderMemoryCard(m) {
  const concepts = JSON.parse(m.concepts || '[]');
  const typeIcon = getTypeIcon(m.obs_type);
  const isHighImportance = (m.importance || 5) >= 7;
  return `
    <div class="memory-card${isHighImportance ? ' high-importance' : ''}" onclick="showMemoryDetail(${m.id})">
      <div class="mc-title">
        <span class="mc-type-icon">${typeIcon}</span>
        ${escHtml(m.title || '(无标题)')}
        ${getFlameHTML(m.importance)}
      </div>
      ${m.subtitle ? `<div class="mc-sub">${escHtml(m.subtitle)}</div>` : ''}
      ${m.narrative ? `<div class="mc-narrative">${escHtml(m.narrative)}</div>` : ''}
      <div class="mc-tags">
        <span class="tag tag-accent">${m.category}</span>
        ${m.memory_type ? `<span class="tag tag-default">${m.memory_type}</span>` : ''}
        ${m.obs_type ? `<span class="tag tag-default">${m.obs_type}</span>` : ''}
        ${concepts.slice(0, 3).map(t => `<span class="tag tag-default">${t}</span>`).join('')}
      </div>
      <div class="mc-meta">
        <span>${m.project}</span>
        <span>${fmtTime(m.created_at)}</span>
      </div>
      ${getMaturityBar(m)}
    </div>
  `;
}

function toggleTimeline() {
  const content = document.getElementById('timelineContent');
  const toggle = document.getElementById('timelineToggle');
  if (content.style.display === 'none') {
    content.style.display = 'block';
    toggle.innerHTML = '收起 &#9650;';
  } else {
    content.style.display = 'none';
    toggle.innerHTML = '展开 &#9660;';
  }
}

async function extractSessionMemories(uuid) {
  const btn = document.getElementById('extractBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px"></div> 提取中...';
  toast('正在使用 AI 提取结构化记忆...', 'info');

  try {
    const res = await api('/api/sessions/extract', { method: 'POST', body: { uuid } });
    if (res.error) {
      toast(`提取失败: ${res.error}`, 'error');
    } else {
      toast(`提取完成，生成了 ${res.memories_created} 条记忆`, 'success');
      // Refresh the session detail
      renderSessionDetail(document.getElementById('content'), uuid);
    }
  } catch (e) {
    toast(`提取出错: ${e.message}`, 'error');
  }
  btn.disabled = false;
  btn.innerHTML = '生成记忆';
}

function renderTimelineItem(item) {
  let inputPreview = '';
  try {
    const inp = item.tool_input ? JSON.parse(item.tool_input) : {};
    inputPreview = summarizeInput(item.tool_name, inp);
  } catch { inputPreview = (item.tool_input || '').slice(0, 120); }

  let responsePreview = '';
  try {
    responsePreview = (item.tool_response || '').slice(0, 300);
  } catch { responsePreview = ''; }

  return `
    <div class="timeline-item">
      <div class="tl-header">
        <span class="tl-tool">${escHtml(item.tool_name || item.hook_event)}</span>
        <span class="tag tag-default" style="font-size:10px">${item.status}</span>
        <span class="tl-time">${fmtTime(item.created_at)}</span>
      </div>
      <div class="tl-body">
        ${inputPreview ? `
          <div class="tl-label">输入</div>
          <div class="tl-content">${escHtml(inputPreview)}</div>
        ` : ''}
        ${responsePreview ? `
          <div class="tl-label" style="margin-top:8px">响应预览</div>
          <div class="tl-content">${escHtml(responsePreview)}</div>
        ` : ''}
      </div>
    </div>
  `;
}

function summarizeInput(tool, inp) {
  if (!inp || typeof inp !== 'object') return '';
  if (tool === 'Read' || tool === 'Edit' || tool === 'Write') return inp.file_path || '';
  if (tool === 'Bash') return (inp.command || '').slice(0, 150);
  if (tool === 'Grep') return inp.pattern || '';
  if (tool === 'Glob') return inp.pattern || '';
  if (tool === 'Agent') return inp.description || inp.prompt?.slice(0, 100) || '';
  return JSON.stringify(inp).slice(0, 150);
}

/* ═══ Memories ══════════════════════════════════════════════════ */
async function renderMemories(c) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const data = await api('/api/memories?limit=50');
  const memories = data.memories || [];
  const total = data.total || memories.length;
  const categories = [...new Set(memories.map(m => m.category))];
  S._memoriesOffset = memories.length;
  S._memoriesTotal = total;

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">记忆库</div>
          <div class="page-subtitle">${total} 条记忆</div>
        </div>
      </div>
      <div class="card">
        <div class="card-toolbar">
          <div class="filter-bar" style="margin-bottom:0">
            <span class="filter-chip active" onclick="filterMemories(null, this)">全部</span>
            ${categories.map(cat => `<span class="filter-chip" onclick="filterMemories('${escHtml(cat)}', this)">${escHtml(cat)}</span>`).join('')}
          </div>
        </div>
        <div id="memoryList">
          ${renderMemoryCards(memories)}
        </div>
        ${memories.length < total ? `
          <div style="text-align:center;padding:16px;border-top:1px solid var(--border)">
            <button class="btn btn-secondary btn-sm" onclick="loadMoreMemories()" id="loadMoreMemBtn">加载更多 (${total - memories.length} 条)</button>
          </div>` : ''}
      </div>
    </div>
  `;
  S._allMemories = memories;
}

async function loadMoreMemories() {
  const btn = document.getElementById('loadMoreMemBtn');
  if (btn) btn.disabled = true;
  const data = await api(`/api/memories?limit=50&offset=${S._memoriesOffset}`);
  const more = data.memories || [];
  S._allMemories = (S._allMemories || []).concat(more);
  S._memoriesOffset += more.length;
  const list = document.getElementById('memoryList');
  if (list) list.insertAdjacentHTML('beforeend', renderMemoryCards(more));
  const remaining = S._memoriesTotal - S._memoriesOffset;
  if (remaining > 0 && btn) {
    btn.disabled = false;
    btn.textContent = `加载更多 (${remaining} 条)`;
  } else if (btn) {
    btn.parentElement.remove();
  }
}

function renderMemoryCards(memories) {
  if (!memories.length) return '<div class="empty"><div class="empty-icon">&#9670;</div><div class="empty-text">暂无记忆数据</div></div>';
  return memories.map(m => renderMemoryCard(m)).join('');
}

function filterMemories(category, chipEl) {
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  chipEl.classList.add('active');
  let filtered = S._allMemories || [];
  if (category) filtered = filtered.filter(m => m.category === category);
  document.getElementById('memoryList').innerHTML = renderMemoryCards(filtered);
}

async function showMemoryDetail(id) {
  const all = S._allMemories || [];
  const m = all.find(x => x.id === id);
  if (!m) { toast('未找到记忆', 'error'); return; }

  const facts = JSON.parse(m.facts || '[]');
  const concepts = JSON.parse(m.concepts || '[]');

  openModal(`
    <div class="modal-title">${escHtml(m.title || '(无标题)')}</div>
    <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
      <span class="tag tag-accent">${m.category}</span>
      ${m.obs_type ? `<span class="tag tag-default">${m.obs_type}</span>` : ''}
      <span class="tag tag-default">${m.project}</span>
    </div>
    ${m.subtitle ? `<p style="color:var(--text-secondary);margin-bottom:12px">${escHtml(m.subtitle)}</p>` : ''}
    ${m.narrative ? `<p style="color:var(--text-secondary);margin-bottom:12px;line-height:1.7">${escHtml(m.narrative)}</p>` : ''}
    ${m.content ? `<div style="margin-bottom:12px"><div class="form-label">内容</div><div style="font-size:13px;color:var(--text-secondary);padding:12px;background:var(--surface-1);border-radius:var(--radius-md);border:1px solid var(--border);white-space:pre-wrap">${escHtml(m.content)}</div></div>` : ''}
    ${facts.length ? `<div style="margin-bottom:12px"><div class="form-label">事实</div><ul style="padding-left:20px;color:var(--text-secondary);font-size:13px">${facts.map(f => `<li style="margin-bottom:4px">${escHtml(f)}</li>`).join('')}</ul></div>` : ''}
    ${concepts.length ? `<div style="margin-bottom:12px"><div class="form-label">概念</div><div style="display:flex;gap:6px;flex-wrap:wrap">${concepts.map(t => `<span class="tag tag-default">${t}</span>`).join('')}</div></div>` : ''}
    <div style="font-size:11px;color:var(--text-tertiary);margin-top:16px">
      创建: ${fmtTime(m.created_at)} | 更新: ${fmtTime(m.updated_at)} | ID: ${m.id}
      ${m.origin_session ? ` | 来源会话: ${m.origin_session.slice(0,8)}...` : ''}
    </div>
    <div class="modal-actions">
      <button class="btn btn-danger btn-sm" onclick="deleteMemory(${m.id})">删除</button>
      <button class="btn btn-secondary btn-sm" onclick="closeModal()">关闭</button>
    </div>
  `);
}

async function deleteMemory(id) {
  if (!confirm('确定要删除这条记忆吗？')) return;
  await api('/api/memories/delete', { method: 'POST', body: { id } });
  closeModal();
  toast('已删除', 'success');
  renderPage('memories');
}

/* ═══ Search ════════════════════════════════════════════════════ */
function renderSearch(c) {
  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">搜索</div>
          <div class="page-subtitle">全文与语义混合检索</div>
        </div>
      </div>
      <div class="card">
        <div style="display:flex;gap:12px;align-items:center">
          <div style="flex:1">
            <input class="input" id="searchInput" placeholder="输入关键词或自然语言描述..."
                   onkeydown="if(event.key==='Enter')doSearch()" style="font-size:15px;padding:12px 16px">
          </div>
          <select class="select" id="searchCategory" style="min-width:120px">
            <option value="">全部分类</option>
            <option value="observation">observation</option>
            <option value="user">user</option>
            <option value="reference">reference</option>
            <option value="feedback">feedback</option>
            <option value="project">project</option>
          </select>
          <button class="btn btn-primary" onclick="doSearch()">搜索</button>
        </div>
      </div>
      <div id="searchResults"></div>
    </div>
  `;
  document.getElementById('searchInput').focus();
}

async function doSearch() {
  const query = document.getElementById('searchInput').value.trim();
  if (!query) return;
  const category = document.getElementById('searchCategory').value;
  const resultsDiv = document.getElementById('searchResults');
  resultsDiv.innerHTML = '<div style="display:flex;justify-content:center;padding:32px"><div class="spinner"></div></div>';

  const data = await api('/api/memories/search', {
    method: 'POST',
    body: { query, limit: 20, category: category || undefined }
  });

  const results = data.results || [];
  if (!results.length) {
    resultsDiv.innerHTML = '<div class="callout">未找到匹配结果</div>';
    return;
  }

  resultsDiv.innerHTML = `
    <div class="card">
      <div class="card-header"><div class="card-title">搜索结果</div><span class="tag tag-default">${results.length} 条</span></div>
      <div class="stack-list">
        ${results.map(m => `
          <div class="stack-item" style="cursor:pointer" onclick="showMemoryDetail(${m.id})">
            <div style="flex:1;min-width:0">
              <div style="font-size:13px;font-weight:500;color:var(--text-primary)">${escHtml(m.title || '')}</div>
              ${m.narrative ? `<div style="font-size:12px;color:var(--text-tertiary);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(m.narrative.slice(0, 120))}</div>` : ''}
            </div>
            <div style="display:flex;gap:6px;flex-shrink:0">
              <span class="tag tag-accent">${m.category}</span>
              <span class="tag tag-default">${m.project}</span>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

/* ═══ Knowledge ═════════════════════════════════════════════════ */
async function renderKnowledge(c) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const data = await api('/api/knowledge/all');
  const allKnowledge = (data.knowledge || []).map(k => { k._project = k.project; return k; });
  S._allKnowledge = allKnowledge;

  if (!allKnowledge.length) {
    c.innerHTML = '<div class="page-shell"><div class="page-hero"><div><div class="page-title">知识索引</div><div class="page-subtitle">融合过程中自动提取的知识条目</div></div></div><div class="callout">暂无知识条目。知识会在融合过程中自动从记忆中提取。</div></div>';
    return;
  }

  const byProject = {};
  allKnowledge.forEach(k => {
    if (!byProject[k._project]) byProject[k._project] = [];
    byProject[k._project].push(k);
  });

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">知识索引</div>
          <div class="page-subtitle">${allKnowledge.length} 条知识 · ${Object.keys(byProject).length} 个项目</div>
        </div>
      </div>
      ${Object.entries(byProject).map(([project, items]) => `
        <div class="card">
          <div class="card-header">
            <div style="display:flex;align-items:center;gap:8px">
              <span class="tag tag-accent">${escHtml(project)}</span>
              <span style="font-size:12px;color:var(--text-tertiary)">${items.length} 条知识</span>
            </div>
          </div>
          <div class="stack-list">
            ${items.map((k) => `
              <div class="stack-item" style="cursor:pointer" onclick="showKnowledgeDetail(${allKnowledge.indexOf(k)})">
                <div style="flex:1;min-width:0">
                  <div style="font-size:13px;font-weight:500;color:var(--text-primary)">${escHtml(k.key)}</div>
                  <div style="font-size:12px;color:var(--text-tertiary);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml((k.value || '').slice(0, 120))}</div>
                </div>
                <div style="font-size:11px;color:var(--text-tertiary);white-space:nowrap;flex-shrink:0">${fmtTime(k.updated_at)}</div>
              </div>
            `).join('')}
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

function showKnowledgeDetail(idx) {
  const k = (S._allKnowledge || [])[idx];
  if (!k) return;
  openModal(`
    <div class="modal-title">${escHtml(k.key)}</div>
    <div style="display:flex;gap:8px;margin-bottom:16px">
      <span class="tag tag-accent">${k._project}</span>
      ${k.memory_id ? `<span class="tag tag-default">记忆 #${k.memory_id}</span>` : ''}
    </div>
    <div style="margin-bottom:16px">
      <div class="form-label">内容</div>
      <div style="font-size:13px;color:var(--text-secondary);padding:16px;background:var(--surface-1);border-radius:var(--radius-md);border:1px solid var(--border);line-height:1.7;white-space:pre-wrap">${escHtml(k.value || '(无内容)')}</div>
    </div>
    <div style="font-size:11px;color:var(--text-tertiary)">
      更新时间: ${fmtTime(k.updated_at)} | ID: ${k.id}
      ${k.memory_id ? ` | 关联记忆: <span style="cursor:pointer;color:var(--accent)" onclick="closeModal();showMemoryDetailById(${k.memory_id})">#${k.memory_id}</span>` : ''}
    </div>
    <div class="modal-actions">
      <button class="btn btn-secondary btn-sm" onclick="closeModal()">关闭</button>
    </div>
  `);
}

async function showMemoryDetailById(id) {
  const all = S._allMemories || [];
  let m = all.find(x => x.id === id);
  if (!m) {
    // Try fetching from API
    const data = await api(`/api/memories?limit=100`);
    m = (data.memories || []).find(x => x.id === id);
  }
  if (m) {
    showMemoryDetail(id);
  } else {
    toast('未找到关联记忆', 'info');
  }
}

/* ═══ Fusion Log ════════════════════════════════════════════════ */
async function renderFusion(c) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const data = await api('/api/fusion/log?limit=50');
  const logs = data.log || [];

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">融合日志</div>
          <div class="page-subtitle">知识融合、矛盾检测与记忆合并记录</div>
        </div>
        <button class="btn btn-secondary btn-sm" onclick="runFusion()">手动运行融合</button>
      </div>
      ${logs.length ? `
        <div class="card">
          <div class="table-wrap">
            <table>
              <thead><tr><th>操作</th><th>源ID</th><th>目标ID</th><th>原因</th><th>时间</th></tr></thead>
              <tbody>
                ${logs.map(l => `
                  <tr>
                    <td><span class="tag tag-accent">${escHtml(l.action)}</span></td>
                    <td>${l.source_id || '--'}</td>
                    <td>${l.target_id || '--'}</td>
                    <td style="max-width:400px;word-break:break-all;font-size:12px;color:var(--text-secondary)">${escHtml(l.reason || '')}</td>
                    <td style="font-size:12px;color:var(--text-tertiary)">${fmtTime(l.created_at)}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      ` : '<div class="callout">暂无融合日志。融合操作会在会话结束时自动触发。</div>'}
    </div>
  `;
}

async function runFusion() {
  toast('正在运行融合...', 'info');
  const res = await api('/api/fusion/run', { method: 'POST', body: {} });
  toast(`融合完成: ${JSON.stringify(res.stats || {})}`, 'success');
  renderPage('fusion');
}

/* ═══ Skills ═══════════════════════════════════════════════════ */
async function renderSkills(c) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const data = await api('/api/skills?limit=100');
  const skills = data.skills || [];
  S._allSkills = skills;

  const badge = document.getElementById('skillCount');
  if (badge) badge.textContent = skills.length;

  const projects = [...new Set(skills.map(s => s.project))];

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">技能库</div>
          <div class="page-subtitle">${skills.length} 个可复用技能，从重复模式中自动提取</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <input class="input" id="skillSearchInput" placeholder="搜索技能..."
                 onkeydown="if(event.key==='Enter')searchSkills()" style="width:200px;font-size:13px;padding:6px 10px">
        </div>
      </div>
      ${projects.length > 1 ? `
        <div class="card">
          <div class="card-toolbar">
            <div class="filter-bar" style="margin-bottom:0">
              <span class="filter-chip active" onclick="filterSkills(null, this)">全部</span>
              ${projects.map(p => `<span class="filter-chip" onclick="filterSkills('${escHtml(p)}', this)">${escHtml(p)}</span>`).join('')}
            </div>
          </div>
        </div>
      ` : ''}
      <div id="skillList">
        ${renderSkillCards(skills)}
      </div>
    </div>
  `;
}

function renderSkillCards(skills) {
  if (!skills.length) return `
    <div class="empty">
      <div class="empty-icon">&#9889;</div>
      <div class="empty-text">暂无技能</div>
      <div style="font-size:12px;color:var(--text-tertiary);margin-top:8px">
        技能会在会话结束时自动从重复模式中提取
      </div>
    </div>
  `;
  return skills.map(s => renderSkillCard(s)).join('');
}

function renderSkillCard(s) {
  const workflow = JSON.parse(s.workflow || '[]');
  const keywords = JSON.parse(s.trigger_keywords || '[]');
  const confidence = s.confidence || 3;
  const confStars = '★'.repeat(confidence) + '☆'.repeat(5 - confidence);

  return `
    <div class="memory-card" onclick="showSkillDetail(${s.id})">
      <div class="mc-title">
        <span class="mc-type-icon">⚡</span>
        ${escHtml(s.name)}
        <span class="mc-importance" style="margin-left:auto;font-size:11px;color:var(--warning)">${confStars}</span>
      </div>
      ${s.description ? `<div class="mc-narrative">${escHtml(s.description)}</div>` : ''}
      <div class="mc-tags">
        <span class="tag tag-accent">${s.project}</span>
        ${keywords.slice(0, 4).map(k => `<span class="tag tag-default">${k}</span>`).join('')}
        ${workflow.length ? `<span class="tag tag-default">${workflow.length} 步流程</span>` : ''}
      </div>
      <div class="mc-meta">
        <span>使用 ${s.use_count || 0} 次</span>
        <span>${fmtTime(s.created_at)}</span>
        ${s.last_used_at ? `<span>最后使用: ${fmtTime(s.last_used_at)}</span>` : ''}
      </div>
    </div>
  `;
}

function filterSkills(project, chipEl) {
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  chipEl.classList.add('active');
  let filtered = S._allSkills || [];
  if (project) filtered = filtered.filter(s => s.project === project);
  document.getElementById('skillList').innerHTML = renderSkillCards(filtered);
}

async function searchSkills() {
  const query = document.getElementById('skillSearchInput').value.trim();
  if (!query) {
    document.getElementById('skillList').innerHTML = renderSkillCards(S._allSkills || []);
    return;
  }
  const data = await api(`/api/skills/search?q=${encodeURIComponent(query)}`);
  const skills = data.skills || [];
  document.getElementById('skillList').innerHTML = renderSkillCards(skills);
}

async function showSkillDetail(id) {
  const all = S._allSkills || [];
  const s = all.find(x => x.id === id);
  if (!s) { toast('未找到技能', 'error'); return; }

  const workflow = JSON.parse(s.workflow || '[]');
  const keywords = JSON.parse(s.trigger_keywords || '[]');
  const stopConditions = JSON.parse(s.stop_conditions || '[]');
  const examples = JSON.parse(s.examples || '[]');
  const gotchas = JSON.parse(s.gotchas || '[]');
  const references = JSON.parse(s.references_list || '[]');
  const confidence = s.confidence || 3;
  const confStars = '★'.repeat(confidence) + '☆'.repeat(5 - confidence);

  openModal(`
    <div class="modal-title">⚡ ${escHtml(s.name)}</div>
    <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
      <span class="tag tag-accent">${s.project}</span>
      <span class="tag tag-default" style="color:var(--warning)">${confStars} 置信度</span>
      <span class="tag tag-default">使用 ${s.use_count || 0} 次</span>
      ${s.is_active ? '<span class="tag tag-success">活跃</span>' : '<span class="tag tag-danger">已停用</span>'}
    </div>

    ${s.description ? `
      <div style="margin-bottom:16px">
        <div class="form-label">触发条件</div>
        <div style="font-size:13px;color:var(--text-secondary);padding:12px;background:var(--surface-1);border-radius:var(--radius-md);border:1px solid var(--border);line-height:1.7">${escHtml(s.description)}</div>
      </div>
    ` : ''}

    ${workflow.length ? `
      <div style="margin-bottom:16px">
        <div class="form-label">工作流程</div>
        <div style="padding:12px;background:var(--surface-1);border-radius:var(--radius-md);border:1px solid var(--border)">
          ${workflow.map((step, i) => `
            <div style="display:flex;gap:8px;padding:6px 0;${i < workflow.length - 1 ? 'border-bottom:1px solid var(--border)' : ''}">
              <span style="color:var(--accent);font-weight:600;min-width:20px">${i + 1}.</span>
              <span style="font-size:13px;color:var(--text-secondary)">${escHtml(step)}</span>
            </div>
          `).join('')}
        </div>
      </div>
    ` : ''}

    ${keywords.length ? `
      <div style="margin-bottom:16px">
        <div class="form-label">触发关键词</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">${keywords.map(k => `<span class="tag tag-default">${k}</span>`).join('')}</div>
      </div>
    ` : ''}

    ${s.output_format ? `
      <div style="margin-bottom:16px">
        <div class="form-label">输出格式</div>
        <div style="font-size:13px;color:var(--text-secondary);padding:12px;background:var(--surface-1);border-radius:var(--radius-md);border:1px solid var(--border);line-height:1.7">${escHtml(s.output_format)}</div>
      </div>
    ` : ''}

    ${stopConditions.length ? `
      <div style="margin-bottom:16px">
        <div class="form-label">停止条件</div>
        <ul style="padding-left:20px;color:var(--text-secondary);font-size:13px">${stopConditions.map(sc => `<li style="margin-bottom:4px">${escHtml(sc)}</li>`).join('')}</ul>
      </div>
    ` : ''}

    ${gotchas.length ? `
      <div style="margin-bottom:16px">
        <div class="form-label">注意事项</div>
        <ul style="padding-left:20px;color:var(--text-secondary);font-size:13px">${gotchas.map(g => `<li style="margin-bottom:4px">${escHtml(g)}</li>`).join('')}</ul>
      </div>
    ` : ''}

    ${examples.length ? `
      <div style="margin-bottom:16px">
        <div class="form-label">示例</div>
        ${examples.map(ex => `
          <div style="font-size:12px;color:var(--text-secondary);padding:10px;background:var(--surface-1);border-radius:var(--radius-md);border:1px solid var(--border);margin-bottom:6px;font-family:var(--mono);white-space:pre-wrap">${escHtml(ex)}</div>
        `).join('')}
      </div>
    ` : ''}

    ${references.length ? `
      <div style="margin-bottom:16px">
        <div class="form-label">参考资料</div>
        <ul style="padding-left:20px;color:var(--text-secondary);font-size:13px">${references.map(r => `<li style="margin-bottom:4px">${escHtml(r)}</li>`).join('')}</ul>
      </div>
    ` : ''}

    <div style="font-size:11px;color:var(--text-tertiary);margin-top:16px">
      创建: ${fmtTime(s.created_at)} | 更新: ${fmtTime(s.updated_at)} | ID: ${s.id}
      ${s.origin_session ? ` | 来源会话: ${s.origin_session.slice(0,8)}...` : ''}
    </div>
    <div class="modal-actions">
      <button class="btn btn-danger btn-sm" onclick="deactivateSkill(${s.id})">停用</button>
      <button class="btn btn-secondary btn-sm" onclick="closeModal()">关闭</button>
    </div>
  `);
}

async function deactivateSkill(id) {
  if (!confirm('确定要停用这个技能吗？')) return;
  await api('/api/skills/deactivate', { method: 'POST', body: { id } });
  closeModal();
  toast('技能已停用', 'success');
  renderPage('skills');
}

/* ═══ Modules ═══════════════════════════════════════════════════ */
async function renderModules(c) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const data = await api('/api/modules/all');
  const allModules = data.modules || [];
  S._allModules = allModules;

  const badge = document.getElementById('moduleCount');
  if (badge) badge.textContent = allModules.length;

  const uniqueProjects = [...new Set(allModules.map(m => m.project))];

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">记忆模块</div>
          <div class="page-subtitle">${allModules.length} 个主题聚类，自动归类记忆</div>
        </div>
      </div>
      ${uniqueProjects.length > 1 ? `
        <div class="card">
          <div class="card-toolbar">
            <div class="filter-bar" style="margin-bottom:0">
              <span class="filter-chip active" onclick="filterModules(null, this)">全部</span>
              ${uniqueProjects.map(p => `<span class="filter-chip" onclick="filterModules('${escHtml(p)}', this)">${escHtml(p)}</span>`).join('')}
            </div>
          </div>
        </div>
      ` : ''}
      <div id="moduleList">
        ${renderModuleCards(allModules)}
      </div>
    </div>
  `;
}

function renderModuleCards(modules) {
  if (!modules.length) return `
    <div class="empty">
      <div class="empty-icon">&#9638;</div>
      <div class="empty-text">暂无模块</div>
      <div style="font-size:12px;color:var(--text-tertiary);margin-top:8px">
        模块会在保存记忆时自动创建和归类
      </div>
    </div>
  `;
  return `<div class="memory-grid">${modules.map(m => renderModuleCard(m)).join('')}</div>`;
}

function renderModuleCard(m) {
  const icon = getModuleIcon(m.name);
  return `
    <div class="memory-card" onclick="showModuleDetail(${m.id})">
      <div class="mc-title">
        <span class="mc-type-icon">${icon}</span>
        ${escHtml(m.name)}
        <span class="mc-importance" style="margin-left:auto;font-size:12px;color:var(--accent)">${m.memory_count || 0} 条</span>
      </div>
      ${m.description ? `<div class="mc-narrative">${escHtml(m.description)}</div>` : ''}
      <div class="mc-tags">
        <span class="tag tag-accent">${m.project}</span>
      </div>
      <div class="mc-meta">
        <span>创建: ${fmtTime(m.created_at)}</span>
        <span>更新: ${fmtTime(m.updated_at)}</span>
      </div>
    </div>
  `;
}

function getModuleIcon(name) {
  const icons = {
    'auth': '&#128274;', 'db': '&#128451;', 'api': '&#128268;', 'ui': '&#127912;',
    'test': '&#9854;', 'deploy': '&#128640;', 'security': '&#128737;', 'perf': '&#9889;',
    'config': '&#9881;', 'docs': '&#128214;', 'bug': '&#128027;', 'feature': '&#128640;',
    'refactor': '&#128295;', 'data': '&#128202;', 'auth-system': '&#128274;',
    'db-migration': '&#128451;', 'api-endpoints': '&#128268;',
  };
  const lower = name.toLowerCase();
  for (const [key, icon] of Object.entries(icons)) {
    if (lower.includes(key)) return icon;
  }
  return '&#9638;';
}

function getCategoryColor(cat) {
  const colors = { observation: 'accent', user: 'success', reference: 'warning', feedback: 'danger', project: 'accent' };
  return colors[cat] || 'default';
}

function filterModules(project, chipEl) {
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  chipEl.classList.add('active');
  let filtered = S._allModules || [];
  if (project) filtered = filtered.filter(m => m.project === project);
  document.getElementById('moduleList').innerHTML = renderModuleCards(filtered);
}

async function showModuleDetail(id) {
  const data = await api(`/api/modules/detail?id=${id}`);
  const mod = data.module;
  const memories = data.memories || [];
  if (!mod) { toast('模块未找到', 'error'); return; }

  let memoriesHtml = memories.length ? memories.map(m => {
    const importance = m.importance || 5;
    const star = importance >= 8 ? ' ★' : '';
    return `
      <div class="memory-card" style="margin-bottom:8px">
        <div class="mc-title">
          <span class="mc-type-icon">${getTypeIcon(m.obs_type)}</span>
          ${escHtml(m.title || '')}${star}
        </div>
        ${m.narrative ? `<div class="mc-narrative">${escHtml(m.narrative)}</div>` : ''}
        <div class="mc-meta">
          <span class="tag tag-${getCategoryColor(m.category)}">${m.category}</span>
          <span class="tag tag-default">${m.memory_type || 'episodic'}</span>
          <span>重要性: ${importance}/10</span>
          <span>${fmtTime(m.created_at)}</span>
        </div>
      </div>
    `;
  }).join('') : '<div style="color:var(--text-tertiary);padding:16px">该模块暂无记忆</div>';

  openModal(`
    <div class="modal-title">${getModuleIcon(mod.name)} ${escHtml(mod.name)}</div>
    <div style="color:var(--text-secondary);margin-bottom:16px">${escHtml(mod.description || '无描述')}</div>
    <div style="display:flex;gap:12px;margin-bottom:16px">
      <span class="tag tag-accent">${mod.project}</span>
      <span class="tag tag-default">${mod.memory_count || memories.length} 条记忆</span>
      <span class="tag tag-default">创建: ${fmtTime(mod.created_at)}</span>
    </div>
    <div style="max-height:400px;overflow-y:auto">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text-secondary)">模块内记忆</div>
      ${memoriesHtml}
    </div>
  `);
}

/* ═══ Settings ══════════════════════════════════════════════════ */
async function renderSettings(c) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const cfg = await api('/api/config');

  const llm = cfg.llm || {};
  const fusion = cfg.fusion || {};
  const pruning = cfg.pruning || {};
  const ctx = cfg.context || {};
  const server = cfg.server || {};

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">设置</div>
          <div class="page-subtitle">系统配置与维护</div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-primary btn-sm" onclick="saveSettings()">保存配置</button>
          <button class="btn btn-ghost btn-sm" onclick="renderPage('settings')">重置</button>
        </div>
      </div>

      <!-- LLM Config -->
      <div class="card">
        <div class="card-header"><div class="card-title">模型配置</div></div>
        <div class="grid-2">
          <div class="form-group"><label class="form-label">提供商</label><input class="input" id="cfg_provider" value="${escHtml(llm.provider || '')}"></div>
          <div class="form-group"><label class="form-label">模型</label><input class="input" id="cfg_model" value="${escHtml(llm.model || '')}"></div>
          <div class="form-group"><label class="form-label">Base URL</label><input class="input" id="cfg_base_url" value="${escHtml(llm.base_url || '')}"></div>
          <div class="form-group">
            <label class="form-label">API Key</label>
            <div style="display:flex;gap:4px;align-items:center">
              <input class="input" type="password" id="cfg_api_key" value="${escHtml(llm.api_key || '')}" style="flex:1">
              <button class="btn btn-ghost btn-sm" onclick="toggleApiKeyVisibility()" id="apiKeyToggle" style="min-width:32px">👁</button>
            </div>
          </div>
          <div class="form-group"><label class="form-label">Max Tokens</label><input class="input" type="number" id="cfg_max_tokens" value="${llm.max_tokens || 4096}"></div>
          <div class="form-group"><label class="form-label">超时 (秒)</label><input class="input" type="number" id="cfg_timeout" value="${llm.timeout || 60}"></div>
        </div>
        <div style="display:flex;gap:8px;margin-top:var(--space-4)">
          <button class="btn btn-secondary" onclick="testLLM()">测试连接</button>
          <span id="llmTestResult" style="font-size:13px;display:flex;align-items:center"></span>
        </div>
      </div>

      <!-- Fusion & Pruning -->
      <div class="card-grid" style="grid-template-columns: 1fr 1fr">
        <div class="card">
          <div class="card-header"><div class="card-title">融合配置</div></div>
          <div class="form-group"><label class="form-label">融合间隔 (秒)</label><input class="input" type="number" id="cfg_fusion_interval" value="${fusion.interval_seconds || 300}"></div>
          <div class="form-group"><label class="form-label">相似度阈值</label><input class="input" type="number" step="0.01" id="cfg_similarity" value="${fusion.similarity_threshold || 0.75}"></div>
        </div>
        <div class="card">
          <div class="card-header"><div class="card-title">剪枝配置</div></div>
          <div class="form-group"><label class="form-label">过期天数</label><input class="input" type="number" id="cfg_expire_days" value="${pruning.expire_days || 30}"></div>
          <div class="form-group"><label class="form-label">剪枝间隔 (小时)</label><input class="input" type="number" id="cfg_pruning_hours" value="${pruning.interval_hours || 6}"></div>
        </div>
      </div>

      <!-- Context & Server -->
      <div class="card-grid" style="grid-template-columns: 1fr 1fr">
        <div class="card">
          <div class="card-header"><div class="card-title">注入策略</div></div>
          <div class="form-group"><label class="form-label">策略</label><input class="input" id="cfg_ctx_strategy" value="${escHtml(ctx.strategy || 'minimal_index')}"></div>
          <div class="form-group"><label class="form-label">最大字符数</label><input class="input" type="number" id="cfg_ctx_max_chars" value="${ctx.max_chars || 800}"></div>
        </div>
        <div class="card">
          <div class="card-header"><div class="card-title">服务配置</div></div>
          <div class="form-group"><label class="form-label">主机</label><input class="input" id="cfg_host" value="${escHtml(server.host || '127.0.0.1')}"></div>
          <div class="form-group"><label class="form-label">端口</label><input class="input" type="number" id="cfg_port" value="${server.port || 38800}"></div>
        </div>
      </div>

      <!-- Maintenance -->
      <div class="card">
        <div class="card-header"><div class="card-title">记忆管理</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-secondary" onclick="runCleanup()">清理低质量记忆</button>
          <button class="btn btn-secondary" onclick="runFusionFromSettings()">运行融合</button>
          <button class="btn btn-secondary" onclick="cleanupReasoningChains()">清理低质量推理链</button>
          <button class="btn btn-secondary" onclick="cleanupGenericMemories()">清理无效记忆</button>
        </div>
        <div style="font-size:12px;color:var(--text-tertiary);margin-top:8px">清理操作会删除通用叙事的记忆、无效推理链和已知噪音模式</div>
      </div>
    </div>
  `;
}

async function testLLM() {
  const el = document.getElementById('llmTestResult');
  el.innerHTML = '<div class="spinner"></div>';
  const res = await api('/api/llm/test', {
    method: 'POST',
    body: {
      base_url: document.getElementById('cfg_base_url').value,
      api_key: document.getElementById('cfg_api_key').value,
      model: document.getElementById('cfg_model').value,
    }
  });
  if (res.ok) {
    el.innerHTML = `<span style="color:var(--success)">${escHtml(res.message)} (${res.latency_ms}ms)</span>`;
  } else {
    el.innerHTML = `<span style="color:var(--danger)">${escHtml(res.message)}</span>`;
  }
}

function toggleApiKeyVisibility() {
  const input = document.getElementById('cfg_api_key');
  const btn = document.getElementById('apiKeyToggle');
  if (input.type === 'password') {
    input.type = 'text';
    btn.textContent = '🔒';
  } else {
    input.type = 'password';
    btn.textContent = '👁';
  }
}

async function runCleanup() {
  if (!confirm('确定要清理低质量记忆吗？此操作会删除噪音记忆。')) return;
  toast('正在清理...', 'info');
  const res = await api('/api/memories/cleanup', { method: 'POST', body: {} });
  toast(`清理完成: ${JSON.stringify(res.stats || {})}`, 'success');
}

async function runFusionFromSettings() {
  toast('正在运行融合...', 'info');
  const res = await api('/api/fusion/run', { method: 'POST', body: {} });
  toast(`融合完成: ${JSON.stringify(res.stats || {})}`, 'success');
}

async function cleanupReasoningChains() {
  if (!confirm('确定要清理低质量推理链吗？\n\n清理规则：\n- 重要性 ≤ 3 的推理链\n- 失败但没有失败原因的推理链\n- 没有推理步骤的推理链')) return;
  toast('正在清理推理链...', 'info');
  const res = await api('/api/reasoning-chains/cleanup', { method: 'POST', body: { min_importance: 4 } });
  if (res.stats) {
    const s = res.stats;
    toast(`清理完成: 停用 ${s.deactivated} 条 (低重要性:${s.low_importance}, 失败无原因:${s.failure_no_reason}, 无步骤:${s.no_steps})`, 'success');
  } else {
    toast('清理完成', 'success');
  }
  renderPage('reasoning');
}

async function cleanupGenericMemories() {
  if (!confirm('确定要清理无效记忆吗？\n\n清理规则：\n- 标题以"推理提取:"开头的旧格式记忆\n- 叙事包含"从推理中提取的知识"的通用记忆\n- 叙事过短且无事实的记忆')) return;
  toast('正在清理无效记忆...', 'info');
  const res = await api('/api/memories/cleanup-generic', { method: 'POST', body: {} });
  if (res.stats) {
    const s = res.stats;
    toast(`清理完成: 停用 ${s.deactivated} 条 (旧格式:${s.old_reasoning_format}, 无叙事:${s.no_narrative_or_facts}, 通用叙事:${s.generic_narrative})`, 'success');
  } else {
    toast('清理完成', 'success');
  }
  renderPage('memories');
}

async function saveSettings() {
  const cfg = {
    llm: {
      provider: document.getElementById('cfg_provider').value,
      base_url: document.getElementById('cfg_base_url').value,
      api_key: document.getElementById('cfg_api_key').value,
      model: document.getElementById('cfg_model').value,
      max_tokens: parseInt(document.getElementById('cfg_max_tokens').value) || 4096,
      timeout: parseInt(document.getElementById('cfg_timeout').value) || 60,
      max_retries: 3,
    },
    fusion: {
      enabled: true,
      interval_seconds: parseInt(document.getElementById('cfg_fusion_interval').value) || 300,
      similarity_threshold: parseFloat(document.getElementById('cfg_similarity').value) || 0.75,
      contradiction_window_days: 7,
    },
    pruning: {
      enabled: true,
      interval_hours: parseInt(document.getElementById('cfg_pruning_hours').value) || 6,
      expire_days: parseInt(document.getElementById('cfg_expire_days').value) || 30,
      idle_threshold_seconds: 300,
      auto_on_idle: true,
    },
    context: {
      inject_on_session_start: true,
      strategy: document.getElementById('cfg_ctx_strategy').value,
      max_chars: parseInt(document.getElementById('cfg_ctx_max_chars').value) || 800,
      include_types: ["user", "reference", "feedback", "project"],
    },
    server: {
      host: document.getElementById('cfg_host').value,
      port: parseInt(document.getElementById('cfg_port').value) || 38800,
    },
    embedding: { enabled: true, model_name: "all-MiniLM-L6-v2", dimension: 384 },
    hook: { skip_tools: ["TodoWrite", "AskUserQuestion", "ListMcpResourcesTool"], max_queue_size: 500, batch_size: 5 },
  };
  await api('/api/config', { method: 'POST', body: cfg });
  toast('配置已保存', 'success');
}

/* ═══ Desktop Pet ═════════════════════════════════════════════════ */
const Pet = {
  el: null,
  tooltip: null,
  state: 'sleeping',
  _drag: { active: false, x: 0, y: 0 },

  init() {
    this.el = document.getElementById('pet');
    this.tooltip = document.getElementById('petTooltip');
    const container = document.getElementById('petContainer');

    // Drag support
    container.addEventListener('mousedown', (e) => {
      this._drag.active = true;
      this._drag.x = e.clientX - container.offsetLeft;
      this._drag.y = e.clientY - container.offsetTop;
      container.style.cursor = 'grabbing';
    });
    document.addEventListener('mousemove', (e) => {
      if (!this._drag.active) return;
      container.style.left = (e.clientX - this._drag.x) + 'px';
      container.style.top = (e.clientY - this._drag.y) + 'px';
      container.style.right = 'auto';
      container.style.bottom = 'auto';
    });
    document.addEventListener('mouseup', () => {
      this._drag.active = false;
      container.style.cursor = 'grab';
    });
  },

  setState(state, msg) {
    if (!this.el) return;
    this.el.className = 'pet ' + state;
    this.state = state;
    if (msg && this.tooltip) this.tooltip.textContent = msg;
  },

  happy(msg) { this.setState('happy', msg || 'New memory!'); setTimeout(() => this.idle(), 2000); },
  working(msg) { this.setState('working', msg || 'Processing...'); },
  thinking(msg) { this.setState('thinking', msg || 'Searching...'); },
  idle() { this.setState('sleeping', 'Memory System'); },
};

/* ═══ Memory Card Enhancements ═══════════════════════════════════ */
const TYPE_ICONS = {
  discovery: '💡',
  bugfix: '🐛',
  feature: '🚀',
  change: '🔧',
  decision: '📌',
  refactor: '♻️',
  security_note: '🔒',
};

const MATURITY_STAGES = ['🌱', '🌿', '🌳', '🌲', '✨'];

function getTypeIcon(obsType) {
  return TYPE_ICONS[obsType] || '📝';
}

function getFlameHTML(importance) {
  const level = Math.min(5, Math.max(1, Math.round((importance || 5) / 2)));
  const flames = '🔥'.repeat(Math.min(level, 3));
  return `<span class="mc-importance flame-${level}"><span class="flame">${flames}</span> ${importance || 5}</span>`;
}

function getMaturityBar(memory) {
  // Maturity based on: age, access count, stability
  const meta = parseMetadata(memory.metadata);
  const stability = meta.stability || 1.0;
  const accessCount = memory.access_count || 0;
  const age = memory.created_at ? (Date.now() - new Date(memory.created_at).getTime()) / 86400000 : 0;

  // Score 0-100: combination of stability, access count, and age
  const stabilityScore = Math.min(40, stability * 10);
  const accessScore = Math.min(30, accessCount * 5);
  const ageScore = Math.min(30, age * 2);
  const total = Math.min(100, stabilityScore + accessScore + ageScore);

  const stageIdx = Math.min(4, Math.floor(total / 20));
  const stageIcon = MATURITY_STAGES[stageIdx];

  return `
    <div class="mc-maturity">
      <div class="mc-maturity-fill" style="width:${total}%"></div>
    </div>
    <div class="maturity-label">${stageIcon} 成熟度 ${Math.round(total)}%</div>
  `;
}

/* ═══ Reasoning Chains ══════════════════════════════════════════ */
async function renderReasoningChains(c) {
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const [chainsData, statsData] = await Promise.all([
    api('/api/reasoning-chains?limit=50'),
    api('/api/reasoning-chains/stats'),
  ]);
  const chains = chainsData.chains || [];
  const total = chainsData.total || 0;
  const setBadge = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  setBadge('reasoningCount', total);

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title">推理链</div>
          <div class="page-subtitle">${total} 条结构化问题解决记录</div>
        </div>
      </div>
      ${chains.length ? `
        <div class="card">
          <div class="table-wrap">
            <table>
              <thead><tr><th>ID</th><th>问题</th><th>模块</th><th>结果</th><th>步骤</th><th>重要性</th><th>时间</th></tr></thead>
              <tbody>
                ${chains.map(ch => {
                  const steps = typeof ch.steps === 'string' ? JSON.parse(ch.steps || '[]') : (ch.steps || []);
                  const outcomeClass = ch.outcome === 'success' ? 'tag-success' : ch.outcome === 'failure' ? 'tag-danger' : 'tag-default';
                  return `<tr class="clickable" onclick="showReasoningDetail(${ch.id})">
                    <td style="font-variant-numeric:tabular-nums">#${ch.id}</td>
                    <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(ch.question || '')}</td>
                    <td>${ch.module_id ? `<span class="tag tag-info">模块#${ch.module_id}</span>` : '--'}</td>
                    <td><span class="tag ${outcomeClass}">${escHtml(ch.outcome || 'pending')}</span></td>
                    <td>${steps.length} 步</td>
                    <td>${ch.importance || 5}</td>
                    <td style="font-size:12px;color:var(--text-tertiary)">${fmtTime(ch.created_at)}</td>
                  </tr>`;
                }).join('')}
              </tbody>
            </table>
          </div>
        </div>
      ` : '<div class="callout">暂无推理链。推理链会在会话结束时自动提取。</div>'}
    </div>
  `;
}

async function showReasoningDetail(id) {
  const c = document.getElementById('content');
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const data = await api(`/api/reasoning-chains/detail?id=${id}`);
  const ch = data.chain;
  if (!ch) { toast('未找到推理链', 'error'); return; }

  const steps = typeof ch.steps === 'string' ? JSON.parse(ch.steps || '[]') : (ch.steps || []);
  const facts = typeof ch.extracted_facts === 'string' ? JSON.parse(ch.extracted_facts || '[]') : (ch.extracted_facts || []);
  const outcomeClass = ch.outcome === 'success' ? 'tag-success' : ch.outcome === 'failure' ? 'tag-danger' : 'tag-default';

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title"><span style="cursor:pointer;color:var(--accent)" onclick="navigate('reasoning')">推理链</span> <span style="color:var(--text-tertiary);font-weight:400">/</span> #${ch.id}</div>
          <div class="page-subtitle">${escHtml(ch.question || '无标题')}</div>
        </div>
        <span class="tag ${outcomeClass}">${escHtml(ch.outcome || 'pending')}</span>
      </div>

      <div class="meta-grid">
        <div class="meta-card"><div class="meta-label">项目</div><div class="meta-value">${escHtml(ch.project || '--')}</div></div>
        ${ch.module_id ? `<div class="meta-card"><div class="meta-label">模块</div><div class="meta-value">#${ch.module_id}</div></div>` : ''}
        <div class="meta-card"><div class="meta-label">重要性</div><div class="meta-value">${ch.importance || 5}</div></div>
        <div class="meta-card"><div class="meta-label">创建时间</div><div class="meta-value">${fmtTime(ch.created_at)}</div></div>
      </div>

      ${steps.length > 0 ? `
        <div class="card">
          <div class="card-header"><div class="card-title">推理步骤</div><span class="tag tag-default">${steps.length} 步</span></div>
          <div class="stack-list">
            ${steps.map((s, i) => `
              <div class="stack-item" style="flex-direction:column;align-items:stretch">
                <div style="font-size:12px;color:var(--accent);font-weight:600;margin-bottom:4px">步骤 ${i + 1}</div>
                ${s.thought ? `<div style="margin-bottom:4px;font-size:13px"><strong style="color:var(--text-tertiary)">思考:</strong> ${escHtml(s.thought)}</div>` : ''}
                ${s.action ? `<div style="margin-bottom:4px;font-size:13px"><strong style="color:var(--text-tertiary)">行动:</strong> ${escHtml(s.action)}</div>` : ''}
                ${s.observation ? `<div style="font-size:13px"><strong style="color:var(--text-tertiary)">观察:</strong> ${escHtml(s.observation)}</div>` : ''}
              </div>
            `).join('')}
          </div>
        </div>
      ` : ''}

      ${ch.outcome_summary ? `
        <div class="card">
          <div class="card-header"><div class="card-title">结果总结</div></div>
          <div style="font-size:13px;color:var(--text-secondary);line-height:1.7">${escHtml(ch.outcome_summary)}</div>
        </div>
      ` : ''}

      ${ch.failure_reason ? `
        <div class="card" style="border-left:3px solid var(--danger)">
          <div class="card-header"><div class="card-title" style="color:var(--danger)">失败原因</div></div>
          <div style="font-size:13px;color:var(--text-secondary);line-height:1.7">${escHtml(ch.failure_reason)}</div>
        </div>
      ` : ''}

      ${facts.length > 0 ? `
        <div class="card">
          <div class="card-header"><div class="card-title">提取的事实</div></div>
          <ul style="margin:0;padding-left:20px;font-size:13px;color:var(--text-secondary);line-height:1.8">
            ${facts.map(f => `<li>${escHtml(f)}</li>`).join('')}
          </ul>
        </div>
      ` : ''}
    </div>
  `;
}

/* ═══ Claude Code Session Detail ═════════════════════════════════ */
async function renderCCSessionDetail(c, sessionId, project) {
  if (!sessionId) return;
  c.innerHTML = '<div class="page-shell"><div style="display:flex;justify-content:center;padding:48px"><div class="spinner"></div></div></div>';
  const data = await api(`/api/claude-sessions/detail?id=${sessionId}&project=${encodeURIComponent(project || '')}`);
  if (data.error) {
    c.innerHTML = `<div class="page-shell"><div class="callout">${escHtml(data.error)}</div></div>`;
    return;
  }
  const s = data.session;
  const messages = s.messages || [];

  const metaItems = [
    s.project && ['项目', s.project],
    s.cwd && ['目录', s.cwd],
    s.git_branch && ['分支', s.git_branch],
    s.version && ['版本', s.version],
    s.entrypoint && ['入口', s.entrypoint],
  ].filter(Boolean);

  c.innerHTML = `
    <div class="page-shell">
      <div class="page-hero">
        <div>
          <div class="page-title"><span style="cursor:pointer;color:var(--accent)" onclick="navigate('sessions')">会话</span> <span style="color:var(--text-tertiary);font-weight:400">/</span> ${sessionId.slice(0,12)}...</div>
          <div class="page-subtitle">${s.message_count || messages.length} 条消息 · ${escHtml(s.project || '')}</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="extractCCSessionMemories('${escHtml(sessionId)}', '${escHtml(project || '')}')" id="ccExtractBtn">提取记忆</button>
      </div>

      ${metaItems.length ? `
        <div class="meta-grid">
          ${metaItems.map(([label, value]) => `
            <div class="meta-card"><div class="meta-label">${label}</div><div class="meta-value">${escHtml(value)}</div></div>
          `).join('')}
        </div>
      ` : ''}

      <div style="display:flex;flex-direction:column;gap:8px;max-height:70vh;overflow-y:auto">
        ${messages.map(m => `
          <div class="card" style="padding:12px;border-left:3px solid ${m.role==='user'?'var(--accent)':'var(--success)'}">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px">
              <span style="font-size:12px;font-weight:600;color:${m.role==='user'?'var(--accent)':'var(--success)'}">${m.role==='user'?'用户':'AI'}</span>
              <span style="font-size:11px;color:var(--text-tertiary)">${fmtTime(m.timestamp)}</span>
            </div>
            <div style="font-size:13px;white-space:pre-wrap;word-break:break-word">${escHtml(m.content).substring(0, 2000)}</div>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

async function batchExtractCC() {
  const btn = document.getElementById('batchExtractBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px"></div> 启动中...';
  toast('正在启动批量提取（含记忆+推理链）...', 'info');
  try {
    const res = await api('/api/claude-sessions/batch-extract', { method: 'POST', body: { limit: 100, min_msgs: 5 } });
    if (res.error) {
      toast(`启动失败: ${res.error}`, 'error');
    } else {
      toast(`已启动：${res.sessions_to_process} 个会话待处理`, 'success');
      // Poll progress
      let dots = 0;
      const poll = setInterval(async () => {
        dots++;
        btn.textContent = '提取中' + '.'.repeat((dots % 3) + 1);
      }, 1000);
      setTimeout(() => { clearInterval(poll); btn.disabled = false; btn.textContent = '批量提取记忆'; }, 120000);
    }
  } catch (e) {
    toast(`出错: ${e.message}`, 'error');
    btn.disabled = false;
    btn.textContent = '批量提取记忆';
  }
}

async function extractCCSessionMemories(sessionId, project) {
  const btn = document.getElementById('ccExtractBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px"></div> 提取中...';
  toast('正在使用 AI 提取结构化记忆...', 'info');
  try {
    const res = await api('/api/claude-sessions/extract', { method: 'POST', body: { id: sessionId, project } });
    if (res.error) {
      toast(`提取失败: ${res.error}`, 'error');
    } else {
      toast(`提取完成，生成了 ${res.memories_created} 条记忆`, 'success');
    }
  } catch (e) {
    toast(`提取出错: ${e.message}`, 'error');
  }
  btn.disabled = false;
  btn.textContent = '重新提取';
}

function parseMetadata(meta) {
  if (!meta) return {};
  if (typeof meta === 'object') return meta;
  try { return JSON.parse(meta); } catch { return {}; }
}

/* ═══ Helpers ═══════════════════════════════════════════════════ */
function fmtTime(ts) {
  if (!ts) return '--';
  try {
    const d = new Date(ts);
    if (isNaN(d)) return ts;
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch { return ts; }
}

function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/`/g,'&#96;');
}

/* ═══ Init ══════════════════════════════════════════════════════ */
Pet.init();
navigate('dashboard');
