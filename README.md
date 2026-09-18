# MobileGUI-LTM

Pluggable **long-term memory** for Android / MobileWorld-style **mobile GUI agents**.

MobileGUI-LTM is an SDK, not an agent and not a generic chat-memory product. It sits beside an existing GUI loop so trajectories survive across **pass@k multi-attempt protocols**: the emulator may reset; memory does not.

```text
attempt k  →  write_attempt(task, k, traj, outcome)
attempt k+1 → retrieve(query) → inject(prompt, memories) → act
```

Write **successes and failures**. Retrieve UI facts, subgoal traces, failure notes, and **shortcuts** (reusable action snippets from successful attempts). Inject them into system / planner / worker prompts. Swap the JSON backend, encoder, retriever, or injector without forking the agent.

## Install

Python 3.10+.

```bash
git clone https://github.com/Lxr-max/mobilegui-ltm.git
cd mobilegui-ltm
pip install -e .
# optional: tests
pip install -e ".[dev]"
# optional: neural embeddings (not required; downloads model weights)
pip install -e ".[embed]"
```

## Quickstart

```python
from mobilegui_ltm import create_store, OutcomeStatus

store = create_store(path="./.mobilegui_ltm", agent_id="agent-a")

# Attempt 1 fails — still written.
store.write_attempt(
    task_id="shop-red-sneakers-size9",
    attempt_k=1,
    traj=[
        {"app_id": "com.example.shopping", "screen": "pdp",
         "action": "tap:ShopY", "observation": "size 9 unavailable at ShopY"},
    ],
    outcome={
        "status": OutcomeStatus.FAILURE,
        "reason": (
            "Size 9 unavailable at ShopY. Avoid ShopY; sold by ShopX. "
            "Apply a size 9 filter before opening a product."
        ),
    },
)

# Mid-episode: same logical key supersedes the previous active fact.
store.remember("Seller is ShopY", kind="ui_fact", key="ui:seller", screen="search")
store.remember("Seller is ShopX", kind="ui_fact", key="ui:seller", screen="search")
store.update("ui:seller", content="Seller is ShopX (size 9 in stock)")
# store.delete("ui:seller")  # soft-delete; hard=True removes the JSON row

# Attempt 2: environment reset is fine; LTM is still here.
memories = store.retrieve(
    "red sneakers size 9 ShopX",
    task_id="shop-red-sneakers-size9",
    app_ids=["com.example.shopping"],
    k=8,
    screen="search",          # hard filter when the record has a screen
    kind_weights={"failure_note": 1.2, "shortcut": 1.1},
)
prompt = store.inject("Open Shopping and add red sneakers size 9 from ShopX.", memories)
print(store.format_worker_shortcuts(memories))
```

`prompt` is wrapped in `<mobilegui_ltm>…</mobilegui_ltm>` so the planner/worker can see failure notes from attempt 1.

## pass@k without resetting memory

Eval stacks often reset the UI between attempts. This SDK's contract is the opposite for **memory**:

| Layer | Reset between attempts? |
| --- | --- |
| Emulator / activity | yes, allowed |
| Agent weights | n/a (not an agent) |
| `MemoryStore` namespace | **no** — do not call `clear()` inside the k-loop |

Use the bundled runner:

```python
from mobilegui_ltm import create_store
from mobilegui_ltm.adapters import DummyGUIAgent, PassAtKRunner, shopping_task

store = create_store("./demo_store/on", agent_id="dummy-shopping")
report = PassAtKRunner(store, DummyGUIAgent()).run(shopping_task(), k=2)
assert report.attempts[0].success is False
assert report.attempts[1].success is True   # recovered via injected failure notes
assert report.recovered_after_failure
```

Cross-machine reproduction:

```python
store.export_session("session.json")
other = create_store("./other_machine", agent_id="dummy-shopping")
other.import_session("session.json")
```

## LTM on/off ablation and kind matrix

