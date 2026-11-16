# Abstract State Machine

A Home Assistant integration for your own **state machines**: "Alice's day type" (workday / weekend / holiday), "the dishwasher" (cleared / loaded / running / clean), "house mode", whatever you need. You name the states. Each machine is an entity whose state you can use in automations, dashboards and other machines.

**Status: 0.5.0.** Machines move on their own through **rules**: HA triggers and conditions, the same pickers as the automation editor. You can still set them by hand or from automations. See [`.agents/plan.md`](.agents/plan.md).

## Install

HACS → Integrations → ⋮ → Custom repositories → add this repository (category: Integration) → install **Abstract State Machine** → restart Home Assistant. Needs HA 2026.10 or newer.

## Set up

Settings → Devices & services → **Add integration** → Abstract State Machine. Each time you add it, you set up one **person or thing**. Pick where its machines go:

- **A new virtual device**, e.g. "Alice" for Alice's day type and hour type, or "House".
- **A device I already have**, e.g. the dishwasher's smart plug, or Alice's phone. HA people don't have devices, but their phones do.
- **No device**: just the entities, not on any device.

Then add its first machine: a name, its states (type one, press Enter, type the next), an optional person, and the **fallback state** (where it starts, and where it goes when no condition rule matches).

On the integration page, each person or thing is its own entry ("Alice", "Dishwasher"), with its device and its machines' entities. **Add entry** adds another. An entry's **Configure** has:

