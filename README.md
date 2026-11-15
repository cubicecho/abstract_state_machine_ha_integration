# Abstract State Machine

A Home Assistant helper for your own **state machines**: "Alice's day type" (workday / weekend / holiday), "the dishwasher" (cleared / loaded / running / clean), "house mode", whatever you need. You name the states. Each machine is an entity whose state you can use in automations, dashboards and other machines.

**Status: 0.1.0, manual only.** You can build machines, put them on devices and change their state by hand or from automations. Rules that move a machine by themselves (HA triggers and conditions, e.g. "power below 3 W for 15 min → clean") come next. See [`.agents/plan.md`](.agents/plan.md).

## Install

HACS → Integrations → ⋮ → Custom repositories → add this repository (category: Integration) → install **Abstract State Machine** → restart Home Assistant. Needs HA 2026.10 or newer.

## Set up

Settings → Devices & services → **Add integration** → Abstract State Machine. First pick where the machines go:

- **A new virtual device**, e.g. "Alice time" for Alice's day type and hour type, or "House".
- **A device I already have**, e.g. the dishwasher's smart plug, or Alice's phone. HA people don't have devices, but their phones do.
- **No device**: plain helper entities.

Then add the first machine: a name, its states (type one, press Enter, type the next), an optional person, and the **fallback state** (where it starts). Add more machines to the same device with **+ Add state machine** on the integration page. Edit a machine (states, renames, fallback, icons) from its ⋮ menu, and move a device's machines from **Configure**.

## What you get

Per machine, on the chosen device:

| Entity | What it does |
|---|---|
| `select.<device>_<machine>` (e.g. `select.alice_time_day_type`) | The current state. Pick an option to set it by hand. Attributes: `previous`, `reason`, `changed_at`, `manual`, `person` |
| `event.<device>_<machine>_transition` | Fires on every change. The event type is the state entered; attributes `from`, `to`, `reason` |

Every change also fires an `abstract_state_machine_transition` event on the bus with `entity_id`, `machine`, `from`, `to`, `reason` and `manual`.

### Notify when the dishwasher is done

An ordinary state trigger works, because the machine is a normal entity:

```yaml
triggers:
  - trigger: state
    entity_id: select.dishwasher_mode
    from: running
    to: clean
actions:
  - action: notify.mobile_app_alices_phone
    data:
      message: Dishwasher's done
```

### Set a state from an automation, script or NFC tag

```yaml
action: abstract_state_machine.set_state
target:
  entity_id: select.dishwasher_mode
data:
  state: loaded
  reason: NFC tag by the sink   # optional; shows in the reason attribute
```

`select.select_option` works too, and records the reason as `manual`.

## Development

See [`AGENTS.md`](AGENTS.md). To click through the UI in a throwaway Home Assistant: `cd dev && docker --context default compose up -d` ([`dev/README.md`](dev/README.md)).