Citation-style comparison (success rate / recovery after failure) on a dummy shopping task. Offline, no API keys.

```bash
# both arms, ASCII table
mobilegui-ltm-demo --ablate --k 2

# kind-profile matrix (off, failures-only, shortcuts-only, anchors, full)
mobilegui-ltm-demo --matrix --k 2

# single arm (BM25 default — same as CI demo)
mobilegui-ltm-demo --ltm on  --k 2 --data-dir ./demo_store
mobilegui-ltm-demo --ltm off --k 2 --data-dir ./demo_store
mobilegui-ltm-demo --ltm failures-only --k 2

# optional: hybrid ranking with local hashing vectors (no downloads)
mobilegui-ltm-demo --ltm on --retriever hybrid --k 2

# online diagnostics (dry-run promote/demote/quarantine; no LLM)
mobilegui-ltm-demo --diagnose --k 2
# optional: apply conservative actions (still skips reflector rewrites)
mobilegui-ltm-demo --diagnose --apply-diagnostics --k 2
```

Equivalent from a checkout:

```bash
python examples/pass_at_k_demo/run_demo.py --ltm ablate --k 2
```

Expected pattern:

```text
protocol | pass@1 | pass@2 | recovery_after_failure | solved_at
---------+--------+--------+------------------------+----------
ltm-off  | 0.0    | 0.0    | 0.0                    | None
ltm-on   | 0.0    | 1.0    | 1.0                    | 2
```

Attempt 1 fails in both arms (first search hit is the wrong seller). With LTM on, attempt 2 retrieves failure notes and applies a size filter at ShopX. With LTM off, attempt 2 repeats attempt 1.

On the dummy shopping task, **failures-only** recovers (the agent keys off injected failure notes). **shortcuts-only** does not: attempt 1 is a failure, so no executable shortcut is mined. **anchors** recovers because the failure-point anchor copies the outcome reason (including avoidance text). **full** beats **off** on `pass@k` and `recovery_after_failure`.

```python
from mobilegui_ltm import create_store
from mobilegui_ltm.adapters import DummyGUIAgent, run_ablation_matrix, shopping_task

matrix = run_ablation_matrix(
    shopping_task(),
    k=2,
    store_factory=lambda mode: create_store(f"./demo_store/{mode}", profile=mode),
    agent_factory=DummyGUIAgent,
)
print(matrix.format_table())
```

## Memory kinds

| Kind | Role |
| --- | --- |
| **UI facts** | Cross-step / cross-app entities (shops, filters, account-looking state). Same `logical_key` **supersedes** the previous active value so pass@k does not keep contradictory facts. |
| **Subgoal trace** | How far along, where stuck |
| **Failure notes** | Failure mode + next-time avoidance (feeds recovery metrics) |
| **Shortcuts** | Executable snippets mined from **successful** (or clean high-progress partial) trajectories: `name`, `description`, `preconditions`, `arguments`, `atomic_actions[]`, tags (task / app / subgoal) |
| **Causal anchors** | Cheap “why” records: `content`, `evidence` (app / screen / widget), `depends_on[]`. Retrieve can expand **one hop** along those links. No graph database. |
| **App priors** | Optional LocalRAG catalog hits (installed app / package facts). Default **off**. |

`remember` / `update` / `delete` are the mid-episode CRUD surface (in addition to `write_attempt`). Soft-delete is the default; `delete(..., hard=True)` removes the row. Retrieve and inject skip superseded and deleted records.

Successful `write_attempt` calls persist an episode-level shortcut plus small skill windows (filter / cart / search / …). Failures do not become shortcuts. Older text-only shortcut metadata (`actions` lists) is still dual-read. Disable with `TrajectorySummarizer(emit_shortcuts=False)` or `emit_anchors=False`.

Injection renders shortcuts as callable-style blocks (`call name(args)`, `when:`, `steps:`). `format_shortcut_for_worker` / `store.format_worker_shortcuts` format the same block for a worker prompt.

