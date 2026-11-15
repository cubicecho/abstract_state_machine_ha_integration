# Plan: Abstract State Machine MVP

## Goal

A Home Assistant helper that lets you build **state machines** in the UI. You name the machine, add your own states, and for each state choose what moves the machine into it: HA triggers and conditions, the same pickers the automation editor uses. Each machine is a native entity, on its own, on a virtual device, or attached to an existing device. Automations react to its changes like any other entity.

Nothing about the states is built in. Day type, hour type, house mode and the dishwasher below are **examples of machines a user builds**, not presets.

See [research.md](research.md) for what exists already and why this isn't a duplicate.

## Decisions

| Question | Decision | When |
|---|---|---|
| Domain / name | `abstract_state_machine` ("Abstract State Machine"). Repo `cubicecho/abstract_state_machine_ha_integration` | 2026-11-15 |
| States | **User-defined.** No preset state sets. The user adds, names, orders and removes states | 2026-11-15 |
| Rules | **Attached to a state**: "enter this state when …". Rules use HA triggers and conditions (`TriggerSelector` / `ConditionSelector`). No rule language of our own | 2026-11-15 |
| Grouping | **Entry = a person or thing, subentry = a machine.** An entry is a new virtual device ("Alice time"), an existing device (the dishwasher's plug, Alice's phone) or no device; its machines are `machine` subentries, entities on that device. HA 2026.10+ gives a device a single config entry, so one-entry-per-machine couldn't share a device. HA persons have no device, so "attach to Alice" means a virtual "Alice" device or her phone's device | 2026-11-15 |
| One machine = | One subentry. **One machine per person, device or thing**, each made by the user (Alice's day type and Bob's day type are two machines) | 2026-11-15 |
| Presets | **None in the MVP.** Examples live in docs. Maybe later: "duplicate machine" so Bob's day type starts as a copy of Alice's | 2026-11-15 |
| State entity | `select` (options = states). Choosing an option = manual set | 2026-11-15 |
| Events on change | Built in: `state_changed` on the select, one `event` entity per machine, and a bus event. No external hooks | 2026-11-15 |
| Dependencies | None. No `transitions` library: the engine is small and pure | 2026-11-15 |
| Minimum HA | 2026.10 (single-config-entry devices, `probatio` schemas; what pytest-homeassistant-custom-component ships) | 2026-11-15 |

## Concepts

**Machine**: a name, an ordered list of **states**, and a **fallback state** (also the initial state). It lives in an entry, which decides its device (below). Optionally linked to a **person**, which is just a label for now (it shows in attributes).

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

A machine can mix both kinds. **Open:** when a machine has condition rules and a trigger rule fires, does the trigger's state stick until the computed result next changes (like a manual override), or get recomputed straight away? Proposed: it sticks, the same as a manual set.

**Time in a state** needs no special feature. A trigger rule can use a state trigger on the machine's own entity with `for:`, e.g. `clean` for 12 h → `cleared`.

**Manual override**: choosing an option on the select, or calling `set_state`:

- On a machine with only trigger rules, it's simply the new state.
- On a machine with condition rules, it's held until the computed result **changes** (proposed default), until a duration passes (`hold: "02:00:00"`), or until `clear_override`. Attributes show `override: true` and `override_until`.

**Machines read each other.** They're entities, so hour type's rules can say "Alice day type is workday", and house mode can say "every hour type is sleeping". The engine must stop loops (A reads B reads A): refuse them at save time if we can find them, otherwise cap re-evaluation depth at runtime (**open**).

## Entities

Per machine:

| Entity | Notes |
|---|---|
| `select.<name>` | State. Attributes: `previous`, `reason` (rule name, `manual`, `restored`, `fallback`), `changed_at`, `override`, `override_until`, `person` |
| `event.<name>_transition` | One event per change. `event_types` = the states (the event type is the state entered). Attributes `from`, `to`, `reason` |

