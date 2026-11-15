# Abstract State Machine

A Home Assistant helper for your own **state machines**: "Alice's day type" (workday / weekend / holiday), "the dishwasher" (cleared / loaded / running / clean), "house mode", whatever you need. You name the states. Each machine is an entity whose state you can use in automations, dashboards and other machines.

**Status: 0.2.0.** Machines move on their own through **rules**: HA triggers and conditions, the same pickers as the automation editor. You can still set them by hand or from automations. See [`.agents/plan.md`](.agents/plan.md).

## Install

HACS → Integrations → ⋮ → Custom repositories → add this repository (category: Integration) → install **Abstract State Machine** → restart Home Assistant. Needs HA 2026.10 or newer.

## Set up

Settings → Devices & services → **Add integration** → Abstract State Machine. First pick where the machines go:

- **A new virtual device**, e.g. "Alice time" for Alice's day type and hour type, or "House".
- **A device I already have**, e.g. the dishwasher's smart plug, or Alice's phone. HA people don't have devices, but their phones do.
- **No device**: plain helper entities.

Then add the first machine: a name, its states (type one, press Enter, type the next), an optional person, and the **fallback state** (where it starts, and where it goes when no condition rule matches). Add more machines to the same device with **+ Add state machine** on the integration page. Edit a machine from its ⋮ → **Reconfigure** (rules, states, renames, fallback, flip limit, icons), and move a device's machines from **Configure**.

## Rules

Rules belong to the state they lead to: ⋮ → Reconfigure → **Rules** → pick a state → add a rule.

- **Trigger rule**: when something happens, go to this state. Optional **From** states (only from those) and **Only if** conditions. Example, into `clean`: from `running`, trigger "dishwasher power below 3 W for 15 minutes".
- **Condition rule**: while something is true, be in this state. The **first state from the top** with a matching condition rule wins; when none match, the machine goes to its fallback. So the order of the states is the priority. Example, a day type with states `vacation`, `holiday`, `workday`, `weekend` (fallback): `vacation` while the PTO calendar is on, `holiday` while the holiday calendar is on, `workday` while the workday sensor is on.

Condition rules are re-checked whenever an entity they use changes, and once a minute for time, sun and template conditions.

**Setting a state by hand** (or a trigger rule moving the machine) **holds** against its condition rules until their answer changes. Pick `holiday` on a workday, and the machine stays there until the rules say something other than `workday` (at midnight, say), then follows them again. `set_state` can also hold for a fixed time, and `clear_override` ends a hold now. An input that's unavailable counts as "doesn't match", as in automations.

**Machines can read each other**: hour type can use "day type is workday", house mode can use everyone's hour type. If a new rule makes machines read each other in a loop, the flow warns you before saving. Each machine also has a **flip limit** (default 10): after that many rule-driven changes in a minute, it ignores its rules until the minute is up and raises a repair issue.

The machine's `reason` attribute names the rule that put it in its state; `rules_state` shows what its condition rules say right now.

## What you get

Per machine, on the chosen device:

| Entity | What it does |
|---|---|
| `select.<device>_<machine>` (e.g. `select.alice_time_day_type`) | The current state. Pick an option to set it by hand. Attributes: `previous`, `reason` (rule name, `manual`, `fallback`, …), `changed_at`, `manual`, `person`; with condition rules also `rules_state`, `override`, `override_until`; `flip_limited` while it's over its flip limit |
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

`select.select_option` works too, and records the reason as `manual`. On a machine with condition rules, add `hold: "02:00:00"` to go back to the rules after two hours at the latest, or call `abstract_state_machine.clear_override` to go back now.

## Development

See [`AGENTS.md`](AGENTS.md). To click through the UI in a throwaway Home Assistant: `cd dev && docker --context default compose up -d` ([`dev/README.md`](dev/README.md)).
