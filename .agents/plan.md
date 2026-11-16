# Plan: Abstract State Machine MVP

## Goal

A Home Assistant integration that lets you build **state machines** in the UI. You name the machine, add your own states, and for each state choose what moves the machine into it: HA triggers and conditions, the same pickers the automation editor uses. Each machine is a native entity, on its own, on a virtual device, or attached to an existing device. Automations react to its changes like any other entity.

Nothing about the states is built in. Day type, hour type, house mode and the dishwasher below are **examples of machines a user builds**, not presets.

See [research.md](research.md) for what exists already and why this isn't a duplicate.

## Decisions

| Question | Decision | When |
|---|---|---|
| Domain / name | `abstract_state_machine` ("Abstract State Machine"). Repo `cubicecho/abstract_state_machine_ha_integration` | 2026-11-15 |
| States | **User-defined.** No preset state sets. The user adds, names, orders and removes states | 2026-11-15 |
| Rules | **Attached to a state**: "enter this state when …". Rules use HA triggers and conditions (`TriggerSelector` / `ConditionSelector`). No rule language of our own | 2026-11-15 |
| Grouping | **Entry = a person or thing; its machines live in the entry's options (no subentries).** A person or thing is a new virtual device ("Alice"), an existing device (the dishwasher's plug, Alice's phone) or no device; its machines are entities on that device, managed from the entry's Configure (like Magic Areas). HA 2026.10+ gives a device one config entry and at most one subentry, so "entry = device, subentry = machine" left the shared device under "Devices that don't belong to a sub-entry", and "one entry, subentry = person or thing" added a level. Maybe revisit later. HA persons have no device, so "attach to Alice" means a virtual "Alice" device or her phone's device | 2026-11-15 |
| One machine = | One item in its entry's `options.machines`, one `select`. **One machine per person, device or thing**, each made by the user (Alice's day type and Bob's day type are two machines) | 2026-11-15 |
| Integration type | `hub` (Integrations, not Helpers) | 2026-11-15 |
| Presets | **None in the MVP.** Examples live in docs. Maybe later: "duplicate machine" so Bob's day type starts as a copy of Alice's | 2026-11-15 |
| State entity | `select` (options = states). Choosing an option = manual set | 2026-11-15 |
| Events on change | **State triggers on the select only, for now.** The event entity and bus event were dropped in 0.3.0 to keep it simple | 2026-11-15 |
| Dependencies | None. No `transitions` library: the engine is small and pure | 2026-11-15 |
| Manual override | Holds until the condition rules' answer changes; optional `hold` duration; `clear_override` | 2026-11-15 |
| Trigger rule on a machine with condition rules | Holds like a manual change | 2026-11-15 |
| Loops between machines | **Warn** in the flow when a new rule closes a loop; a per-machine **flip limit** (`max_flips` rule-driven changes per minute, default 10) stops it at runtime | 2026-11-15 |
| Unavailable inputs | HA's default: a condition on an unavailable entity is false | 2026-11-15 |
| Minimum HA | 2026.10 (single-config-entry devices, `probatio` schemas; what pytest-homeassistant-custom-component ships) | 2026-11-15 |

## Concepts

**Machine**: a name, an ordered list of **states**, and a **fallback state** (also the initial state). It belongs to a person or thing, which decides its device (below). Optionally linked to a **person**, which is just a label for now (it shows in attributes).

**State**: a name, an optional icon, and its **rules**, the ways to enter it. A state with no rules is entered only by hand or by a service call.

**Rules** come in two kinds. Both belong to the state they lead to:

```yaml
states:
  - name: running
    rules:
      - name: Drawing power        # shown as the reason
        from: [cleared, loaded]    # optional; default = any state
        triggers: [...]            # HA triggers → trigger rule
        conditions: [...]          # optional extra checks
  - name: clean
    rules:
      - name: Power dropped
        from: [running]
        triggers: [{trigger: numeric_state, entity_id: sensor.dw_power, below: 3, for: "00:15:00"}]
```

- **Trigger rule** ("something happened"): when one of its triggers fires, the machine is in one of `from`, and its conditions pass, go to its state.
- **Condition rule** ("this is true now"): conditions only, no triggers. The engine re-checks condition rules whenever an entity they reference changes, and once a minute for time and sun conditions. **The first state, top to bottom, with a matching condition rule wins.** When none match, the machine goes to the fallback state. So the user sets priority by ordering the states.

A machine can mix both kinds. When a machine has condition rules and a trigger rule fires, the trigger's state holds until the condition rules' answer next changes, the same as a manual set.

**Time in a state** needs no special feature. A trigger rule can use a state trigger on the machine's own entity with `for:`, e.g. `clean` for 12 h → `cleared`.

**Manual override**: choosing an option on the select, or calling `set_state`:

