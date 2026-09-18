# MobileGUI-LTM

Pluggable **long-term memory** for Android / MobileWorld-style **mobile GUI agents**.

MobileGUI-LTM is an SDK, not an agent and not a generic chat-memory product. It sits beside an existing GUI loop so trajectories survive across **pass@k multi-attempt protocols**: the emulator may reset; memory does not.

```text
attempt k  →  write_attempt(task, k, traj, outcome)
attempt k+1 → retrieve(query) → inject(prompt, memories) → act
```

Write **successes and failures**. Retrieve UI facts, subgoal traces, and failure notes. Inject them into system / planner / worker prompts. Swap the JSON backend, encoder, retriever, or injector without forking the agent.

## Install

Python 3.10+.

```bash
git clone https://github.com/Lxr-max/mobilegui-ltm.git
cd mobilegui-ltm
pip install -e .
# optional: tests
pip install -e ".[dev]"
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

# single arm
mobilegui-ltm-demo --ltm on  --k 2 --data-dir ./demo_store
mobilegui-ltm-demo --ltm off --k 2 --data-dir ./demo_store
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

| Kind | Role | MVP |
| --- | --- | --- |
| **UI facts** | Cross-step / cross-app entities (shops, filters, account-looking state) | yes |
| **Subgoal trace** | How far along, where stuck | yes |
| **Failure notes** | Failure mode + next-time avoidance (feeds recovery metrics) | yes |
| **Shortcuts** | Reusable action snippets | phase 2 stub (`ShortcutEncoder` returns `[]`) |

Schema is structured JSON (`MemoryRecord`). Optional `embedding` field is reserved; the default retriever is BM25. **No graph database** in the MVP.

## Plugin points

Swap these without forking the agent:

1. **Backend** — JSON files (default) → SQLite stub → vector DB later  
2. **Encoder** — trajectory → tips / UI facts / failure notes (`TrajectorySummarizer`)  
3. **Retriever** — task / app filters + BM25; `EmbeddingRetriever` is an optional stub  
4. **Injector** — which segment: `system` / `planner` / `worker`

```python
from mobilegui_ltm import MemoryStore
from mobilegui_ltm.store import JsonFileBackend
from mobilegui_ltm.encode import TrajectorySummarizer
from mobilegui_ltm.retrieve import BM25Retriever
from mobilegui_ltm.inject import PromptInjector

store = MemoryStore(
    backend=JsonFileBackend("./.mobilegui_ltm"),
    encoder=TrajectorySummarizer(),
    retriever=BM25Retriever(),
    injector=PromptInjector(placement="prepend"),
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

All tests are offline.

## Package layout

```text
src/mobilegui_ltm/
  api.py schema.py inject.py cli.py
  store/{json,sqlite}.py          # sqlite is a phase-2 stub
  encode/traj_summarizer.py
  retrieve/{keyword,embed}.py     # embed is an optional stub
  adapters/{pass_at_k,android_world,agent_s2,dummy}.py
examples/pass_at_k_demo/
tests/
docs/design.md
```

## Phase 2

Extension points only (not in this MVP): vector retrieval with a real encoder, shortcut mining, integrity / poisoning hooks (CoMemOffset-style signing), and thicker env adapters.

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
