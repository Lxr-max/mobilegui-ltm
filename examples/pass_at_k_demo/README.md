# pass@k demo

Dummy shopping task used to show **cross-attempt persistent memory**.

1. Attempt 1: agent taps ShopY (first hit) → size 9 unavailable → **fail** →
   `write_attempt` stores failure notes + UI facts.
2. Attempt 2: store is **not** cleared; `retrieve` + `inject` surface those
   notes → agent applies a size filter at ShopX → **success**.

With `--ltm off`, attempt 2 repeats attempt 1.

```bash
pip install -e .
python examples/pass_at_k_demo/run_demo.py --ltm ablate --k 2
# or
mobilegui-ltm-demo --ablate --k 2
```

The JSON store is written under `--data-dir` (default `./demo_store`).
