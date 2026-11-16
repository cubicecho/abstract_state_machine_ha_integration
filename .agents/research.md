# Research: what already exists

Collected 2026-11-15. Re-check anything marked **verify** before building on it.

## Summary

- **Every piece exists somewhere, but nothing combines them.** I found no integration that is generic (any states, any domain), configured in the UI, uses HA's own trigger and condition editors for its rules, and handles both "what is true now" (day type) and "what just happened" (dishwasher finished).
- The closest generic one is **edalquist/ha_state_machine**. It is transition-only, you paste its config as JSON, and automations drive it by calling a service.
- The closest to our first use case is **HomeShift**: a day-mode `select` driven by a work calendar, weekends and holidays. It covers one concept, with fixed inputs.
- **Core can already do most of it by hand**: a trigger-based template `select`/`sensor` plus `workday`, `holiday`, `schedule`, `calendar`, `person`. The cost is Jinja for every machine, no record of why it changed, no override handling, and nothing reusable between people.

So this is **a fairly new combination, not a new idea**. Our value is in the packaging: a UI-built machine, ordered rules that record *why* the state is what it is, manual overrides that expire, and machines that read each other (house mode built from each person's day type).

## Prior art

| Project | What it does | How it's configured | Gap for us |
|---|---|---|---|
| [edalquist/ha_state_machine](https://community.home-assistant.io/t/finite-state-machine-sensor/510915) (custom component) | FSM **sensor** backed by the Python `transitions` library. States list triggers (`above`, `timeout: {after, to}`) and where they lead. | JSON (Node-RED FSM format) pasted into a helper form. [`hasmtools`](https://pypi.org/project/hasmtools/) converts JSON ↔ YAML and draws Graphviz diagrams. | Transitions only, nothing derived. Automations must call `state_machine.trigger` with a trigger name, so the logic is split between the machine and automations. Hand-written JSON. Its domain `state_machine` is taken. Repo status: **verify**. |
| [RobertoPeters/HomeAssistantStateMachine](https://github.com/RobertoPeters/HomeAssistantStateMachine) | External C# Blazor app with a diagram editor. JavaScript in states and transitions. Talks to HA and MQTT. | Its own web UI. | Not an HA integration. Its entities aren't native, and it's a separate service to run. The diagram editor is a good UI idea. |
| [aneeshd/schedule_state](https://github.com/aneeshd/schedule_state) (HACS) | Sensor with any string value, picked by time-of-day blocks plus conditions. Re-checks when the entities in a condition change. | YAML. | Close to "hour type", but YAML only and built around a schedule. No transitions, no per-person presets. |
| [Gamso/homeshift](https://github.com/Gamso/homeshift) (custom) | `select.day_mode` (Home / Work / Remote / Away …) from a work calendar, weekend and holiday calendars, with event-name → mode mapping. Turns scheduler switches on and off per mode. Has a companion card. | UI config flow. | One fixed concept. Not generic, and per-person support is unclear (**verify**). Good evidence the day-type use case is real. |
| Magic Areas (HACS) | Per-area states (occupied, clear, extended, sleep, …) and area-level aggregate entities. | UI. | Fixed states per area. Not a general machine. |
| [WashData](https://community.home-assistant.io/t/washdata-thread/964758) (HACS), [Appliance Notifications blueprint](https://community.home-assistant.io/t/650166) | Washer/dishwasher cycle detection from smart-plug power; WashData also estimates time left. | UI / blueprint. | Appliance-only. Notes worth keeping: dishwashers have **long low-power pauses mid-cycle**, so "finished" needs a long `for:`; anti-wrinkle phases read as still running. |
| [muxa/esphome-state-machine](https://github.com/muxa/esphome-state-machine) | FSM on ESPHome devices. | ESPHome YAML. | On the device, not HA-wide. |
| AppDaemon (`appdaemon-machine`, [ad-ModeManagement](https://github.com/Pythm/ad-ModeManagement)), pyscript, Node-RED FSM, [entity-controller](https://github.com/maxlyth/entity-controller) | State machines written as code, or motion-light controllers built on `transitions`. | Code. | Not native entities or UI. |

Community threads ask for this over and over: [house modes](https://community.home-assistant.io/t/any-notion-of-house-modes-states-in-home-assistant/733385), [an FSM with transitions](https://community.home-assistant.io/t/add-ability-to-create-finite-statemachine-and-define-transitions/427035), and [a sleep/awake status](https://community.home-assistant.io/t/wth-no-sleep-awake-status-option/482176). People usually answer with an `input_select` plus a pile of automations.

## HA core building blocks we reuse

| Piece | Use |
|---|---|
| **Trigger engine** (`homeassistant.helpers.trigger.async_initialize_triggers`) | Trigger rules use the same triggers as automations: state, numeric_state with `for:`, time, sun, calendar event start/end with offset, zone, tag, template. No rule language of our own. |
| **Condition engine** (`homeassistant.helpers.condition.async_from_config`) | Condition rules use automation conditions. To know when to re-check, read the entities they reference (`condition.async_extract_entities`, **verify** name and that it still exists) and add a once-a-minute tick for time and sun conditions. |
| **`TriggerSelector` / `ConditionSelector`** | Options-flow forms that show the automation editor's trigger and condition pickers. They exist and blueprints use them. A [forum report](https://community.home-assistant.io/t/selector-conditionselector-inconsistency/899965) says a `ConditionSelector` in a config flow stores `entity_id` as a list when the editor sent a string, so normalise before storing. **Verify** both render inside options flows on 2026.11. |
| **`select` entity** | The machine's state. `options` = states. Choosing one in the UI is a manual set. State triggers in the automation editor already offer the options as from/to dropdowns. |
| **`event` entity** (2023.8+) | Considered for a "Transition" event per machine; dropped in 0.3.0 to keep it simple (state triggers on the select do the job). |
| **Config flows + subentries** (`integration_type: hub`) | Entries are added under Settings → Devices & services → Add integration. Not `helper`: that lists the entries and their entities under Settings → Helpers. |
| **Linking a helper to an existing device** | Template, derivative and similar helpers can attach their entity to a chosen device (`homeassistant.helpers.device.async_device_info_to_link_from_device_id`, **verify**). That puts the dishwasher machine on the dishwasher's smart-plug device. |
| **`workday` binary sensor**, **`holiday` calendar** | Country holidays and which weekdays are workdays. Typical inputs for a day-type machine. |
| **`schedule` helper** | A weekly block per person (work hours, sleep hours). Typical inputs for an hour-type machine. |
| **`calendar` entities and triggers** | PTO, vacation, meetings, focus time. A calendar entity is `on` with the current event in `message`, but only for **one** event at a time. Matching "any current event whose title contains X" needs a template, or a condition type we provide. Verified: integrations can add condition platforms (`condition.py`, 2026.10+); `abstract_state_machine.calendar_event` does this. |
| **`person`, `zone`, mobile app sensors** | Presence (home, work zone), phone charging / sleep-ish signals for "sleeping". |
| **`RestoreEntity`** | Keeps transition machines in their state across restarts. |

## Takeaways for the design

1. **Don't invent a rule language.** Use HA triggers and conditions, edited with HA's own selectors. This is the biggest difference from edalquist's machine.
2. **Keep the logic in the machine.** The integration should not depend on automations calling a service to move it, though a `fire` signal service is still useful as an extra input.
3. **Record why.** A `reason` attribute (rule name, `manual`, `restored`) is what template machines don't give you, and it's what you need when "why does it think it's a holiday?" comes up.
4. **Events come for free.** Every change is a `state_changed` on the `select`, so "dishwasher running → clean, notify me" is an ordinary automation. (An event entity and a bus event were tried and dropped in 0.3.0.)