Schema is structured JSON (`MemoryRecord`). Vectors live on `MemoryRecord.embedding` and in an optional `{agent}.vectors.json` sidecar. Default retriever is BM25. **No graph database**.

## Vector / hybrid retrieval

Default ranking is BM25 (offline, no extra deps). Enable vectors with a pluggable **Embedder**:

| Embedder | When to use | Extra |
| --- | --- | --- |
| `HashingEmbedder` (`embedder="hashing"`) | Default local backend: signed hashing trick, deterministic, no downloads | none |
| `FakeEmbedder` | Unit tests / CI fixture vectors | none |
| `SentenceTransformerEmbedder` (`embedder="sentence-transformers"`) | Neural embeddings | `pip install 'mobilegui-ltm[embed]'` |

```python
from mobilegui_ltm import create_store, FakeEmbedder, HashingEmbedder

# BM25 + hashing vectors (recommended local hybrid)
store = create_store("./.mobilegui_ltm", retriever="hybrid")
# equivalent:
store = create_store("./.mobilegui_ltm", embedder="hashing")

# Vector-only
store = create_store("./.mobilegui_ltm", retriever="vector", embedder=HashingEmbedder(dim=128))

# Tests / CI: no network, no weights
store = create_store("./.mobilegui_ltm", retriever="hybrid", embedder=FakeEmbedder())

# Optional neural backend (downloads weights; not used in CI)
store = create_store("./.mobilegui_ltm", embedder="sentence-transformers")
```

Fusion is `lexical_weight * BM25 + vector_weight * cosine` (defaults 0.5 / 0.5). Tune with `create_store(..., lexical_weight=0.3, vector_weight=0.7)`.

Per-kind weights, hard filters, and 1-hop expand:

```python
store = create_store(
    "./.mobilegui_ltm",
    kind_weights={"failure_note": 1.2, "shortcut": 1.1, "causal_anchor": 1.05},
    expand_hops=1,
    strict_task=False,
    profile="full",
)
hits = store.retrieve(
    "apply size filter at ShopX",
    task_id="shop-red-sneakers-size9",
    app_ids=["com.example.shopping"],
    screen="search",
    state={"app": "com.example.shopping", "screen": "search"},
    k=8,
)
```

- **Hard filters:** `app_ids`, `screen` (records with no screen still match), optional `strict_task`.
- **Soft match:** shortcut `preconditions` vs the query / `state` dict (rank bonus, not a hard drop).
- **Expand:** after the primary top-k, follow `depends_on` / `metadata.links` one hop (id or logical key).

## Named memory blocks

Planner and worker do not have to share one undifferentiated dump. Default views:

| Block | Kinds | Typical target |
| --- | --- | --- |
| `planner_failures` | failure notes, subgoal traces, causal anchors | planner |
| `worker_shortcuts` | shortcuts | worker |
| `ui_state` | UI facts | planner + worker |
| `app_priors` | LocalRAG app catalog | optional blend |

```python
planner = store.inject(prompt, memories, block="planner_failures")
worker = store.inject(state, memories, block="worker_shortcuts", target="worker")
# retrieve + inject one view
prompt = store.inject_block(prompt, "ui_state", "ShopX size 9", task_id="shop-red-sneakers-size9")
store.remember("tip", kind="failure_note", block="planner_failures")
```

The `<mobilegui_ltm>` wrapper is unchanged (pass@k dummy agents still detect it). Block-scoped inject adds a `[block=…]` section inside that wrapper.

## Promotion gate

Shortcuts and causal anchors start as **`candidate`**. Promote to **`stable`** after `promote_after` successes (default 2) or `store.promote(key)`. `store.demote(key)` sends stable → candidate, then candidate → **retired**. Retrieve prefers stable (score bonus); set `include_candidates=False` to hide unproven skills. Retired rows are omitted unless `include_retired=True`.

```python
store = create_store("./.mobilegui_ltm", promote_after=2, include_candidates=True)
store.promote("shortcut:filter_cart")
hits = store.retrieve("apply size filter", include_candidates=False)
```

