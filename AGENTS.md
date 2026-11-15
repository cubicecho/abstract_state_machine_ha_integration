# Project: Abstract State Machine — Home Assistant Integration

A Home Assistant helper for building **state machines** in the UI. The user names the machine, adds their own states, and for each state picks the rules (HA triggers and conditions) that move the machine into it. Each machine is a `select` entity (plus a transition `event` entity), on its own, on a virtual device, or attached to an existing device. One machine per person, device or thing. Example machines (built by the user, not built in): per-person **day type** and **hour type**, **house mode**, a **dishwasher** cycle.

**Status: phase 0 (planning).** Docs and a local preview instance only; no integration code yet. See [`.agents/plan.md`](.agents/plan.md).

## Commands

Preview the UI in a throwaway HA (Docker): `cd dev && docker compose up` → http://localhost:8123. See [`dev/README.md`](dev/README.md).

Tests, types and lint (once code exists; same setup as map-fusion):

```bash
pip install ".[test]" ruff mypy && pip uninstall -y abstract-state-machine-ha-integration
pytest
mypy custom_components/abstract_state_machine/
ruff check custom_components/ tests/
```

## Key Conventions

- **No rule language of our own.** Rules are HA triggers and conditions, edited with `TriggerSelector` / `ConditionSelector`, run with HA's trigger and condition helpers.
- **States are user-defined; rules belong to the state they lead to.** Two kinds: trigger rules ("this happened → enter this state", optional `from` states) and condition rules ("this is true → this state"). For condition rules, the first state top to bottom with a match wins, else the fallback state.
- **`engine.py` and `model.py` stay pure** (no HA imports), so decisions are tested without HA.
- **Every change records a `reason`** (rule name, `manual`, `restored`, `fallback`).
- **Events are built in:** the select's state change, an `event` entity per machine, and a bus event. Automations don't call into the machine to make it work.
- **No presets.** Day type, house mode, etc. are README recipes the user builds.
- **Config lives in `entry.options`** (helper convention); `entry.data` is empty.
- **Machines read each other** (house mode from each person's machines). Guard against loops.

## Agent File Convention

All planning, structure, and API reference docs live in `.agents/`. Always add new files to the reference list below.

## Agent Reference Files

- [`.agents/plan.md`](.agents/plan.md) — Goal, concepts, entities, flows, examples, architecture, phases, open questions
- [`.agents/research.md`](.agents/research.md) — Prior art (similar integrations) and the HA core pieces we reuse
