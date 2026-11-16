# Project: Abstract State Machine — Home Assistant Integration

A Home Assistant integration (`integration_type: hub`, so it lives under Integrations, not Helpers) for building **state machines** in the UI. The user names each machine, adds their own states, and picks for each state the rules, HA triggers and conditions, that move the machine into it. Each machine is a `select` entity. Example machines, built by the user, not built in: per-person **day type** and **hour type**, **house mode**, a **dishwasher** cycle.

**Status: phase 3 done (0.4.0).** One entry per person or thing, its machines in the entry's options (no subentries); trigger and condition rules, holds, flip limit, loop warning; a `calendar_event` condition; README recipes. No transition events (for now). Next: phase 4 (extras). See [`.agents/plan.md`](.agents/plan.md).

## Commands

```bash
# Test environment (uv on this machine: keep TMPDIR on the main disk, /tmp is small)
TMPDIR=$PWD/.uvtmp uv venv -p 3.14 .venv
TMPDIR=$PWD/.uvtmp uv pip install -p .venv/bin/python pytest pytest-asyncio pytest-homeassistant-custom-component ruff mypy
# (CI: pip install ".[test]" ruff mypy && pip uninstall -y abstract-state-machine-ha-integration.
#  Not editable: HA's custom-component loader can't read editable installs.)

.venv/bin/python -m pytest
.venv/bin/mypy custom_components/abstract_state_machine/
.venv/bin/ruff check custom_components/ tests/
```

Preview the UI in a throwaway HA (Docker, local daemon): `cd dev && docker --context default compose up -d` → http://localhost:8123. After a Python change, `docker --context default compose restart`. See [`dev/README.md`](dev/README.md).

## Layout

```
custom_components/abstract_state_machine/
  __init__.py      # setup: the entry's Thing (device + MachineRuntimes); drops removed machines and an unused virtual device
  const.py         # DOMAIN, placements, option keys, attribute names
  model.py         # MachineDef/StateDef/RuleDef, a thing's machines, validation, edits (rules follow renames), loops (pure)
  engine.py        # Status (with holds), compute/apply_computed, fire_trigger, set_manual (pure)
  runtime.py       # MachineRuntime: HA triggers + condition checkers → engine, flip limit, timers
  data.py          # Thing, AsmConfigEntry, unique_id()
  select.py        # the state (RestoreEntity, full status incl. holds as extra data); device_entry; service handlers
  config_flow.py   # config flow: placement + first machine; options flow (Configure): machines, rules, states, settings, name/device
  services.py      # set_state (reason, hold), clear_override (platform entity services)
  condition.py     # condition platform: abstract_state_machine.calendar_event (+ conditions.yaml for the picker)
tests/
  common.py        # machine(), add_thing(), machine_data(): entries with machines, no flows
dev/               # docker compose preview HA + fake inputs
```

## Key Conventions

- **Entry = a person or thing; its machines live in `entry.options`.** `options` = `placement` (virtual / existing / none), `device_id` (existing), `machines` (each with its own `id`); `entry.data` is empty. No subentries: HA 2026.10+ gives a device one config entry and at most one subentry, so machines as subentries left the shared device under "Devices that don't belong to a sub-entry" (0.2.0), and people/things as subentries of one entry added a level (briefly, in 0.3.0 development). Machines are added and edited through the options flow (Configure), which saves rule edits mid-flow with `async_update_entry` and finishes with `async_create_entry(data=options)`.
- **Devices go on entities through `device_entry`.** The virtual device is created in `async_setup_entry` with identifier `(DOMAIN, entry_id)`; an existing device belongs to another integration's entry and is only linked.
- **A machine's unique id is `f"{machine_id}_state"`.** Setup removes registry entries of removed machines, and the virtual device once the entry moved off it (after the platforms set up, so the entities have moved first).
- **Any entry change reloads it** (update listener): machines added, edited or removed, placement, title. HA doesn't abort an open options flow on reload, so the rule editor can save and stay.
- **Schemas use `probatio`** (HA 2026.10+), imported as `vol`.
- **States are user-defined; rules belong to the state they lead to.** Condition rules: first state top to bottom with a match wins, else the fallback state. A manual change or a trigger rule's change holds against them until their answer changes (or `hold` runs out, or `clear_override`).
- **Rules are stored exactly as the automation editor sends them** (plain JSON). `_RawTriggerSelector` / `_RawConditionSelector` show HA's pickers but skip their validation, which would produce unstorable objects; `runtime.async_validate_*` validate a copy at save time and at setup. A rule that fails at setup is logged and skipped, not fatal.
- **Condition rules re-check through HA's condition checkers** (`async_track_changes`, plus a minute tick when `needs_polling`). Re-checks are coalesced into one tracked task (`hass.async_create_task`), so machines that read each other can't recurse and `async_block_till_done` waits for them.
- **Flip limit, not loop prevention:** the flow warns about loops between machines (`model.find_loop`, best effort: no templates), and at runtime each machine allows `max_flips` rule-driven changes per minute, then ignores its rules for the rest of the minute and raises a repair issue.
- **Timers pass the time they fired for** to the engine (`_evaluate(now)`), so tests can move HA's clock.
- **`engine.py` and `model.py` stay pure** (no HA imports). Model edits keep keys they don't know.
- **Every change records a `reason`** (`manual`, a caller's reason, a rule name; `restored` / `initial` on startup).
- **No event entity or bus event (for now):** automations use a state trigger on the select. Removed in 0.3.0 to keep it simple.
- **Our own conditions go through HA's condition platform** (`condition.py` → `async_get_conditions`, `conditions.yaml`, `conditions` in strings). They show in the automation editor's picker, so in rule forms too, but only once the integration is loaded (an entry exists). `calendar_event` caches today's and tomorrow's events (refresh every 15 min and at midnight), checks synchronously, `needs_polling` is True, and calls tracked actions when a refresh changes the events. Checkers hold timers: whoever builds one must `async_unload()` it (the runtime does).
- **No presets.** Day type, house mode, etc. are recipes the user builds.

## Agent File Convention

All planning, structure, and API reference docs live in `.agents/`. Always add new files to the reference list below.

## Agent Reference Files

- [`.agents/plan.md`](.agents/plan.md) — Goal, concepts, entities, flows, examples, architecture, phases, open questions
- [`.agents/research.md`](.agents/research.md) — Prior art (similar integrations) and the HA core pieces we reuse
