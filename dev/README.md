# Local preview

A throwaway Home Assistant in Docker with this repo's integration mounted. Use it to click through the config/options flows and watch the entities. It is not for tests (those use `pytest-homeassistant-custom-component`).

```bash
cd dev
docker compose up          # first start pulls the image (~1.5 GB) and takes a minute
# open http://localhost:8123, do onboarding once (any user/password; it stays in dev/config)
```

- **After changing Python code:** `docker compose restart` (HA loads custom components only at startup). For flow and translation changes, a restart is enough. No rebuild.
- **Reset everything:** `docker compose down`, then delete everything in `dev/config/` except `configuration.yaml`.
- **Logs:** `docker compose logs -f`. The integration logs at debug level.

## Fake inputs

`configuration.yaml` sets these up so machines have something to react to:

| Entity | Use |
|---|---|
| `input_number.dishwasher_power` (slider, W) | Dishwasher power. Above 10 W → running, below 3 W for a while → clean |
| `input_boolean.dishwasher_door` | Door open → cleared |
| `person.alice`, `person.bob` | Move them with Developer tools → Actions → `device_tracker.see` (`dev_id: alice_phone`, `location_name: home` / `not_home` / `Work`) |
| `zone.work` | Placeholder coordinates; only the name matters here |
| `schedule.alice_work_hours`, `schedule.alice_sleep` | Inputs for an example hour-type machine |
| `input_boolean.alice_phone_charging` | Stand-in for a phone's charging sensor |

Add these through the UI once, since they have no YAML setup: **Workday** (your country), **Holiday** (your country), and **Local Calendar** (call it "Alice", then add an all-day "PTO" event to test the vacation rule).

To test time-based rules without waiting, change the schedule helpers in the UI.