- **Edit a state machine**: rules, states, renames, fallback, person, flip limit, state sensors, icons.
- **Add a state machine** to the same person or thing.
- **Copy a state machine** from this or another person or thing, e.g. Bob's day type from Alice's. It copies states, rules, icons and settings; rules that read Alice's calendar still do, so edit them afterwards.
- **Remove a state machine** (when there's more than one; to remove the last, delete the entry).
- **Rename, or change the device**: move its machines to another device, or off devices. They keep their state and entity IDs.

## Rules

Rules belong to the state they lead to: **Configure** → Edit a state machine → **Rules** → pick a state → add a rule.

- **Trigger rule**: when something happens, go to this state. Optional **From** states (only from those) and **Only if** conditions. Example, into `clean`: from `running`, trigger "dishwasher power below 3 W for 15 minutes".
- **Condition rule**: while something is true, be in this state. The **first state from the top** with a matching condition rule wins; when none match, the machine goes to its fallback. So the order of the states is the priority. Example, a day type with states `vacation`, `holiday`, `workday`, `weekend` (fallback): `vacation` while the PTO calendar is on, `holiday` while the holiday calendar is on, `workday` while the workday sensor is on.

Condition rules are re-checked whenever an entity they use changes, and once a minute for time, sun and template conditions.

**Setting a state by hand** (or a trigger rule moving the machine) **holds** against its condition rules until their answer changes. Pick `holiday` on a workday, and the machine stays there until the rules say something other than `workday` (at midnight, say), then follows them again. `set_state` can also hold for a fixed time, and `clear_override` ends a hold now. An input that's unavailable counts as "doesn't match", as in automations.

**Machines can read each other**: hour type can use "day type is workday", house mode can use everyone's hour type. If a new rule makes machines read each other in a loop, the flow warns you before saving. Each machine also has a **flip limit** (default 10): after that many rule-driven changes in a minute, it ignores its rules until the minute is up and raises a repair issue.

The machine's `reason` attribute names the rule that put it in its state; `rules_state` shows what its condition rules say right now.

## Calendar events

A calendar entity's state only shows one event at a time, so "is there a PTO event today?" isn't a plain state condition. This integration adds a condition for it, **Calendar has a matching event** (`abstract_state_machine.calendar_event`), in HA's condition picker: in rules, and in your own automations too (once at least one machine is set up). Pick one or more calendars, then:

- **When**: *now* (an event is going on right now), *today* or *tomorrow* (any event on that day).
- **Title contains** (optional): e.g. `PTO`. Case doesn't matter. Tick **Also search the description** to look there too.
- **Kind of event**: any, all-day only, or timed only.

```yaml
condition: abstract_state_machine.calendar_event
target:
  entity_id: calendar.alice
options:
  when: today
  title_contains: PTO
  event_kind: any
```

It reads today's and tomorrow's events every 15 minutes and at midnight, so a newly added event can take up to 15 minutes to count.

## Recipes

Built in the UI, one machine at a time. States are listed top to bottom, so for condition rules the first match wins. Entity names are examples: use your own.

### Day type (per person)

On a virtual device "Alice", machine **Day type**, states `vacation`, `holiday`, `workday`, `weekend` (fallback).

| State | Rule (condition) |
|---|---|
| `vacation` | Calendar has a matching event: `calendar.alice`, today, title contains `PTO` |
| `holiday` | Calendar has a matching event: `calendar.holidays`, today, all-day only. Or: the Holiday integration's calendar |
| `workday` | State: `binary_sensor.workday_sensor` is on (the Workday integration) |
| `weekend` | none: it's the fallback |

Bob gets his own Day type on his own device, with his calendar.

### Hour type (per person, reads day type)

Same device, machine **Hour type**, states `sleeping`, `working`, `free` (fallback).

| State | Rule (condition) |
|---|---|
| `sleeping` | State: `schedule.alice_sleep` is on. A second rule: `input_boolean.alice_phone_charging` is on, and `person.alice` is home, and time is after 22:00 and before 07:00 |
| `working` | State: `select.alice_day_type` is `workday`, and `schedule.alice_work_hours` is on. A second rule: `person.alice` is in zone Work |
| `free` | none: the fallback |

Picking `free` by hand during work hours (a long lunch) holds until the rules change their answer, here at the end of the work schedule.

### House mode (reads everyone's machines)

A virtual device "House", machine **Mode**, states `vacation`, `away`, `sleeping`, `wake_up`, `home` (fallback).

| State | Rule |
|---|---|
| `vacation` | Condition: `select.alice_day_type` is `vacation`, and `select.bob_day_type` is `vacation` |
| `away` | Condition: `zone.home` below 1 (nobody home) |
| `sleeping` | Condition: `select.alice_hour_type` is `sleeping`, and `select.bob_hour_type` is `sleeping` |
| `wake_up` | Trigger: time 06:30, from `sleeping`. It holds against the condition rules until their answer changes (someone stops sleeping), then the house goes to `home` |
| `home` | none: the fallback |

### Dishwasher (on the smart plug's device)

Pick **A device I already have** → the plug, machine **Mode**, states `cleared` (fallback), `loaded`, `running`, `clean`. Only trigger rules, so nothing holds and it simply follows them.

| State | Rule (trigger) |
|---|---|
| `running` | Numeric state: `sensor.dishwasher_power` above 10 for 2 minutes |
| `clean` | From `running`. Numeric state: power below 3 for 15 minutes (long, because dishwashers pause mid-cycle) |
| `cleared` | From `clean`. State: `binary_sensor.dishwasher_door` on for 1 minute |
| `loaded` | none: set by hand, or an NFC tag calling `set_state` (below) |

## What you get

One entity per machine, on the person or thing's device: `select.<device>_<machine>` (e.g. `select.alice_day_type`). Its state is the machine's state; pick an option to set it by hand. Attributes:

- `previous`, `reason` (rule name, `manual`, `fallback`, …), `changed_at`, `manual`, `person`
- with condition rules: `rules_state` (what they say now), `override`, `override_until`
- `flip_limited` while it's over its flip limit

Optionally, **a binary sensor per state** (Edit a state machine → Name, fallback, person and more → *A binary sensor per state*): `binary_sensor.alice_hour_type_sleeping` is on while Alice's hour type is `sleeping`. Handy for dashboards, HomeKit, or anything that wants a plain on/off. Renaming a state keeps its sensor.

In the automation editor's **device** triggers and conditions, HA's own select support already offers "Mode changed from … to …" for a machine on a device.

### Notify when the dishwasher is done

A state trigger on the machine's entity:

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

Give it a `from` (or `not_from: [unavailable, unknown]`): the entity also comes back after a restart or after you edit its machine, and a trigger with only `to: clean` would fire then too.

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

### Why is it in this state?

Developer tools → Actions → **Explain** (`abstract_state_machine.explain`) on a machine returns its state, reason, hold, what its condition rules say, and for every rule whether it matches right now (condition rules), whether it could fire from here (trigger rules: `from_ok`, `conditions_pass`), or why it was skipped (`error`).

```yaml
action: abstract_state_machine.explain
target:
  entity_id: select.alice_day_type
```

## Development

See [`AGENTS.md`](AGENTS.md). To click through the UI in a throwaway Home Assistant: `cd dev && docker --context default compose up -d` ([`dev/README.md`](dev/README.md)).