- On a machine with only trigger rules, it's simply the new state.
- On a machine with condition rules, it's held until the computed result **changes**, until a duration passes (`hold: "02:00:00"`), or until `clear_override`. Attributes show `override: true`, `override_until` and `rules_state` (what the rules say).

**Machines read each other.** They're entities, so hour type's rules can say "Alice day type is workday", and house mode can say "every hour type is sleeping". Loops (A reads B reads A) get a warning when the rule is saved, and the flip limit stops them at runtime.

## Entities

Per machine, one `select.<device>_<machine>`: the state. Attributes: `previous`, `reason` (rule name, `manual`, `restored`, `fallback`), `changed_at`, `manual`, `person`; with condition rules `rules_state`, `override`, `override_until`; `flip_limited`.

"Dishwasher running → clean, send me a notification" is a state trigger with `from`/`to`. (An event entity and a bus event existed until 0.3.0; they were dropped to keep it simple. They'd add "real transitions only", `manual` filtering and one trigger for every machine, if that's ever wanted.)

Renaming a state: the select's options change, so automations using the old name break. The options flow warns before a rename.

Later, maybe: device triggers ("Dishwasher entered clean") so the automation editor reads nicely. Optional binary sensors per state ("Alice is sleeping").

## Placement: person or thing = device

Each person or thing (entry), picked when it's created and changeable from its Configure → **Rename, or change the device**:

1. **New virtual device**: a device with the entry's name ("Alice", "House"). All its machines are entities on it.
2. **Existing device**: the machines' entities attach to a device you already have (the dishwasher's smart plug, Alice's phone), the way template helpers do (`entity.device_entry`). We don't join that device's config entry.
3. **No device**: just the entities, on no device.

Moving keeps entity ids and states. Moving off a virtual device deletes it (after the entities have moved, so they aren't removed with it).

## Config and options flows

**Entry** (Settings → Devices & services → Add integration → Abstract State Machine, once per person or thing): a menu (new virtual device / existing device / no device), then a name or a device, then its first machine: name, states (a list: type a name, press Enter), optional person; then the fallback state.

**Configure** (the entry's options flow) is a menu: **Edit a state machine** (picks one if there are several), **Add a state machine**, **Remove a state machine** (when there's more than one), **Rename, or change the device**.

**Edit a state machine** is a menu:

- **Add, remove or reorder states** (one list; the order is the priority order).
- **Rename a state**: keeps its place, icon, rules and the machine's current state (carried across the reload in `runtime._RENAMES`).
- **Name, fallback and person**.
- **State icons**: the select shows the current state's icon.
- Phase 2: **Rules**: pick a state → list its rules → add / edit / remove. Edit = name, kind (trigger or condition), `from` states, triggers (`TriggerSelector`), conditions (`ConditionSelector`).


Forms are HA's standard selectors. If the step-by-step menus feel clumsy in the preview, the fallback is a sidebar panel later (like map-fusion's), maybe with a diagram of the machine.

## Examples (docs, not code)

These go in the README as "how to build it" recipes. The user builds each one, per person where it applies.

