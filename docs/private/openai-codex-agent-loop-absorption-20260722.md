# OpenAI Codex Agent Loop Absorption for Hermes

```yaml
schema: hermes_reference_review_v1
id: openai-codex-agent-loop-20260722
source_media: https://openai.com/index/unrolling-the-codex-agent-loop/
source_repo: https://github.com/openai/codex
project: hermes-agent
status: absorbed-class-pattern-runtime-audited
risk: D2
review_date: 2026-07-22
```

## Goal

Absorb the durable engineering contracts from OpenAI's “Unrolling the Codex agent loop” into Hermes without collapsing Hermes's control-plane scope into a coding-only harness. Resolve exactly how GPT image requests traverse Hermes `image_generate` and provider-native image generation.

## Non-goals

- Do not copy Codex implementation wholesale.
- Do not grant a coding worker lifecycle, deployment, external-write, or notification authority.
- Do not force `image_generate` for educational or analytical mentions of image generation.
- Do not change runtime behavior without a failing contract and evidence that the current behavior violates it.
- No push, deploy, external dispatch, credential entry, or production mutation.

## Source facts

1. The Codex inner loop distinguishes a user turn from repeated model/tool iterations.
2. Model behavior is a function of the harness, tools, instructions, and context policy, not the model alone.
3. Exact-prefix stability affects prompt-cache reuse; tool schemas and ordering are part of that prefix.
4. Codex shell sandboxing does not automatically govern MCP tools.
5. Provider-native `/responses/compact` can preserve model-specific state that a portable text summary cannot.

Canonical class-level extraction:

- `~/.hermes/skills/autonomous-ai-agents/references/codex-agent-loop-vs-control-plane.md`

## Current Hermes evidence

### Already aligned

- `agent/transports/codex.py::_content_cache_key` content-addresses the stable instructions + sorted tool schemas instead of volatile per-fire session IDs.
- `agent/transports/codex.py::ResponsesApiTransport.build_kwargs` sends the Responses API tools with `tool_choice="auto"` and enables parallel tool calls.
- `agent/context_compressor.py` preserves tool-call/result atomicity, supports deterministic degraded reconstruction, and aborts on known model-capability/auth/network failure categories instead of silently discarding all middle history.
- Hermes already separates inner-loop execution from outer session, memory, skill, delegation, cron, gateway, taskpool, and evidence controls.

### Remaining risks

- Generic summary failures may still take a deterministic degraded compression path unless `compression.abort_on_summary_failure` is enabled. This is explicit and observable, not silent, but needs task-level policy rather than a universal forced default.
- Multi-provider fallback can break prefix-cache locality and can change compaction/tool semantics.
- Broad tool and MCP surfaces require per-tool sandbox/approval ownership; host-level sandbox assumptions must not be projected onto remote tools.
- Main-loop tool selection remains probabilistic under `tool_choice="auto"`; concrete artifact requests rely on the model following the tool contract and Hermes execution policy.

## GPT image-generation routing contract

There are two tool-selection layers:

```text
concrete image request
  -> main GPT/Hermes loop sees `image_generate`
     (`tool_choice="auto"`; model selects, not globally forced)
  -> Hermes dispatches `image_generate` to configured image provider
  -> provider generates/edits the image
```

If the configured provider is Hermes `openai-codex`, a second nested Responses request is constructed by:

- `plugins/image_gen/openai-codex/__init__.py::_build_responses_payload`

That request declares OpenAI's native `{"type": "image_generation"}` tool and forces it with:

```json
{
  "type": "allowed_tools",
  "mode": "required",
  "tools": [{"type": "image_generation"}]
}
```

Therefore:

- GPT as the main chat model does not by itself imply OpenAI image generation.
- The main Hermes call normally chooses `image_generate` for an actual image artifact request, but its transport uses automatic tool choice rather than a hardcoded image-intent router.
- Once `image_generate` is selected, dispatch to the configured backend is deterministic.
- Only the `openai-codex` image backend's nested native `image_generation` call is explicitly required/forced.
- In the current session, the exposed tool reports FAL/FLUX as the active image backend, so an image request here would not default to OpenAI's native Image Generator merely because the main model is GPT.

## Decisions

1. Keep Hermes as the outer control plane and use Codex-style runtimes as constrained workers/executors.
2. Preserve `tool_choice="auto"` in the general main loop; global forcing would misroute discussion, prompt-writing, comparison, and analysis requests.
3. Preserve deterministic forcing inside a provider-specific image generation request, where intent is already resolved.
4. Do not flip compression policy globally in this absorption pass: current code has explicit degraded reconstruction and category-specific fail-closed behavior; a default flip needs workload evidence and dedicated regression tests.
5. Absorb the architecture and routing contracts into the class skill; avoid a speculative runtime rewrite where current code already implements the durable mechanisms.

## Negative cases

- “Explain how image generation works” must not necessarily create an image.
- “Write me a prompt for an image model” must not necessarily invoke `image_generate` unless the user also asks for the artifact.
- Missing/disabled `image_generate` must not be described as successful image generation.
- A GPT main model with a FAL image provider must not be reported as OpenAI native image generation.
- An MCP tool must not be described as protected by the local terminal sandbox without tool-specific evidence.
- A compression summary failure must not be reported as lossless compaction.

## Acceptance and evidence

- [x] Canonical article and source repository resolved.
- [x] Turn/iteration, model/harness, cache, compaction, and sandbox boundaries extracted.
- [x] Current Hermes transport and compressor paths inspected.
- [x] Main-loop automatic image-tool selection separated from provider-native forced image generation.
- [x] Class-level reusable reference created and linked from the autonomous-agent umbrella skill.
- [x] Independent adversarial review requested. Claude review was unavailable because its session quota was exhausted; a degraded isolated-context review is pending and no runtime mutation depends on it.
- [x] Targeted tests for Codex cache-key behavior and OpenAI-Codex image-provider request shape pass on the current tree: `83 passed in 1.16s`.

## Fresh verification evidence

```text
main Responses transport:
{'tool_choice': 'auto', 'tools': ['image_generate'], 'cache_key_prefix': 'pck_'}

openai-codex image provider:
{'tool_choice': {'type': 'allowed_tools', 'mode': 'required',
                 'tools': [{'type': 'image_generation'}]},
 'native_tool': 'image_generation', 'native_model': 'gpt-image-2'}
```

The first attempted test command used the system Python environment and failed because `pytest` was not on `PATH`; verification was rerun with the project `.venv/bin/python` and passed.

## Evidence commands

```bash
pytest -q tests/agent/test_codex_transport.py tests/plugins/image_gen/test_openai_codex_provider.py

git diff -- \
  skills/autonomous-ai-agents/SKILL.md \
  skills/autonomous-ai-agents/references/codex-agent-loop-vs-control-plane.md
```

## Converge boundary

This absorption is complete only at class-pattern and audited-current-runtime level. It does not claim a new runtime feature, universal cache-hit proof, lossless compression, UI acceptance, external deployment, or production rollout.
