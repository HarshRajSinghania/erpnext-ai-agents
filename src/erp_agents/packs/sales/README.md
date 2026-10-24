# The sales pack (skeleton)

**Status: an empty skeleton. There is no logic here.** It exists to show where a sales agent would go and to prove that the core finds, validates and switches packs by their manifest. It declares no tools, no approval kinds, no settings and no ERPNext permissions, and it has no `pack.py`, so there is nothing to load. It is off by default and switching it on does nothing.

## What is in this folder

| File | What it is |
|---|---|
| `manifest.yaml` | the manifest: name, version, `status: skeleton`, `enabled_by_default: false`, `depends_on: []` |
| `README.md` | this page |
| `__init__.py` | makes the folder a Python package |

## Rules for turning it into a real pack (quotations, sales orders and customers)

1. A pack depends only on the core (`erp_agents.core`), never on another pack. If this area needs stock data, ask for it with a tool call through the core's tool registry; do not import `erp_agents.packs.stock_procurement`. `tests/test_pack_boundaries.py` fails if a pack imports another.
2. Declare everything in `manifest.yaml`: the tools (name, role, effect: `read` or `prepare`), the approval kinds and what answering them may create in the ERP, the settings sections, the ERPNext roles and permissions, and what an agent may send to a language model. Change `status` to `active`. The core checks the manifest against what `pack.py` registers when it starts, and refuses to start if they differ.
3. No tool orders, approves, pays or changes a setting. A tool reads, or prepares an approval request that a person answers. The core checks the role, the order gate and the verification of answers for every pack; a pack cannot opt out.
4. Add `pack.py` with a `PACK` object (a subclass of `erp_agents.core.packs.Pack`) and its tests next to it in `tests/`.
5. Everything a person is told is plain English for a small-business owner, built in one message module.

The full guide, with the hooks and an example, is "How to add a pack" in [docs/DEVELOPING.md](../../../../docs/DEVELOPING.md).