**Day type** (one per person). States top to bottom, so priority runs downwards: `vacation` (condition: the person's calendar has an all-day PTO event), `holiday` (condition: holiday calendar on), `workday` (condition: `workday` sensor on), `weekend` (fallback, no rules).

**Hour type** (one per person). `sleeping` (sleep schedule on, or phone charging at night at home), `working` (day type is `workday` and work schedule on, or in the work zone), `commuting`, `waking_up`, `winding_down`, `free` (fallback).

**House mode**. `vacation` (everyone's day type is vacation), `away` (nobody home), `sleeping` (every hour type at home is sleeping), `home` (fallback). Plus a trigger rule into `wake_up_routine` from `sleeping` at the first alarm.

**Dishwasher**, on the smart plug's device. `running` (trigger: power > 10 W for 2 min), `clean` (from `running`, trigger: power < 3 W for 15 min, long because dishwashers pause mid-cycle), `cleared` (from `clean`, trigger: door open for 1 min), `loaded` (from `cleared`, manual or an NFC tag). Notification: an automation on `select.dishwasher` from `running` to `clean`.

## Services

| Service | Fields | Use |
|---|---|---|
| `set_state` | `entity_id`, `state`, `hold` (optional duration) | Manual set or override |
| `clear_override` | `entity_id` | Go back to the computed state |
| `reevaluate` | `entity_id` | Re-check condition rules now (debugging) |
| `signal` | `entity_id`, `name` | Named input that trigger rules can listen for, for automations or NFC tags that just want to poke a machine ("dishwasher_loaded") |

Response-only, later: `explain`, which lists each rule and whether it matches right now.

## Architecture

```
 options flow (TriggerSelector / ConditionSelector)
            │ states, rules, placement  →  entry.options
            ▼
 ┌──────────────────────────┐
 │ model.py                 │  Machine, State, Rule (pure dataclasses, parse + validate)
 │ engine.py                │  pure: (state, rules, event | condition results, override) → new state + reason
 └──────────┬───────────────┘
            ▼
 ┌──────────────────────────┐   HA trigger + condition helpers
 │ runtime.py               │◄─ attach triggers, compile conditions, track referenced entities,
 │                          │   1-min tick, debounce, startup wait, loop guard
 └──────────┬───────────────┘
            ▼
 select.py (state) ── services.py
```

Planned files under `custom_components/abstract_state_machine/`:

| File | Job |
|---|---|
| `model.py` | Dataclasses, parsing from `entry.options`, validation: unique state names, `from` states exist, fallback exists. Pure |
| `engine.py` | Pure decisions: manual set, trigger rule `from` check, first matching condition rule, override expiry, reason. Tested without HA |
| `runtime.py` | One per machine. Sets up triggers and condition listeners, calls the engine, writes state, fires events. (Phase 2) |
| `config_flow.py` | Config flow and the Configure (options) menus (above) |
| `select.py` | The entity. `RestoreEntity` |
| `services.py`, `services.yaml` | Above |

`entry.data` is empty. The entry's title is the person or thing's name, and `options` = `placement`, `device_id` (existing device) and `machines` (each: `id`, `name`, states with rules, fallback, person, max_flips).

**Startup**: hold the restored state until `EVENT_HOMEASSISTANT_STARTED`, then evaluate condition rules. Otherwise unavailable inputs at boot flip day type to the fallback and fire bogus transitions.

**Unavailable inputs**: a condition on an unavailable entity is false (HA's behaviour), so the rule just doesn't match. Optionally, keep the current state while a referenced entity is unavailable (**open**).

## Previewing the UI locally

`dev/` runs a throwaway Home Assistant in Docker with this repo's `custom_components/` mounted, plus fake inputs (power slider, door toggle, people, schedules) to poke at. See `dev/README.md`. It's for clicking through the flows and watching entities change, not for tests: tests use `pytest-homeassistant-custom-component` like the other integrations.

## Phases

0. **Scaffold** ✅ (2026-11-15) — these docs, `dev/` preview instance, git repo.
1. **Machine core, manual only** ✅ (2026-11-15, 0.1.0). Manifest, `model.py`, `engine.py`, `runtime.py`, entry flow (virtual / existing / no device) → first machine, machine subentry flow + reconfigure menu (states, rename, name/fallback/person, icons), entry options (placement), `select` + `event` entities, bus event, `set_state` (with `reason`), restore, CI. 44 tests. Differences from the plan:
   - **Entry = device, subentry = machine** (was: entry = machine). HA 2026.10 devices belong to one config entry, so per-machine entries couldn't share a device.
   - No `clear_override` / `hold` yet: nothing to override until rules exist.
   - Restoring never fires events. A restored state that was removed from the machine falls back.
   - A machine named like its device isn't doubled: "Dishwasher" on device "Dishwasher" is `select.dishwasher`.
2. **Rules** ✅ (2026-11-15, 0.2.0). Per-state trigger and condition rules in the reconfigure flow (HA's pickers), `runtime.py` (HA trigger and condition helpers → engine), reasons, holds (`hold`, `clear_override`, kept across restarts as extra restore data), startup after HA has started, flip limit + repair issue, loop warning in the flow. 89 tests. Differences from the plan:
   - No `signal` or `reevaluate` services. Automations can already call `set_state`, and condition rules re-check on their own; add them if a real use shows up.
   - Rule changes save straight away and the flow stays on the rule list ("Done" closes it), so several rules can be added in one go.
   - Labels in the rule list (Add / Edit / Delete …) are English only: they're built per rule, so they can't come from translations.
   - Condition rules re-check via HA 2026.10's condition checkers (`async_track_changes` / `needs_polling`), not by extracting entity ids.
   - **Restructured in 0.3.0** after looking at the integration page: `integration_type: hub` (not a helper); entry = person or thing with its machines in its options, no subentries (so no "Devices that don't belong to a sub-entry"); machines managed from Configure. No event entity or bus event. 95 tests.
3. **Composition and calendars** ✅ (2026-11-15, 0.4.0). Our own condition type `abstract_state_machine.calendar_event` (calendars as target; when now / today / tomorrow; title contains, optionally the description too; any / all-day / timed), via HA's condition platform, so it's in the condition picker for rules and automations. README recipes: day type, hour type, house mode, dishwasher. A test of house mode reading other entries' machines with a holding trigger rule. 104 tests. Differences from the plan:
   - No template step: templates can't list calendar events, so the condition type came first.
   - Composition needed no code: machines are entities, so rules read them like anything else.
4. **Extras.** Duplicate machine, `explain` service, device triggers, per-state binary sensors, maybe a panel or card that draws the machine (states as nodes, rules as edges, current state lit).

## Open questions

None right now.
