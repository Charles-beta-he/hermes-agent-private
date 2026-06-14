# Modular Workflow Kernel Phase 75 ADR: Private Export Artifact Compiler

## Status

Accepted — Phase75 compiles a private/local export artifact manifest without external publication.

## Decision

Add:

```text
scripts/workflow_private_export_artifact_compiler.py
tests/scripts/test_workflow_private_export_artifact_compiler.py
```

The compiler references governance archive, sequence map, and authorization packet evidence; visibility remains `private_local_only`.

## Evidence

```text
status=PRIVATE_EXPORT_ARTIFACT_COMPILER_READY
visibility=private_local_only
private_export_ready=true
publication_apply_enabled=false
external_publication_enabled=false
```

## Boundary

No publication, external send, cron mutation, unattended dispatch, or credential access is performed.
