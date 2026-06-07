(() => {
  const root = document.createElement('div');
  root.className = 'control-tower-plugin';
  root.innerHTML = `
    <section class="control-tower-card">
      <p class="control-tower-eyebrow">Hermes Control Tower</p>
      <h1>Event-driven workflow control plane</h1>
      <p>
        Phase 9 prepares digest-bound external adapter write intents after
        bounded evidence-bound closeout inside the Control Tower Ledger. This
        dashboard can record operator intent, human decisions, copy-command
        executor plans, read-only executor runs, evidence gates, closeout
        authorizations, ledger closeouts, and blocked external write intents.
        External taskpool/Kanban adapter write-through remains disabled until
        adapter contract and rollback/reopen semantics are approved.
        Durable workflow facts belong to <code>~/.hermes/control_tower.db</code>.
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
        <span>Writes enabled: <strong>${writesEnabled ? 'yes' : 'no'}</strong></span>
        <span>External closeout writes: <strong>${externalCloseoutWrites ? 'yes' : 'no'}</strong></span>
        <span>Closeout enabled: <strong>${closeoutEnabled ? 'ledger-only' : 'no'}</strong></span>
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
    fetch(`/api/plugins/control-tower/approvals/${encodeURIComponent(approval.approval_id)}/decision`, {
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
    fetch(`/api/plugins/control-tower/commands/${encodeURIComponent(command.command_id)}/executor-plan`, {
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
    fetch(`/api/plugins/control-tower/executor-plans/${encodeURIComponent(plan.plan_id)}/policy-review`, {
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
    fetch(`/api/plugins/control-tower/executor-plans/${encodeURIComponent(review.plan_id)}/execute-read-only`, {
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
    fetch('/api/plugins/control-tower/evidence', {
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
    fetch('/api/plugins/control-tower/evidence-gates/evaluate', {
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
    fetch(`/api/plugins/control-tower/evidence-gates/${encodeURIComponent(review.evidence_review_id)}/closeout-authorization`, {
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
    fetch(`/api/plugins/control-tower/closeout-authorizations/${encodeURIComponent(authorization.closeout_authorization_id)}/decision`, {
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
    fetch(`/api/plugins/control-tower/closeout-authorizations/${encodeURIComponent(authorization.closeout_authorization_id)}/execute`, {
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
    fetch(`/api/plugins/control-tower/closeout-executions/${encodeURIComponent(run.closeout_execution_id)}/external-write-intent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        adapter_name: 'taskpool',
        external_ref: `taskpool:${run.task_id}`,
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
  loadSnapshot();
})();
