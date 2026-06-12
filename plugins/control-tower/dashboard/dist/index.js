(() => {
  const escapeHtml = (value) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
  const root = document.createElement('div');
  root.className = 'control-tower-plugin';
  root.innerHTML = `
    <section class="control-tower-card">
      <p class="control-tower-eyebrow">Hermes Control Tower</p>
      <h1>Event-driven workflow control plane</h1>
      <p>
        Brain taskpool markdown is the task-lifecycle SSOT; Kanban is an
        execution projection, and Dashboard/Console are operator projections.
        This screen reports projection drift instead of advancing lifecycle state.
        Phase 15 enables explicitly approved, command-digest Kanban/taskpool
        closeout write-through plus digest-bound rollback/reopen while keeping
        conflicting evidence override disabled.
        This dashboard can record operator intent, human decisions, read-only
        runs, evidence gates, ledger closeouts, blocked external write intents,
        readiness reviews, authorization decisions, preflights, digest-bound
        Kanban/taskpool write execution runs, and digest-bound rollback runs.
        Durable workflow facts belong to <code>~/.hermes/control_tower.db</code>.
      </p>
      <div class="control-tower-actions control-tower-primary-actions">
        <button id="control-tower-refresh" type="button">Refresh snapshot</button>
      </div>
      <details class="control-tower-debug-actions">
        <summary>Operator/debug actions</summary>
        <div class="control-tower-actions">
          <button id="control-tower-propose-refresh" type="button">
            Propose RefreshProjection
          </button>
          <button id="control-tower-approve-first" type="button">
            Approve first pending
          </button>
          <button id="control-tower-reject-first" type="button">
            Reject first pending
          </button>
          <button id="control-tower-plan-first" type="button">
            Plan first proposed
          </button>
          <button id="control-tower-policy-first" type="button">
            Review first plan policy
          </button>
          <button id="control-tower-execute-read-only" type="button">
            Execute read-only plan
          </button>
          <button id="control-tower-record-evidence" type="button">
            Record sample evidence
          </button>
          <button id="control-tower-evidence-gate" type="button">
            Evaluate evidence gate
          </button>
          <button id="control-tower-request-closeout" type="button">
            Request closeout auth
          </button>
          <button id="control-tower-approve-closeout" type="button">
            Approve closeout auth
          </button>
          <button id="control-tower-execute-closeout" type="button">
            Execute approved closeout
          </button>
          <button id="control-tower-prepare-external-intent" type="button">
            Prepare external write intent
          </button>
          <button id="control-tower-review-external-intent" type="button">
            Review external intent readiness
          </button>
          <button id="control-tower-request-external-auth" type="button">
            Request external write auth
          </button>
          <button id="control-tower-approve-external-auth" type="button">
            Approve external write auth
          </button>
          <button id="control-tower-prepare-external-preflight" type="button">
            Prepare external write preflight
          </button>
          <button id="control-tower-execute-external-write" type="button">
            Execute approved adapter write
          </button>
          <button id="control-tower-rollback-external-write" type="button">
            Rollback adapter write
          </button>
        </div>
      </details>
      <div id="control-tower-summary" class="control-tower-summary">Loading…</div>
      <section class="control-tower-card control-tower-operator-overview-card">
        <p class="control-tower-eyebrow">Operator Overview</p>
        <h2>Can Hermes progress now?</h2>
        <p>First-screen read-only answer for progressability, blockers, critical alerts, human gates, and evidence debt.</p>
        <div id="control-tower-operator-overview">Loading operator overview…</div>
      </section>
      <section class="control-tower-card control-tower-missions-card">
        <p class="control-tower-eyebrow">Mission control</p>
        <h2>High-level directions</h2>
        <p>Tracks active long-running Missions, open human decisions, cron readiness, and the next operator action.</p>
        <div id="control-tower-missions">Loading missions…</div>
      </section>
      <section class="control-tower-card control-tower-mission-flow-card">
        <p class="control-tower-eyebrow">Mission event flow</p>
        <h2>Signals and taskpool bridge</h2>
        <p>Read-only operator projection for event-driven Mission wakeups, consumed latency, stale signals, and taskpool bridge status.</p>
        <div id="control-tower-mission-flow">Loading mission flow…</div>
      </section>
      <details class="control-tower-debug-snapshot">
        <summary>Raw snapshot JSON</summary>
        <pre id="control-tower-snapshot">Loading snapshot…</pre>
      </details>
    </section>
  `;

  const mount = document.getElementById('root') || document.body;
  mount.appendChild(root);

  const snapshotEl = document.getElementById('control-tower-snapshot');
  const summaryEl = document.getElementById('control-tower-summary');
  const operatorOverviewEl = document.getElementById('control-tower-operator-overview');
  const missionsEl = document.getElementById('control-tower-missions');
  const missionFlowEl = document.getElementById('control-tower-mission-flow');
  let latestSnapshot = null;

  const authFetch = (url, options) => {
    const sdk = window.__HERMES_PLUGIN_SDK__;
    if (sdk?.authedFetch) return sdk.authedFetch(url, options);
    const headers = new Headers(options?.headers || {});
    const token = window.__HERMES_SESSION_TOKEN__;
    if (token && !headers.has('X-Hermes-Session-Token')) headers.set('X-Hermes-Session-Token', token);
    return window.fetch(url, { ...options, headers, credentials: options?.credentials || 'include' });
  };

  const renderMissions = (data) => {
    if (!missionsEl) return;
    const board = data?.board || [];
    const inbox = data?.inbox || [];
    if (!board.length && !inbox.length) {
      missionsEl.innerHTML = '<p>No active Missions yet.</p>';
      return;
    }
    const rows = board.map((m) => `
      <tr>
        <td><code>${m.mission_id}</code></td>
        <td>${m.status}</td>
        <td>${m.ticks_used}/${m.ticks_max ?? '∞'}</td>
        <td>${m.cron_installed ? 'yes' : 'no'}</td>
        <td>${m.needs_user ? 'yes' : 'no'}</td>
        <td><code>${m.next_step}</code></td>
      </tr>
      <tr class="control-tower-mission-direction"><td colspan="6">${m.direction}</td></tr>
    `).join('');
    const decisions = inbox.map((item) => {
      const d = item.decision;
      const options = (d.options || []).map((option) => `<li>${option}</li>`).join('');
      return `<li><strong>${d.question}</strong><ul>${options}</ul><p>Recommended: ${d.recommended_option || '—'}</p><code>${item.decide_command}</code></li>`;
    }).join('');
    missionsEl.innerHTML = `
      <p><strong>${board.length}</strong> mission(s), <strong>${inbox.length}</strong> open decision(s).</p>
      <table class="control-tower-missions-table">
        <thead><tr><th>ID</th><th>Status</th><th>Ticks</th><th>Cron</th><th>User</th><th>Next</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${inbox.length ? `<h3>Needs your decision</h3><ul>${decisions}</ul>` : '<p>No open human decisions.</p>'}
    `;
  };


  const formatSeconds = (seconds) => {
    const value = Number(seconds || 0);
    if (value < 60) return `${value}s`;
    if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s`;
    return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`;
  };

  const renderMissionFlow = (data) => {
    if (!missionFlowEl) return;
    const flow = data?.mission_flow_signals;
    const bridge = data?.mission_taskpool_bridge;
    if (!flow || flow.status !== 'ok') {
      missionFlowEl.textContent = `Mission flow unavailable: ${flow?.reason || flow?.status || 'missing snapshot'}`;
      return;
    }
    const flowRows = (flow.missions || []).map((mission) => {
      const metrics = mission.metrics || {};
      const pendingRows = (mission.pending_signals || []).map((signal) => `
        <li>
          <code>${signal.signal_id}</code> ${signal.signal_type}
          age=${formatSeconds(signal.age_seconds)} stale=${signal.stale ? 'yes' : 'no'}
        </li>
      `).join('');
      return `
        <tr>
          <td><code>${mission.mission_id}</code></td>
          <td>${metrics.pending_count ?? 0}</td>
          <td>${metrics.stale_count ?? 0}</td>
          <td>${formatSeconds(metrics.oldest_pending_age_seconds)}</td>
          <td>${metrics.dispatched_count ?? 0}</td>
          <td>${metrics.consumed_count ?? 0}</td>
          <td>${metrics.avg_consumed_latency_seconds == null ? '—' : formatSeconds(metrics.avg_consumed_latency_seconds)}</td>
        </tr>
        ${pendingRows ? `<tr class="control-tower-mission-direction"><td colspan="7"><ul>${pendingRows}</ul></td></tr>` : ''}
      `;
    }).join('');
    const bridgeRows = (bridge?.missions || []).map((mission) => {
      const events = (mission.recent_events || []).map((event) => `
        <li>
          <code>${event.task_id || event.event_id}</code>
          ${event.from_status || '—'} → ${event.to_status || '—'}
          ${event.pending_signal_id ? `pending=<code>${event.pending_signal_id}</code>` : 'consumed/no-pending'}
        </li>
      `).join('');
      return `
        <tr>
          <td><code>${mission.mission_id}</code></td>
          <td>${mission.bridged_event_count ?? 0}</td>
          <td>${mission.pending_bridge_signal_count ?? 0}</td>
          <td>${events ? `<ul>${events}</ul>` : '—'}</td>
        </tr>
      `;
    }).join('');
    missionFlowEl.innerHTML = `
      <div class="control-tower-summary control-tower-mission-flow-summary">
        <span>Pending signals: <strong>${flow.pending_count ?? 0}</strong></span>
        <span>Stale signals: <strong>${flow.stale_count ?? 0}</strong></span>
        <span>Oldest pending: <strong>${formatSeconds(flow.oldest_pending_age_seconds)}</strong></span>
        <span>Dispatched: <strong>${flow.dispatched_count ?? 0}</strong></span>
        <span>Maintenance ticks: <strong>${flow.maintenance_tick_count ?? 0}</strong></span>
        <span>Consumed: <strong>${flow.consumed_count ?? 0}</strong></span>
        <span>Avg latency: <strong>${flow.avg_consumed_latency_seconds == null ? '—' : formatSeconds(flow.avg_consumed_latency_seconds)}</strong></span>
        <span>Bridge pending: <strong>${bridge?.pending_bridge_signal_count ?? 0}</strong></span>
      </div>
      ${flowRows ? `
        <h3>Pending flow signals</h3>
        <table class="control-tower-missions-table control-tower-mission-flow-table">
          <thead><tr><th>Mission</th><th>Pending</th><th>Stale</th><th>Oldest</th><th>Dispatched</th><th>Consumed</th><th>Avg latency</th></tr></thead>
          <tbody>${flowRows}</tbody>
        </table>
      ` : '<p>No pending Mission flow signals.</p>'}
      ${bridgeRows ? `
        <h3>Taskpool bridge</h3>
        <table class="control-tower-missions-table control-tower-mission-bridge-table">
          <thead><tr><th>Mission</th><th>Bridge events</th><th>Pending bridge signals</th><th>Recent bridged tasks</th></tr></thead>
          <tbody>${bridgeRows}</tbody>
        </table>
      ` : '<p>No recent taskpool bridge events.</p>'}
    `;
  };

  const renderOperatorOverview = (data) => {
    if (!operatorOverviewEl) return;
    const overview = data?.operator_overview;
    if (!overview) {
      operatorOverviewEl.textContent = 'Operator overview unavailable: snapshot missing operator_overview.';
      return;
    }
    const alerts = (overview.critical_alerts || []).map((alert) => `
      <li class="control-tower-alert control-tower-alert-${alert.severity || 'info'}">
        <strong>${alert.kind || 'alert'}</strong>: ${alert.message || ''}
      </li>
    `).join('');
    const blockers = (overview.blocker_summary || []).map((item) => `
      <li><code>${item.reason}</code> × ${item.count}</li>
    `).join('');
    const nextActions = (overview.next_safe_actions || []).map((item) => `
      <li>
        <strong>${item.action || 'action'}</strong>
        <code>${item.mode || 'unknown'}</code>
        <span>${item.reason || ''}</span>
        <p>${item.command || ''}</p>
      </li>
    `).join('');
    const evidence = overview.evidence_debt || {};
    const human = overview.human_gate || {};
    const sourceDrift = overview.source_drift || {};
    const taskpoolRuntime = overview.taskpool_runtime || {};
    const projectionGovernance = overview.projection_governance || data?.projection_governance || {};
    operatorOverviewEl.innerHTML = `
      <div class="control-tower-operator-headline control-tower-operator-${overview.status}">
        <strong>${overview.status || 'unknown'}</strong>
        <span>${overview.headline || 'No headline available.'}</span>
      </div>
      <div class="control-tower-operator-meta">
        <span>Recommended next action: <strong>${overview.recommended_next_action || 'none'}</strong></span>
        <span>Human gate: <strong>${human.status || 'unknown'} (${human.pending_count ?? 0})</strong></span>
        <span>Evidence: <strong>${evidence.status || 'unknown'}</strong></span>
        <span>Stale/conflicting evidence: <strong>${evidence.stale_or_conflicting ?? 0}</strong></span>
        <span>Source drift: <strong>${sourceDrift.status || 'unknown'}</strong></span>
        <span>Runtime taskpool: <strong>${escapeHtml(taskpoolRuntime.status || 'unknown')}</strong>${taskpoolRuntime.next_task_id ? ` (${escapeHtml(taskpoolRuntime.next_task_id)})` : ''}</span>
        <span>Projection governance: <strong>${escapeHtml(projectionGovernance.status || 'unknown')}</strong>${projectionGovernance.score == null ? '' : ` score=${escapeHtml(projectionGovernance.score)}`}</span>
        <span>SSOT: <strong>${escapeHtml(projectionGovernance.ssot || 'brain_taskpool_markdown')}</strong></span>
      </div>
      <h3>Critical alerts</h3>
      ${alerts ? `<ul>${alerts}</ul>` : '<p>No critical alerts.</p>'}
      <h3>Next safe actions</h3>
      ${nextActions ? `<ul class="control-tower-next-actions">${nextActions}</ul>` : '<p>No safe actions required.</p>'}
      <h3>Blocker summary</h3>
      ${blockers ? `<ul>${blockers}</ul>` : '<p>No blockers observed.</p>'}
    `;
  };


  const renderSummary = (data) => {
    const pending = data?.human_inbox?.pending_count ?? 0;
    const decided = data?.human_inbox?.decided_count ?? 0;
    const proposed = data?.command_registry?.proposed_count ?? 0;
    const plans = data?.executor_plans?.planned_count ?? 0;
    const policyReviews = data?.execution_policy?.review_count ?? 0;
    const readOnlyEligible = data?.execution_policy?.read_only_eligible_count ?? 0;
    const policyBlocked = data?.execution_policy?.blocked_count ?? 0;
    const readOnlyRuns = data?.read_only_executor?.run_count ?? 0;
    const evidenceReviews = data?.evidence_policy?.review_count ?? 0;
    const closeoutPending = data?.closeout_authorizations?.pending_count ?? 0;
    const closeoutDecided = data?.closeout_authorizations?.decided_count ?? 0;
    const closeoutExpired = data?.closeout_authorizations?.expired_count ?? 0;
    const closeoutRuns = data?.closeout_execution?.run_count ?? 0;
    const externalWriteIntents = data?.external_adapter_write_intents?.intent_count ?? 0;
    const externalWriteIntentBlocked = data?.external_adapter_write_intents?.blocked_count ?? 0;
    const externalWriteReviews = data?.external_adapter_write_intent_reviews?.review_count ?? 0;
    const externalWriteReviewBlocked = data?.external_adapter_write_intent_reviews?.blocked_count ?? 0;
    const externalWriteAuths = data?.external_adapter_write_authorizations?.authorization_count ?? 0;
    const externalWriteAuthApproved = data?.external_adapter_write_authorizations?.approved_count ?? 0;
    const externalWriteAllowed = data?.external_adapter_write_authorizations?.write_through_allowed_count ?? 0;
    const externalWritePreflights = data?.external_adapter_write_execution_preflights?.preflight_count ?? 0;
    const externalWritePreflightBlocked = data?.external_adapter_write_execution_preflights?.blocked_count ?? 0;
    const externalWriteRuns = data?.external_adapter_write_execution_runs?.run_count ?? 0;
    const externalWriteRunsCompleted = data?.external_adapter_write_execution_runs?.completed_count ?? 0;
    const externalRollbackRuns = data?.external_adapter_write_rollback_runs?.run_count ?? 0;
    const externalRollbackCompleted = data?.external_adapter_write_rollback_runs?.completed_count ?? 0;
    const externalRollbackEnabled = data?.external_adapter_write_rollback_runs?.rollback_reopen_enabled === true;
    const externalWriteExecutorEnabled = data?.external_adapter_write_execution_runs?.external_write_executor_enabled === true;
    const externalCloseoutWrites = data?.closeout_execution?.external_adapter_write_through_enabled === true;
    const writesEnabled = data?.execution_policy?.writes_enabled === true;
    const closeoutEnabled = data?.closeout_execution?.mode === 'control_tower_ledger_closeout_consumer';
    const ready = data?.workflow_map?.ready?.length ?? 0;
    const blocked = data?.workflow_map?.blocked?.length ?? 0;
    if (summaryEl) {
      summaryEl.innerHTML = `
        <span>Commands proposed: <strong>${proposed}</strong></span>
        <span>Human pending: <strong>${pending}</strong></span>
        <span>Human decided: <strong>${decided}</strong></span>
        <span>Executor plans: <strong>${plans}</strong></span>
        <span>Policy reviews: <strong>${policyReviews}</strong></span>
        <span>Read-only eligible: <strong>${readOnlyEligible}</strong></span>
        <span>Read-only runs: <strong>${readOnlyRuns}</strong></span>
        <span>Policy blocked: <strong>${policyBlocked}</strong></span>
        <span>Evidence reviews: <strong>${evidenceReviews}</strong></span>
        <span>Closeout auth pending: <strong>${closeoutPending}</strong></span>
        <span>Closeout auth decided: <strong>${closeoutDecided}</strong></span>
        <span>Closeout auth expired: <strong>${closeoutExpired}</strong></span>
        <span>Closeout runs: <strong>${closeoutRuns}</strong></span>
        <span>External write intents: <strong>${externalWriteIntents}</strong></span>
        <span>External intent blocked: <strong>${externalWriteIntentBlocked}</strong></span>
        <span>External intent reviews: <strong>${externalWriteReviews}</strong></span>
        <span>External review blocked: <strong>${externalWriteReviewBlocked}</strong></span>
        <span>External write auths: <strong>${externalWriteAuths}</strong></span>
        <span>External auth approved: <strong>${externalWriteAuthApproved}</strong></span>
        <span>External write preflights: <strong>${externalWritePreflights}</strong></span>
        <span>External preflight blocked: <strong>${externalWritePreflightBlocked}</strong></span>
        <span>External write runs: <strong>${externalWriteRuns}</strong></span>
        <span>External write completed: <strong>${externalWriteRunsCompleted}</strong></span>
        <span>External rollback runs: <strong>${externalRollbackRuns}</strong></span>
        <span>External rollback completed: <strong>${externalRollbackCompleted}</strong></span>
        <span>External rollback/reopen: <strong>${externalRollbackEnabled ? 'yes' : 'no'}</strong></span>
        <span>External write executor: <strong>${externalWriteExecutorEnabled ? 'yes' : 'no'}</strong></span>
        <span>External write allowed: <strong>${externalWriteAllowed}</strong></span>
        <span>Writes enabled: <strong>${writesEnabled ? 'yes' : 'no'}</strong></span>
        <span>External closeout writes: <strong>${externalCloseoutWrites ? 'yes' : 'no'}</strong></span>
        <span>Closeout enabled: <strong>${closeoutEnabled ? 'ledger-only' : 'no'}</strong></span>
        <span>Workflow ready: <strong>${ready}</strong></span>
        <span>Blocked gates: <strong>${blocked}</strong></span>
      `;
    }
  };

  const loadMissions = () => {
    if (missionsEl) missionsEl.textContent = 'Loading missions…';
    authFetch('/api/plugins/control-tower/missions')
      .then((response) => response.json())
      .then((data) => renderMissions(data))
      .catch((error) => {
        if (missionsEl) missionsEl.textContent = `Mission load failed: ${error}`;
      });
  };

  const loadSnapshot = () => {
    if (snapshotEl) snapshotEl.textContent = 'Loading snapshot…';
    loadMissions();
    authFetch('/api/plugins/control-tower/snapshot')
      .then((response) => response.json())
      .then((data) => {
        latestSnapshot = data;
        renderSummary(data);
        renderOperatorOverview(data);
        renderMissionFlow(data);
        if (snapshotEl) snapshotEl.textContent = JSON.stringify(data, null, 2);
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Snapshot load failed: ${error}`;
      });
  };

  const proposeRefresh = () => {
    authFetch('/api/plugins/control-tower/commands/propose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        command_type: 'RefreshProjection',
        actor_type: 'human',
        actor_id: 'dashboard',
        target_type: 'projection',
        target_id: 'overview',
        risk_level: 'L0',
        payload: { reason: 'dashboard proposal-only refresh' },
        idempotency_key: `dashboard-refresh-${new Date().toISOString().slice(0, 16)}`,
      }),
    })
      .then((response) => response.json())
      .then(() => loadSnapshot())
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Command proposal failed: ${error}`;
      });
  };

  const decideFirstPending = (decision) => {
    const approval = latestSnapshot?.human_inbox?.pending?.[0];
    if (!approval) {
      if (snapshotEl) snapshotEl.textContent = 'No pending approval to decide.';
      return;
    }
    authFetch(`/api/plugins/control-tower/approvals/${encodeURIComponent(approval.approval_id)}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        decision,
        actor_type: 'human',
        actor_id: 'dashboard',
        reason: `dashboard ${decision}; no lifecycle action executed`,
      }),
    })
      .then((response) => response.json())
      .then(() => loadSnapshot())
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Approval decision failed: ${error}`;
      });
  };

  const planFirstProposed = () => {
    const command = latestSnapshot?.command_registry?.recent_proposed?.[0];
    if (!command) {
      if (snapshotEl) snapshotEl.textContent = 'No proposed command to plan.';
      return;
    }
    authFetch(`/api/plugins/control-tower/commands/${encodeURIComponent(command.command_id)}/executor-plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_type: 'human',
        actor_id: 'dashboard',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Executor planning failed: ${error}`;
      });
  };

  const reviewFirstPlanPolicy = () => {
    const plan = latestSnapshot?.executor_plans?.recent_plans?.[0];
    if (!plan) {
      if (snapshotEl) snapshotEl.textContent = 'No executor plan to review.';
      return;
    }
    authFetch(`/api/plugins/control-tower/executor-plans/${encodeURIComponent(plan.plan_id)}/policy-review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_type: 'human',
        actor_id: 'dashboard',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Policy review failed: ${error}`;
      });
  };

  const executeFirstReadOnlyPlan = () => {
    const review = latestSnapshot?.execution_policy?.recent_reviews?.find((item) => item.allowed_to_execute === true);
    if (!review) {
      if (snapshotEl) snapshotEl.textContent = 'No read-only eligible policy review to execute.';
      return;
    }
    authFetch(`/api/plugins/control-tower/executor-plans/${encodeURIComponent(review.plan_id)}/execute-read-only`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_type: 'human',
        actor_id: 'dashboard',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Read-only executor run failed: ${error}`;
      });
  };

  const recordSampleEvidence = () => {
    authFetch('/api/plugins/control-tower/evidence', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_id: 'dashboard:sample-evidence',
        evidence_type: 'proof',
        maturity: 'observed',
        source_type: 'dashboard',
        summary: 'Dashboard sample evidence; no closeout executed',
        producer_type: 'human',
        producer_id: 'dashboard',
        acceptance_refs: ['AC-dashboard-sample'],
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Evidence record failed: ${error}`;
      });
  };

  const evaluateEvidenceGate = () => {
    authFetch('/api/plugins/control-tower/evidence-gates/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_id: 'dashboard:sample-evidence',
        risk_level: 'L1',
        acceptance_refs: ['AC-dashboard-sample'],
        actor_type: 'human',
        actor_id: 'dashboard',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Evidence gate failed: ${error}`;
      });
  };

  const requestCloseoutAuthorization = () => {
    const review = latestSnapshot?.evidence_policy?.recent_reviews?.find(
      (entry) => entry.evidence_status === 'satisfied'
    );
    if (!review) {
      if (snapshotEl) snapshotEl.textContent = 'No satisfied evidence gate review to authorize.';
      return;
    }
    authFetch(`/api/plugins/control-tower/evidence-gates/${encodeURIComponent(review.evidence_review_id)}/closeout-authorization`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'owner',
        reason: 'dashboard requested evidence-bound closeout authorization; no closeout executed',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Closeout authorization request failed: ${error}`;
      });
  };

  const approveFirstCloseoutAuthorization = () => {
    const authorization = latestSnapshot?.closeout_authorizations?.pending?.[0];
    if (!authorization) {
      if (snapshotEl) snapshotEl.textContent = 'No pending closeout authorization to approve.';
      return;
    }
    authFetch(`/api/plugins/control-tower/closeout-authorizations/${encodeURIComponent(authorization.closeout_authorization_id)}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        decision: 'approved',
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'owner',
        reason: 'dashboard approved evidence-bound closeout authorization; execution requires explicit execute click',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Closeout authorization decision failed: ${error}`;
      });
  };

  const executeFirstCloseoutAuthorization = () => {
    const authorization = latestSnapshot?.closeout_authorizations?.recent?.find(
      (entry) => entry.status === 'approved' && entry.closeout_allowed === false
    );
    if (!authorization) {
      if (snapshotEl) snapshotEl.textContent = 'No approved closeout authorization to execute.';
      return;
    }
    authFetch(`/api/plugins/control-tower/closeout-authorizations/${encodeURIComponent(authorization.closeout_authorization_id)}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'owner',
        reason: 'dashboard executed evidence-bound closeout inside Control Tower ledger only',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Closeout execution failed: ${error}`;
      });
  };

  const prepareFirstExternalWriteIntent = () => {
    const run = latestSnapshot?.closeout_execution?.recent_runs?.find(
      (entry) => entry.execution_status === 'closeout_completed' && entry.external_adapter_write_through_enabled === false
    );
    if (!run) {
      if (snapshotEl) snapshotEl.textContent = 'No ledger-only closeout run to prepare external write intent for.';
      return;
    }
    const externalRef = run.external_ref || run.task_id;
    if (!externalRef || externalRef.includes('/') || externalRef.endsWith('.md')) {
      if (snapshotEl) snapshotEl.textContent = `Closeout run ${run.closeout_execution_id} has no safe taskpool external_ref.`;
      return;
    }
    authFetch(`/api/plugins/control-tower/closeout-executions/${encodeURIComponent(run.closeout_execution_id)}/external-write-intent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        adapter_name: 'taskpool',
        external_ref: externalRef,
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'owner',
        reason: 'dashboard prepared digest-bound external write intent; adapter write-through remains disabled',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `External write intent preparation failed: ${error}`;
      });
  };

  const reviewFirstExternalWriteIntent = () => {
    const intent = latestSnapshot?.external_adapter_write_intents?.recent_intents?.find(
      (entry) => entry.intent_status === 'blocked_pending_adapter_contract'
    );
    if (!intent) {
      if (snapshotEl) snapshotEl.textContent = 'No external write intent to review.';
      return;
    }
    authFetch(`/api/plugins/control-tower/external-write-intents/${encodeURIComponent(intent.external_write_intent_id)}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'owner',
        reason: 'dashboard reviewed external write readiness; write-through remains disabled',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `External write intent review failed: ${error}`;
      });
  };

  const requestFirstExternalWriteAuthorization = () => {
    const review = latestSnapshot?.external_adapter_write_intent_reviews?.recent_reviews?.find(
      (entry) => entry.review_status === 'blocked'
    );
    if (!review) {
      if (snapshotEl) snapshotEl.textContent = 'No external write review to authorize.';
      return;
    }
    authFetch(`/api/plugins/control-tower/external-write-reviews/${encodeURIComponent(review.external_write_review_id)}/authorization`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'admin',
        reason: 'dashboard requested external write authorization; write-through remains disabled',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `External write authorization request failed: ${error}`;
      });
  };

  const approveFirstExternalWriteAuthorization = () => {
    const authorization = latestSnapshot?.external_adapter_write_authorizations?.recent_authorizations?.find(
      (entry) => entry.status === 'requested'
    );
    if (!authorization) {
      if (snapshotEl) snapshotEl.textContent = 'No external write authorization to approve.';
      return;
    }
    authFetch(`/api/plugins/control-tower/external-write-authorizations/${encodeURIComponent(authorization.external_write_authorization_id)}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        decision: 'approved',
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'admin',
        reason: 'dashboard approved authorization record only; write-through remains disabled',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `External write authorization decision failed: ${error}`;
      });
  };

  const prepareFirstExternalWriteExecutionPreflight = () => {
    const authorization = latestSnapshot?.external_adapter_write_authorizations?.recent_authorizations?.find(
      (entry) => entry.status === 'approved'
    );
    if (!authorization) {
      if (snapshotEl) snapshotEl.textContent = 'No approved external write authorization to preflight.';
      return;
    }
    authFetch(`/api/plugins/control-tower/external-write-authorizations/${encodeURIComponent(authorization.external_write_authorization_id)}/execution-preflight`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'admin',
        reason: 'dashboard prepared external write command digest; executor and write-through remain disabled',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `External write execution preflight failed: ${error}`;
      });
  };


  const executeFirstExternalWritePreflight = () => {
    const preflight = latestSnapshot?.external_adapter_write_execution_preflights?.recent_preflights?.find(
      (entry) => entry.preflight_status === 'blocked_pending_external_write_executor' && ['kanban', 'taskpool'].includes(entry.adapter_name)
    );
    if (!preflight) {
      if (snapshotEl) snapshotEl.textContent = 'No approved adapter external write preflight to execute.';
      return;
    }
    authFetch(`/api/plugins/control-tower/external-write-preflights/${encodeURIComponent(preflight.external_write_preflight_id)}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_command_execution_digest: preflight.command_execution_digest,
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'admin',
        reason: 'dashboard executed approved digest-bound adapter write-through; rollback/reopen is separately digest-bound',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `External write execution failed: ${error}`;
      });
  };

  const rollbackFirstExternalWriteRun = () => {
    const rolledBack = new Set((latestSnapshot?.external_adapter_write_rollback_runs?.recent_runs ?? [])
      .map((entry) => entry.external_write_execution_id));
    const run = latestSnapshot?.external_adapter_write_execution_runs?.recent_runs?.find(
      (entry) => entry.execution_status === 'write_completed' && ['kanban', 'taskpool'].includes(entry.adapter_name) && !rolledBack.has(entry.external_write_execution_id)
    );
    if (!run) {
      if (snapshotEl) snapshotEl.textContent = 'No digest-bound adapter external write run to rollback.';
      return;
    }
    authFetch(`/api/plugins/control-tower/external-write-runs/${encodeURIComponent(run.external_write_execution_id)}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_command_execution_digest: run.command_execution_digest,
        actor_type: 'human',
        actor_id: 'dashboard',
        actor_role: 'admin',
        reason: 'dashboard rolled back digest-bound adapter write-through',
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data?.detail) throw new Error(JSON.stringify(data.detail));
        loadSnapshot();
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `External write rollback failed: ${error}`;
      });
  };

  document.getElementById('control-tower-refresh')?.addEventListener('click', loadSnapshot);
  document
    .getElementById('control-tower-propose-refresh')
    ?.addEventListener('click', proposeRefresh);
  document
    .getElementById('control-tower-approve-first')
    ?.addEventListener('click', () => decideFirstPending('approved'));
  document
    .getElementById('control-tower-reject-first')
    ?.addEventListener('click', () => decideFirstPending('rejected'));
  document
    .getElementById('control-tower-plan-first')
    ?.addEventListener('click', planFirstProposed);
  document
    .getElementById('control-tower-policy-first')
    ?.addEventListener('click', reviewFirstPlanPolicy);
  document
    .getElementById('control-tower-execute-read-only')
    ?.addEventListener('click', executeFirstReadOnlyPlan);
  document
    .getElementById('control-tower-record-evidence')
    ?.addEventListener('click', recordSampleEvidence);
  document
    .getElementById('control-tower-evidence-gate')
    ?.addEventListener('click', evaluateEvidenceGate);
  document
    .getElementById('control-tower-request-closeout')
    ?.addEventListener('click', requestCloseoutAuthorization);
  document
    .getElementById('control-tower-approve-closeout')
    ?.addEventListener('click', approveFirstCloseoutAuthorization);
  document
    .getElementById('control-tower-execute-closeout')
    ?.addEventListener('click', executeFirstCloseoutAuthorization);
  document
    .getElementById('control-tower-prepare-external-intent')
    ?.addEventListener('click', prepareFirstExternalWriteIntent);
  document
    .getElementById('control-tower-review-external-intent')
    ?.addEventListener('click', reviewFirstExternalWriteIntent);
  document
    .getElementById('control-tower-request-external-auth')
    ?.addEventListener('click', requestFirstExternalWriteAuthorization);
  document
    .getElementById('control-tower-approve-external-auth')
    ?.addEventListener('click', approveFirstExternalWriteAuthorization);
  document
    .getElementById('control-tower-prepare-external-preflight')
    ?.addEventListener('click', prepareFirstExternalWriteExecutionPreflight);
  document
    .getElementById('control-tower-execute-external-write')
    ?.addEventListener('click', executeFirstExternalWritePreflight);
  document
    .getElementById('control-tower-rollback-external-write')
    ?.addEventListener('click', rollbackFirstExternalWriteRun);
  loadSnapshot();

  if (window.__HERMES_PLUGINS__) {
    window.__HERMES_PLUGINS__.register('control-tower', () => null);
  }
})();