Bus event `abstract_state_machine_transition`: `{entity_id, machine, from, to, reason, manual}`.

That covers "dishwasher running → clean, send me a notification" three ways (state trigger with from/to, event entity trigger, event trigger). The state trigger is the one to document.

Renaming a state: the select's options change, so automations using the old name break. The options flow warns before a rename.

Later, maybe: device triggers ("Dishwasher entered clean") so the automation editor reads nicely. Optional binary sensors per state ("Alice is sleeping").

## Placement: entry = device

The entry, picked when it's created and changeable from its **Configure** button:

1. **New virtual device**: the entry makes a device with its own name ("Alice time", "House"). All its machines are entities on it.
2. **Existing device**: the machines' entities attach to a device you already have (the dishwasher's smart plug, Alice's phone), the way template helpers do (`entity.device_entry`). We don't join that device's config entry.
3. **No device**: plain helper entities, like an `input_select`.

Moving an entry keeps entity ids and states. Moving off a virtual device deletes it (after the entities have moved, so they aren't removed with it).

## Config and subentry flows

**Entry** (Settings → Devices & services → Add integration → Abstract State Machine): a menu (new virtual device / existing device / no device), then a name or a device. Creating it opens the first machine's flow straight away (as core's Bayesian helper does).

**Machine** (+ Add state machine on the integration page): name, states (a list: type a name, press Enter), optional person; then the fallback state.

**Edit a machine** (its ⋮ → Reconfigure) is a menu:

- **Add, remove or reorder states** (one list; the order is the priority order).
- **Rename a state**: keeps its place, icon, rules and the machine's current state (carried across the reload in `runtime._RENAMES`).
- **Name, fallback and person**.
- **State icons**: the select shows the current state's icon.
- Phase 2: **Rules**: pick a state → list its rules → add / edit / remove. Edit = name, kind (trigger or condition), `from` states, triggers (`TriggerSelector`), conditions (`ConditionSelector`).

**Entry options**: placement.

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
 select.py (state) ── event.py (transitions) ── bus event ── services.py
```

Planned files under `custom_components/abstract_state_machine/`:

| File | Job |
|---|---|
| `model.py` | Dataclasses, parsing from `entry.options`, validation: unique state names, `from` states exist, fallback exists. Pure |
| `engine.py` | Pure decisions: manual set, trigger rule `from` check, first matching condition rule, override expiry, reason. Tested without HA |
| `runtime.py` | One per machine. Sets up triggers and condition listeners, calls the engine, writes state, fires events. (Phase 2) |
| `config_flow.py` | Entry flow, entry options (placement), machine subentry flow and reconfigure menu (above) |
| `select.py`, `event.py` | Entities. `RestoreEntity` on the select |
| `services.py`, `services.yaml` | Above |

The entry's placement is in `entry.options` (so its options flow can edit it); `entry.data` is empty. Each machine is in its subentry's `data` (states, fallback, person; rules from phase 2) and title (its name).

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
2. **Rules.** Per-state rules in the options flow (selectors), `runtime.py`, reasons, overrides, `clear_override`, `signal`, `reevaluate`, startup handling. Preview: the dishwasher driven by the fake power slider.
3. **Composition and calendars.** Loop guard for machines that read each other. Calendar matching ("any current event whose title contains PTO"): a template first, our own condition type if templates are too painful. README recipes for the examples.
4. **Extras.** Duplicate machine, `explain` service, device triggers, per-state binary sensors, maybe a panel or card that draws the machine (states as nodes, rules as edges, current state lit).

## Open questions

1. **Manual override default** on machines with condition rules: hold until the computed result changes (proposed), or until the end of the day?
2. **Trigger rules on a machine with condition rules:** does the trigger's state stick until the computed result changes (proposed), or get recomputed straight away?
3. **Loops between machines:** refuse at save time, or only cap at runtime?
4. **Unavailable inputs:** treat as "doesn't match" (HA default), or freeze the machine while an input is unavailable?