UI facts and failure notes stay `stable` (they are observations, not skills).

## LocalRAG (optional, default off)

Pluggable installed-app / package-catalog priors. **No ADB and no device** at import time. Pass a fake catalog in tests; register rows from an env adapter when you already know the package list.

```python
store = create_store(
    "./.mobilegui_ltm",
    local_rag=[{"app_id": "com.example.shopping", "name": "Shopping", "capabilities": ["cart"]}],
    blend_local_rag=True,
)
# or later:
from mobilegui_ltm.adapters import AndroidWorldAdapter
AndroidWorldAdapter(store).register_app_prior("com.example.shopping", name="Shopping")
hits = store.retrieve("shopping cart", blend_local_rag=True)
```

Catalog hits are ephemeral `app_prior` records blended **after** LTM ranking (they are not written unless you `remember` them yourself).

## Integrity research hooks

Optional retrieve-time tamper detection for poisoning experiments. **Not a cryptography product**: SHA-256 over canonical record bytes, plus optional HMAC (`hmac_key=` or `MOBILEGUI_LTM_HMAC_KEY`). Default off.

```python
store = create_store("./.mobilegui_ltm", integrity=True, hmac_key="dev-only")
rec = store.remember("Avoid ShopY", key="ui:tip")
store.poison(rec.id, content="tap ShopY always")  # hash not refreshed
assert store.retrieve("ShopY", k=5) == []         # dropped
```

`store.poison` / `tamper(record)` are fixtures for eval harnesses.

## OnlineDiagnostics (optional)

SDK-only runtime scoring of **memory usefulness** after each episode. No LLM and no API keys in the default path. Enable with `create_store(..., diagnostics=True)` or pass an `OnlineDiagnostics` instance (optional `EpisodeReflector` / `OutcomeProvider` plugins).

After `write_attempt` (and therefore after each `PassAtKRunner` attempt), the store builds an `EpisodeTrace` and returns a `DiagnosticReport`:

| Signal | Meaning |
| --- | --- |
| **Attribution** | Retrieved rows scored `helped` / `hurt` / `unused`. A success whose retrieved shortcut aligns with the trajectory counts as helped; a failure after injecting a shortcut counts as hurt. |
| **Failure class** | Heuristic from `outcome.reason` / trajectory: `state_loss`, `misbinding`, `context_drift`, `unverified_progress`, `interruption`, `unknown`. |
| **Recovery delta** | Previous attempt failed and this one succeeded — extra promote signal for retrieved shortcuts/anchors. |
| **Auditor** | `EvalAuditor.scan(store)` flags polarity contradictions, integrity mismatches, and stale candidates, then **proposes quarantine**. |

**Auto vs human-in-the-loop**

| Action | Default |
| --- | --- |
| Propose promote / demote / quarantine | automatic (report.actions) |
| Apply those proposals | **dry-run** unless `apply(dry_run=False)` or CLI `--apply-diagnostics` |
| Tip / shortcut rewrites from `EpisodeReflector` | plugin only (`NullEpisodeReflector` by default); **not applied** unless `apply(..., include_reflector=True)` |
| Hard-delete on quarantine | never — metadata flag only; retrieve skips quarantined rows unless `include_quarantined=True` |

```python
from mobilegui_ltm import create_store, OnlineDiagnostics, NullEpisodeReflector

store = create_store("./.mobilegui_ltm", diagnostics=True)
# … pass@k loop …
print(store.diagnostics.format_history())          # ASCII summary
store.diagnostics.apply(dry_run=True)              # preview
store.quarantine("ui:bad-tip", reason="contradiction")
hits = store.retrieve("ShopY", include_quarantined=False)  # default: hidden
```

`OutcomeProvider` is a stub for a later AndroidWorld-style verifier (`NullOutcomeProvider` / `CallableOutcomeProvider`). Bind an LLM reflector later if you want rewrite proposals; tests and CI stay offline.

