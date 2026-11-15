# Local preview

A throwaway Home Assistant in Docker with this repo's integration mounted. Use it to click through the config/options flows and watch the entities. It is not for tests (those use `pytest-homeassistant-custom-component`).

```bash
cd dev
docker --context default compose up -d   # first start pulls the image (~1.5 GB) and takes a minute
# open http://localhost:8123, do onboarding once (any user/password; it stays in dev/config)
```

`--context default` uses the Docker daemon on this machine. Your current context (`docker context ls`) may point at another host (`docker.lan`), where the repo's files aren't, so the bind mounts would come up empty. The commands below assume the same flag.

Then: Settings → Devices & services → Add integration → **Abstract State Machine**.

- **After changing Python code:** `docker --context default compose restart` (HA loads custom components only at startup). For flow and translation changes, a restart is enough. No rebuild.
- **Stop:** `docker --context default compose down`.
- **Reset everything:** stop it, then delete everything in `dev/config/` except `configuration.yaml`. HA runs as root in the container, so its files are root-owned: `docker --context default run --rm -v "$PWD/config":/c alpine sh -c 'find /c -mindepth 1 ! -name configuration.yaml -delete'`.
- **Logs:** `docker --context default compose logs -f`. The integration logs at debug level.

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
