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

# Attempt 2: environment reset is fine; LTM is still here.
memories = store.retrieve(
    "red sneakers size 9 ShopX",
    task_id="shop-red-sneakers-size9",
    app_ids=["com.example.shopping"],
    k=8,
)
prompt = store.inject("Open Shopping and add red sneakers size 9 from ShopX.", memories)
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

## LTM on/off ablation

Citation-style comparison (success rate / recovery after failure) on a dummy shopping task. Offline, no API keys.

```bash
# both arms, ASCII table
mobilegui-ltm-demo --ablate --k 2

# single arm (BM25 default — same as CI demo)
mobilegui-ltm-demo --ltm on  --k 2 --data-dir ./demo_store
mobilegui-ltm-demo --ltm off --k 2 --data-dir ./demo_store

# optional: hybrid ranking with local hashing vectors (no downloads)
mobilegui-ltm-demo --ltm on --retriever hybrid --k 2
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

## Memory kinds

| Kind | Role |
| --- | --- |
| **UI facts** | Cross-step / cross-app entities (shops, filters, account-looking state) |
| **Subgoal trace** | How far along, where stuck |
| **Failure notes** | Failure mode + next-time avoidance (feeds recovery metrics) |
| **Shortcuts** | Reusable action sequences mined from **successful** (or clean high-progress partial) trajectories: task/app/subgoal tags, action list, optional preconditions |

Successful `write_attempt` calls persist an episode-level shortcut plus small skill windows (filter / cart / search / …). Failures do not become shortcuts. Disable with `TrajectorySummarizer(emit_shortcuts=False)`.

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

Embeddings are written on `write_attempt` and stored in the JSON memory file. A sidecar `{root}/{agent}.vectors.json` records `embedder` name, `dim`, and `{id: vector}` for inspection. After changing embedders, rebuild:

```python
store = create_store("./.mobilegui_ltm", retriever="hybrid")
n = store.rebuild_index()  # re-embed all records in this namespace
```

`export_session` / `import_session` carry `embedding` fields. If you import into a hybrid store and vectors are missing, they are stamped with the current embedder.

## Plugin points

Swap these without forking the agent:

1. **Backend** — JSON files (default) → SQLite stub  
2. **Encoder** — trajectory → UI facts / subgoals / failure notes / shortcuts  
3. **Retriever** — BM25, hybrid, or vector (`HybridRetriever` / `EmbeddingRetriever`)  
4. **Injector** — which segment: `system` / `planner` / `worker`  
5. **Embedder** (optional) — hashing / fake / sentence-transformers

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

- `mobilegui_ltm.adapters.pass_at_k` — `PassAtKRunner`, `run_ltm_ablation`
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
  api.py schema.py inject.py cli.py plugins.py
  store/{json,sqlite}.py
  encode/{traj_summarizer,shortcuts}.py
  retrieve/{keyword,embed,embedder,index}.py
  adapters/{pass_at_k,android_world,agent_s2,dummy}.py
examples/pass_at_k_demo/
tests/
docs/design.md
```

## Later work

Integrity / poisoning hooks (CoMemOffset-style signing) and thicker env adapters.

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
