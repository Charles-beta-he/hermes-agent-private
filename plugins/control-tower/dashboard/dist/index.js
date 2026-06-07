(() => {
  const root = document.createElement('div');
  root.className = 'control-tower-plugin';
  root.innerHTML = `
    <section class="control-tower-card">
      <p class="control-tower-eyebrow">Hermes Control Tower</p>
      <h1>Event-driven workflow control plane</h1>
      <p>
        Phase 3 is still no-execution: this dashboard can record durable operator
        intent and approval decisions in the Control Tower Ledger, but it cannot
        execute lifecycle actions. Durable workflow facts belong to
        <code>~/.hermes/control_tower.db</code>.
      </p>
      <div class="control-tower-actions">
        <button id="control-tower-refresh" type="button">Refresh snapshot</button>
        <button id="control-tower-propose-refresh" type="button">
          Propose RefreshProjection
        </button>
        <button id="control-tower-approve-first" type="button">
          Approve first pending
        </button>
        <button id="control-tower-reject-first" type="button">
          Reject first pending
        </button>
      </div>
      <div id="control-tower-summary" class="control-tower-summary">Loading…</div>
      <pre id="control-tower-snapshot">Loading snapshot…</pre>
    </section>
  `;

  const mount = document.getElementById('root') || document.body;
  mount.appendChild(root);

  const snapshotEl = document.getElementById('control-tower-snapshot');
  const summaryEl = document.getElementById('control-tower-summary');
  let latestSnapshot = null;

  const renderSummary = (data) => {
    const pending = data?.human_inbox?.pending_count ?? 0;
    const decided = data?.human_inbox?.decided_count ?? 0;
    const proposed = data?.command_registry?.proposed_count ?? 0;
    const ready = data?.workflow_map?.ready?.length ?? 0;
    const blocked = data?.workflow_map?.blocked?.length ?? 0;
    if (summaryEl) {
      summaryEl.innerHTML = `
        <span>Commands proposed: <strong>${proposed}</strong></span>
        <span>Human pending: <strong>${pending}</strong></span>
        <span>Human decided: <strong>${decided}</strong></span>
        <span>Workflow ready: <strong>${ready}</strong></span>
        <span>Blocked gates: <strong>${blocked}</strong></span>
      `;
    }
  };

  const loadSnapshot = () => {
    if (snapshotEl) snapshotEl.textContent = 'Loading snapshot…';
    fetch('/api/plugins/control-tower/snapshot')
      .then((response) => response.json())
      .then((data) => {
        latestSnapshot = data;
        renderSummary(data);
        if (snapshotEl) snapshotEl.textContent = JSON.stringify(data, null, 2);
      })
      .catch((error) => {
        if (snapshotEl) snapshotEl.textContent = `Snapshot load failed: ${error}`;
      });
  };

  const proposeRefresh = () => {
    fetch('/api/plugins/control-tower/commands/propose', {
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
    fetch(`/api/plugins/control-tower/approvals/${approval.approval_id}/decision`, {
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
  loadSnapshot();
})();
