(() => {
  const root = document.createElement('div');
  root.className = 'control-tower-plugin';
  root.innerHTML = `
    <section class="control-tower-card">
      <p class="control-tower-eyebrow">Hermes Control Tower</p>
      <h1>Event-driven workflow control plane</h1>
      <p>
        Phase 2 is proposal-only: this dashboard can record durable operator
        intent in the Control Tower Ledger, but it cannot execute lifecycle
        actions. Durable workflow facts belong to
        <code>~/.hermes/control_tower.db</code>.
      </p>
      <div class="control-tower-actions">
        <button id="control-tower-refresh" type="button">Refresh snapshot</button>
        <button id="control-tower-propose-refresh" type="button">
          Propose RefreshProjection
        </button>
      </div>
      <pre id="control-tower-snapshot">Loading snapshot…</pre>
    </section>
  `;

  const mount = document.getElementById('root') || document.body;
  mount.appendChild(root);

  const snapshotEl = document.getElementById('control-tower-snapshot');

  const loadSnapshot = () => {
    if (snapshotEl) snapshotEl.textContent = 'Loading snapshot…';
    fetch('/api/plugins/control-tower/snapshot')
      .then((response) => response.json())
      .then((data) => {
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

  document.getElementById('control-tower-refresh')?.addEventListener('click', loadSnapshot);
  document
    .getElementById('control-tower-propose-refresh')
    ?.addEventListener('click', proposeRefresh);
  loadSnapshot();
})();