## Plugin points
Embeddings are written on `write_attempt` and stored in the JSON memory file. A sidecar `{root}/{agent}.vectors.json` records `embedder` name, `dim`, and `{id: vector}` for inspection. After changing embedders, rebuild:

```python
store = create_store("./.mobilegui_ltm", retriever="hybrid")
n = store.rebuild_index()  # re-embed all records in this namespace
```

`export_session` / `import_session` carry `embedding` fields. If you import into a hybrid store and vectors are missing, they are stamped with the current embedder.

## Plugin points

Swap these without forking the agent:

1. **Backend** — JSON files (default) → SQLite stub  
2. **Encoder** — trajectory → UI facts / subgoals / failure notes / shortcuts / causal anchors  
3. **Retriever** — BM25, hybrid, or vector (`HybridRetriever` / `EmbeddingRetriever`) with kind weights  
4. **Injector** — which segment: `system` / `planner` / `worker`; optional named **block**  
5. **Embedder** (optional) — hashing / fake / sentence-transformers  
6. **LocalRAG** (optional, default off) — installed-app catalog priors  
7. **IntegrityGuard** (optional, default off) — hash / HMAC research hooks
8. **OnlineDiagnostics** (optional, default off) — episode attribution + conservative actions
9. **EpisodeReflector / OutcomeProvider** (optional) — rewrite / verifier plugins; Null by default

```python
from mobilegui_ltm import MemoryStore, HashingEmbedder
from mobilegui_ltm.store import JsonFileBackend
from mobilegui_ltm.encode import TrajectorySummarizer
from mobilegui_ltm.retrieve import HybridRetriever
from mobilegui_ltm.inject import PromptInjector

store = MemoryStore(
    backend=JsonFileBackend("./.mobilegui_ltm"),
    encoder=TrajectorySummarizer(),
    retriever=HybridRetriever(HashingEmbedder(), lexical_weight=0.5, vector_weight=0.5),
    injector=PromptInjector(placement="prepend"),
    embedder=HashingEmbedder(),
    agent_id="agent-a",
)
worker_view = store.namespace("worker-B")  # isolated namespace, same files root
```

`create_store(..., enabled=False)` turns retrieve/write/inject into no-ops for ablation.

## Adapters

- `mobilegui_ltm.adapters.pass_at_k` — `PassAtKRunner`, `run_ltm_ablation`, `run_ablation_matrix`
- `mobilegui_ltm.adapters.dummy` — rule-based shopping agent used by the demo
- `mobilegui_ltm.adapters.agent_s2` — planner/worker split inject (reference)
- `mobilegui_ltm.adapters.android_world` — thin hook for AndroidWorld / MobileWorld-style envs (no extra dependency)

## Tests

```bash
pip install -e ".[dev]"
pytest
```

All tests are offline (FakeEmbedder / hashing; no model downloads).

## Package layout

```text
src/mobilegui_ltm/
  api.py schema.py inject.py cli.py plugins.py profiles.py
  blocks.py localrag.py integrity.py
  diagnostics/{schema,online,attribution,failure_taxonomy,outcome,auditor,reflector}.py
  store/{json,sqlite}.py
  encode/{traj_summarizer,shortcuts,anchors}.py
  retrieve/{keyword,embed,embedder,index,scoring}.py
  adapters/{pass_at_k,android_world,agent_s2,dummy}.py
examples/pass_at_k_demo/
tests/
docs/design.md
```

## Later work

Thicker env adapters, optional SQLite / external vector DB backends, and a real LLM client behind `EpisodeReflector` (still a plugin — not the default).

## Citation

```bibtex
@software{mobilegui_ltm,
  title  = {MobileGUI-LTM: Pluggable Long-Term Memory for Mobile GUI Agents},
  year   = {2026},
  url    = {https://github.com/Lxr-max/mobilegui-ltm}
}
```

## License

MIT — see [LICENSE](LICENSE).
