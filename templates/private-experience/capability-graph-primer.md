# Capability graph primer

Treat workflows, modules, agents, gates, adapters, and templates as
CapabilityNodes.

A reusable node should declare:

- responsibility;
- consumes;
- produces;
- preconditions;
- side effects;
- closure states;
- evidence gates;
- cannot-prove list.

An agent is a governed wrapper around capability nodes, not just a prompt name.
It needs policy, authority, outputs, observability, and cannot-do boundaries.

Default side-effect posture:

- local read/write can be scoped;
- external write, publish/deploy/push, destructive actions, credentials, and
  broad fanout remain disabled until exact target, rollback/escape, receipt,
  and readback are configured.
