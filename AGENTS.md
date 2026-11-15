# Project: Abstract State Machine — Home Assistant Integration

A Home Assistant helper for building **state machines** in the UI. The user names each machine, adds their own states, and picks for each state the rules, HA triggers and conditions, that move the machine into it. Each machine is a `select` entity plus a transition `event` entity. Example machines, built by the user, not built in: per-person **day type** and **hour type**, **house mode**, a **dishwasher** cycle.

**Status: phase 2 done (0.2.0).** Machines, devices, trigger and condition rules, holds, flip limit, loop warning. Next: phase 3 (composition and calendars, README recipes). See [`.agents/plan.md`](.agents/plan.md).

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
  __init__.py      # setup: one AsmData per entry (device + MachineRuntime per subentry); services registered in async_setup
  const.py         # DOMAIN, placements, subentry type, attribute names
  model.py         # MachineDef/StateDef/RuleDef, validation, edits (rules follow renames), loop finding (pure)
  engine.py        # Status (with holds), compute/apply_computed, fire_trigger, set_manual (pure)
  runtime.py       # MachineRuntime: HA triggers + condition checkers → engine, flip limit, timers, bus event
  data.py          # AsmData, AsmConfigEntry
  entity.py        # MachineEntity: device_entry, unique ids, naming
  select.py        # the state (RestoreEntity, full status incl. holds as extra data); service handlers
  event.py         # transitions
  config_flow.py   # entry flow (placement menu) → first machine; options (placement); machine subentry flow: rules, states, settings
  services.py      # set_state (reason, hold), clear_override (platform entity services)
tests/
  common.py        # machine(), add_entry(): entries with subentries, no flows
dev/               # docker compose preview HA + fake inputs
```

## Key Conventions

- **Entry = a person or thing; subentry = a machine.** An entry is a new virtual device, an existing device, or no device (`options.placement`). Its machines are `machine` subentries. HA 2026.10+ gives a device a single config entry, so this is the only way several machines share a device.
- **Devices go on entities through `device_entry`, never `device_info`.** `device_info` would make one device per subentry, and an existing device belongs to another integration. The virtual device is created in `async_setup_entry` with the entry (no subentry).
- **Any entry change reloads it** (update listener): new machine, edited machine, placement, title. So reconfigure flows use `async_update_and_abort`, not `..._reload_and_abort`.
- **Schemas use `probatio`** (HA 2026.10+), imported as `vol`.
- **States are user-defined; rules belong to the state they lead to.** Condition rules: first state top to bottom with a match wins, else the fallback state. A manual change or a trigger rule's change holds against them until their answer changes (or `hold` runs out, or `clear_override`).
- **Rules are stored exactly as the automation editor sends them** (plain JSON). `_RawTriggerSelector` / `_RawConditionSelector` show HA's pickers but skip their validation, which would produce unstorable objects; `runtime.async_validate_*` validate a copy at save time and at setup. A rule that fails at setup is logged and skipped, not fatal.
- **Condition rules re-check through HA's condition checkers** (`async_track_changes`, plus a minute tick when `needs_polling`). Re-checks are coalesced into one tracked task (`hass.async_create_task`), so machines that read each other can't recurse and `async_block_till_done` waits for them.
- **Flip limit, not loop prevention:** the flow warns about loops between machines (`model.find_loop`, best effort: no templates), and at runtime each machine allows `max_flips` rule-driven changes per minute, then ignores its rules for the rest of the minute and raises a repair issue.
- **Timers pass the time they fired for** to the engine (`_evaluate(now)`), so tests can move HA's clock.
- **`engine.py` and `model.py` stay pure** (no HA imports). Model edits keep keys they don't know.
- **Every change records a `reason`** (`manual`, a caller's reason, later a rule name; `restored` / `initial` on startup). Startup never fires transition events.
- **Events are built in:** the select's state change, the event entity, and the `abstract_state_machine_transition` bus event.
- **No presets.** Day type, house mode, etc. are recipes the user builds.

## Agent File Convention

All planning, structure, and API reference docs live in `.agents/`. Always add new files to the reference list below.

## Agent Reference Files

- [`.agents/plan.md`](.agents/plan.md) — Goal, concepts, entities, flows, examples, architecture, phases, open questions
- [`.agents/research.md`](.agents/research.md) — Prior art (similar integrations) and the HA core pieces we reuse
