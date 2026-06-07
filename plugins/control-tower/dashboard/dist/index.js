(() => {
  const root = document.createElement('div');
  root.className = 'control-tower-plugin';
  root.innerHTML = `
    <section class="control-tower-card">
      <p class="control-tower-eyebrow">Hermes Control Tower</p>
      <h1>Event-driven workflow control plane</h1>
      <p>
        Phase 1 is read-only: the dashboard plugin is a UI host and projection,
        not a workflow fact source. Durable workflow facts belong to the Control
        Tower Ledger at <code>~/.hermes/control_tower.db</code>.
      </p>
      <pre id="control-tower-snapshot">Loading snapshot…</pre>
    </section>
  `;

  const mount = document.getElementById('root') || document.body;
  mount.appendChild(root);

  fetch('/api/plugins/control-tower/snapshot')
    .then((response) => response.json())
    .then((data) => {
      const el = document.getElementById('control-tower-snapshot');
      if (el) el.textContent = JSON.stringify(data, null, 2);
    })
    .catch((error) => {
      const el = document.getElementById('control-tower-snapshot');
      if (el) el.textContent = `Snapshot load failed: ${error}`;
    });
})();
