# CLAUDE.md — RoomMind fork (fix cooling control for Rehau/TABS)

## What this repo is
Fork of `snazzybean/roommind` — a Home Assistant custom integration
(`integration_type: hub`, domain `roommind`) that adds smart per-room
climate control on top of existing `climate.*` devices: self-learning
thermal model (EKF), MPC optimizer, proportional valve control, solar
gain, schedules, presence, mold protection, cover shading. It has a
**Lit/TypeScript frontend panel** (`frontend/`) plus the Python backend
(`custom_components/roommind/`).

Owner/fork: Cristian (GitHub `cryystyy`). Working branch:
`fix/cooling-control`. Remotes: `origin` = the fork, `upstream` =
snazzybean. Goal: fix cooling control so it works on a
Rehau-NEA-Smart / TABS (concrete-slab) cooling system, keep changes
clean and upstream-mergeable, PR back.

## Why we forked — the symptom (empirically confirmed on live hardware)
On the owner's system (3 rooms: Office, Master Bedroom, Ula's Room, each
a Rehau MQTT `climate.*` zone that supports only `hvac_modes: [off, cool]`
in summer), RoomMind with device **Type = "Thermostat"** NEVER engaged
cooling for two full days despite rooms at 27 C, cool target 24 C:
- `sensor.roommind_<area>_mode` stayed `idle`.
- `sensor.roommind_<area>_target_temp` reported the **heating** setpoint
  (23) even in cooling season — i.e. the controller was stuck in a
  heating decision frame and never issued a cool command.
- Changing device **Type to "Climate Device"** made it immediately go
  `mode=cooling` and drive the zone correctly (direct `hvac_mode`
  commands instead of the proportional/idle setpoint path). That is the
  workaround; the fork's job is to make the Thermostat/proportional path
  and the idle behaviour correct too.

## CONFIRMED BUGS (from source review of upstream main — fix these)

### BUG 1 — "Low setpoint (keep awake)" overcools in cooling season
File: `custom_components/roommind/utils/device_utils.py` (enum) +
`custom_components/roommind/control/mpc_controller.py` (`async_idle_device`,
`_resolve_idle_setpoint`).
- Idle actions: `IDLE_ACTION_OFF="off"`, `IDLE_ACTION_FAN_ONLY`,
  `IDLE_ACTION_SETBACK="setback"`, `IDLE_ACTION_LOW="low"`,
  `DEFAULT_IDLE_SETBACK_OFFSET=2.0`. UI dropdown only exposes
  "Turn off" (off) and "Low setpoint (keep awake)" (low).
- `IDLE_ACTION_LOW` resolves the idle setpoint to the device's
  **`min_temp`** unconditionally and never inspects `hvac_mode`. On a
  cooling device (`hvac_mode=cool`) writing `min_temp` (~16 C) = MAXIMUM
  cooling demand = overcool. It was designed for heating TRVs.
- `IDLE_ACTION_SETBACK` DOES invert correctly (reads live hvac:
  `cool -> cool_target + offset`, `heat -> heat_target - offset`).
- FIX: make `IDLE_ACTION_LOW` (and `_resolve_idle_setpoint`) invert for
  cooling — when the device's active/desired mode is `cool`, idle to
  `max_temp` (or `cool_target + offset`), not `min_temp`. Also consider
  exposing `setback` / `fan_only` in the frontend Type/idle dropdown.

### BUG 2 — Proportional/Thermostat path never enters COOLING (the big one)
Files: `control/mpc_controller.py` (`_evaluate_bangbang`, `_evaluate_mpc`,
`async_apply`), `coordinator.py` (`_build_room_state_dict`,
target/mode resolution), `utils/temp_utils.py` (target selection),
`const.py` (mode + setpoint-mode constants).
- With Type=Thermostat / `SETPOINT_MODE_PROPORTIONAL`, on a cool-only
  device the controller reported `target_temp` = the HEATING setpoint and
  stayed `MODE_IDLE` — it selected the heat target and idled instead of
  choosing COOL when room >> cool target. Type=Climate Device (direct
  `set_hvac_mode`) does not hit this path and works.
- INVESTIGATE + FIX: in Auto climate mode, the heat/cool decision and
  target selection must pick COOL (and the cool target) when
  `room_temp > cool_target` and the device supports cool / it is cooling
  season. Ensure the proportional apply path issues `set_hvac_mode(cool)`
  + boost setpoint like the Climate-Device path does. Compare the two
  Type code paths (search `direct` / `_direct_eids` /
  `SETPOINT_MODE_DIRECT` vs proportional) and make Thermostat behave.

### BUG 3 — Mode sensor shows internal decision, not device reality (cosmetic but confusing)
File: `coordinator.py` (`_build_room_state_dict`, `display_mode`).
- In Full Control (`has_external_sensor=True`) the `mode` sensor mirrors
  the controller's commanded mode, NOT the device's actual hvac state
  ("Full Control: controller's mode is authoritative"). So it can read
  `idle`/`Standby` while the physical zone is cooling, and vice-versa.
- FIX (optional/low priority): add a reconciliation so the displayed
  mode reflects actual device action when they diverge (there is already
  a one-way "ghost-heating guard" for EKF training — extend the idea to
  display, or surface both commanded vs actual in the panel).

## Control-loop facts (context, don't re-derive)
- `DataUpdateCoordinator`, `UPDATE_INTERVAL = 30` s (const.py). After a
  reload, mode defaults to `MODE_IDLE` until the first cycle; early
  cycles use bang-bang hysteresis, MPC engages once enough EKF samples
  accumulate (`_has_enough_data`, `MIN_IDLE_UPDATES`/`MIN_ACTIVE_UPDATES`).
- Proportional cooling drive: `ac_cool_boost` = device `min_temp` or
  const `AC_COOLING_BOOST_TARGET=16`; setpoint scaled by `power_fraction`
  toward the boost, clamped `min(effective_target, ...)`. Heating analog
  uses `HEATING_BOOST_TARGET=30`. Deadband `PROPORTIONAL_DEADBAND_C=0.5`.
- Outdoor cooling gate exists (default "no cooling below ~16 C outdoor").
- `control/__init__.py` is empty; all logic is in
  `control/mpc_controller.py`, `control/mpc_optimizer.py`,
  `control/thermal_model.py`.

## Tasks (priority order)
1. BUG 2 — make Thermostat/proportional Auto-mode enter COOLING correctly
   (root cause of "never cools"). Acceptance: with Type=Thermostat,
   Climate Mode=Auto, room 27 C > cool target 24 C, cool-only device →
   RoomMind goes `mode=cooling`, issues `set_hvac_mode(cool)` + boost
   setpoint, and modulates/stops at ~24 (does NOT sit idle, does NOT
   overcool to 15/16).
2. BUG 1 — invert `IDLE_ACTION_LOW` for cooling (no overcool when idle
   in cooling season). Acceptance: room at cool target, idle_action=low,
   cool device → setpoint written = max_temp (no cooling demand), device
   stays awake, does not overcool.
3. BUG 3 — mode display reconciliation (optional).
4. Add tests under `tests/` for the cooling decision + idle inversion.

## Build & release (how to "build" and install into HA)
This repo has BOTH a Python backend and a Lit/TS frontend, and ships via
HACS as a zip asset. `hacs.json`: `zip_release: true`,
`filename: "roommind.zip"`; manifest `version: 1.7.5`, HA `2026.5.0+`.

Frontend build (required before packaging — the panel is bundled into
the integration):
```
cd frontend
npm ci
npm run build          # = tsc && vite build; output lands inside custom_components/roommind
```
(Check `frontend/vite.config.*` for the exact outDir; deploy.sh tars the
whole `custom_components/roommind` AFTER building, so built assets must
already be inside it.)

Two install paths for the live box:
A) DEV (fast iteration) — `./deploy.sh <HA_IP> <ssh_port>` builds the
   frontend and SSH-copies `custom_components/roommind` to
   `/config/custom_components/roommind`. Requires the Terminal & SSH
   add-on running and SSH creds (see `.env.example`). Then: Python
   changes → reload the RoomMind config entry; frontend changes →
   hard-refresh browser; manifest/WS changes → restart HA.
B) HACS RELEASE (clean, gives Update button) — mirror the Ariston fork:
   1. bump `custom_components/roommind/manifest.json` version.
   2. commit + push branch; make it the fork default branch if needed.
   3. build frontend (npm run build).
   4. `cd custom_components/roommind; zip -r ../../roommind.zip .`
      (files at zip ROOT — manifest.json at top level, matching
      `filename: roommind.zip`).
   5. tag + release on the FORK (not upstream):
      `git tag vX; git push origin vX`
      `gh release create vX --repo cryystyy/roommind --verify-tag ...`
      then `gh release upload vX roommind.zip --repo cryystyy/roommind`.
      NOTE: `gh release create` may need the `workflow` scope; if the
      first push errors, push the tag via plain git first, then create
      the release with `--verify-tag`. Target `--repo cryystyy/roommind`
      explicitly (gh defaults to upstream on a fork).
   6. HACS → custom repo `https://github.com/cryystyy/roommind`
      (Integration) → download vX → restart HA. Use a clean (non
      pre-release) tag so HACS doesn't hide it as beta.

## Live test environment (a REAL inhabited house — be careful)
- HA at `http://homeassistant.local:8123` (HAOS on HA Yellow). RoomMind
  panel in sidebar. Owner present for live tests.
- Managed rooms + Rehau zones:
  - Office → `climate.marley_home_birou` (currently Type=Climate Device,
    working, cooling)
  - Master Bedroom → `climate.marley_home_dormitor_matrim`
  - Ula's Room → `climate.marley_home_dormitor_ula`
  - All support `hvac_modes: [off, cool]` in summer only. External temp
    sensors are Netatmo (human-height), humidity too.
- Cooling hydraulics: single heat-pump circuit (Ariston NIMBUS 80 S).
  Heat pump makes ~8 C water for a living-room fan-coil (VCV, not in HA);
  a Rehau mixing station tempers it to ~16 C for CEILING TABS (pipes 3 cm
  deep in the slab) in the 3 managed rooms. HIGH latency: the slab/water
  cools long before room air does.
- CONDENSATION is handled by the REHAU base station at installer level:
  it raises the supply-water temperature dynamically to stay above the
  measured dew point (dew-point supply reset) — it does NOT stop cooling.
  This is the primary, correct defense. Two HA automations exist as a
  thin backup only (`safety_floor_cooling_condensation_guard`,
  `safety_tabs_mixing_valve_failure_guard`) but should be treated as
  redundant/last-resort — do NOT design RoomMind logic around them and do
  not over-tighten them (they can fight the Rehau's own modulation).
- The Rehau base station ENERGY LEVEL must be "Normal" (not Standby) for
  any cooling to happen — set it in the Rehau add-on UI (sidebar REHAU →
  System), NOT the HA `select.marley_home_livello_energia` (that reverts).
- Test protocol: reproduce with Type=Thermostat on ONE room (Office) only,
  keep the other two on the known-good path so the house stays cool.
  Reading is always safe; coordinate any live write with the owner.

## Code map
- custom_components/roommind/control/mpc_controller.py — MODE decisions
  (`_evaluate_bangbang`, `_evaluate_mpc`), device apply (`async_apply`),
  idle (`async_idle_device`, `_resolve_idle_setpoint`). **Bugs 1 & 2 here.**
- .../coordinator.py — 30 s loop, room-state dict, `display_mode`
  (Bug 3), target/mode plumbing.
- .../utils/device_utils.py — IDLE_ACTION_* enum, device capability
  helpers (min/max temp, supported hvac modes, Type routing).
- .../utils/temp_utils.py — comfort/eco + heat/cool target resolution.
- .../const.py — MODE_*, SETPOINT_MODE_*, boost/deadband constants.
- .../config_flow.py + frontend/ — where device Type ("Thermostat" vs
  "Climate Device") and "When idle" are chosen; expose better options here.
- tests/ — add regression tests for the cooling path.

## Definition of done
1. `npm run build` + `pytest` (tests/) green; hassfest/CI passes.
2. On the live NIMBUS/Rehau rig, Type=Thermostat + Auto cools a hot room
   to target and stops (no idle-stuck, no overcool), verified WITH the
   owner. The other rooms unaffected.
3. Non-cooling / heating-only setups unregressed.
4. Branch pushed to origin; PR drafted upstream (snazzybean) citing the
   live evidence. Keep this CLAUDE.md in the fork, exclude from the PR.

## ============================================================
## STATUS UPDATE 2026-07-13 -- original cooling fix SHIPPED; new display work below
## ============================================================

The original cooling bugs (BUG 1/2/3 above) are DONE and released. Keep that
section for history but treat it as COMPLETED. Current fork state:

SHIPPED / DONE:
- BUG 2 (Thermostat/proportional Auto never entered cooling) -- FIXED and
  validated live: Office runs Type=Thermostat, Climate Mode=Auto, and now
  goes mode=cooling with target_temp = the COOL target (24.0), driving the
  Rehau zone instead of sitting idle.
- BUG 1 (IDLE_ACTION_LOW overcool) -- addressed; idle action "Turn off" in use.
- Ships via HACS as a zip asset. Current shipped version: v1.7.7.
- VERSION-SYNC FIX (important, do not regress): custom_components/roommind/
  const.py `VERSION` MUST always equal manifest.json `version`. They drifted
  (const 1.7.5 vs manifest 1.7.6); __init__.py::_async_check_version_mismatch
  compares them on every setup and raised a PERSISTENT is_fixable
  "restart_required" repair -- a false "new version / please restart" nag that
  no restart could clear. Fixed by bumping BOTH to 1.7.7.
  RULE: every release, bump manifest.json version AND const.py VERSION to the
  SAME value.
  NOTE: the CI release workflow (.github/workflows/release.yml) is DORMANT --
  `gh run list` shows zero runs ever; the v1.7.6/v1.7.7 zips were built and
  uploaded by hand. Build roommind.zip locally and `gh release upload`.
- LIVE CONFIG NOW: Office / Master Bedroom / Ula's Room all have
  climate_control_enabled=true, each with a linked schedule
  (schedule.office_comfort 08-22, schedule.master_bedroom_comfort comfort
  12-19, schedule.ula_s_room_comfort comfort 06-14). Schedules are stored per
  room in room["schedules"] (list of {entity_id}) in .storage/roommind, set via
  the `roommind/rooms/save` websocket cmd (merges). Ula's Room was set to
  climate_mode="cool_only" as a STOPGAP for ISSUE A below; once ISSUE A is
  fixed it can go back to "auto".

## NEW ISSUES TO FIX -- Auto-mode target display + Cooling/Heating direction

### ISSUE A -- In Auto mode the UI headlines the HEAT setpoint as the target (wrong in cooling season)
Symptom (live, reproduced): Ula's Room, Climate Mode=Auto, in its eco window,
summer/cooling. Panel hero showed "TARGET 23.0 - 26.5C" and the schedule chip
showed "Ula's Room Comfort 23.0C (eco)". 23 is eco_heat; the real cooling
target is eco_cool = 26.5. The BACKEND WAS CORRECT: sensor
roommind_ula_s_room_target_temp = 26.5 and the zone was correctly idle
(room 25.7 < 26.5). This is a FRONTEND-ONLY display bug -- it shows the
heat-side number in Auto.

Root cause (frontend/src/components):
- rs-schedule-settings.ts :: _getStatusText() (~L388-428) builds the chip text.
  The eco branch (~L425-427, key "schedule.eco_detail") uses:
      this.climateMode === "cool_only" ? this.ecoCool : this.ecoHeat
  and the comfort/on branch (~L416-421, key "schedule.fallback") uses:
      this.climateMode === "cool_only" ? this.comfortCool : this.comfortHeat
  So for climateMode === "auto" it falls back to the HEAT value -> "23.0C (eco)".
- Same `cool_only ? cool : heat` pattern in the "view-temps" summary
  (~L183-193, keys schedule.view_comfort / schedule.view_eco) and the
  comfort/eco label bindings (~L334-345).
- rs-hero-status.ts :: target render (~L403-415): for auto it shows a RANGE
  `heat_target - cool_target` ("23.0 - 26.5"). Defensible, but combined with
  the chip showing 23 it reads as "targeting 23".

Desired behavior:
- In Auto, the hero target and the schedule chip must reflect the ACTIVE
  direction: when cooling (see ISSUE B), show the COOL comfort/eco value
  (26.5 eco / 25 comfort); when heating, show the HEAT value. Never headline
  the opposite-direction setpoint.
- If direction is genuinely unknown, a range is acceptable ONLY if clearly
  labeled which is heat and which is cool (e.g. "heat 23 / cool 26.5"), not a
  bare "23 - 26.5".
Acceptance: Ula's Room back in Auto, eco, cooling season -> hero shows 26.5
(or a clearly-labeled heat/cool split), chip shows "26.5C (eco)", and no bare
23 is presented as the target.

### ISSUE B -- In Auto, show per-room whether it is currently COOLING or HEATING
Today the hero mode pill (rs-hero-status.ts ~L525-535, formatMode(live.mode)
from utils/room-state.ts) only renders Heating / Cooling / Idle(Standby), and
RoomMode = "heating" | "cooling" | "idle". When a room is idle in Auto you
cannot tell which direction it is oriented to. The owner wants that visible.

Desired behavior:
- When climate_mode === "auto", the room must clearly show the current control
  DIRECTION (Cooling vs Heating) even while idle/standby -- e.g. "Standby -
  Cooling", or a snowflake/radiator sub-icon. The hero accent already keys off
  mode (rs-hero-status.ts ~L475-484); extend it to cover idle+direction.

Implementation notes:
- Backend must expose direction when idle. In coordinator.py::
  _build_room_state_dict (the live/`rs` dict that sets "mode","heat_target",
  "cool_target","heating_power" ~L261-278, and the target pick ~L671-677) add
  e.g. live.direction = "cooling" | "heating", derived from: the device's
  currently-supported hvac (a cool-only Rehau zone in summer -> cooling),
  and/or demand sign / which target the deadband is oriented to, and/or the
  outdoor cooling gate. Trivial for heat_only/cool_only; the real case is
  auto+idle.
- Frontend: extend the live-state type (types/index.ts) with the direction
  field, render it near the mode pill in rs-hero-status.ts, and REUSE it to
  drive ISSUE A's target/chip selection (single source of truth).
Acceptance: an Auto room sitting in the deadband (Standby) still shows whether
it is Cooling or Heating; hero target and chip follow that direction.

### Files / pointers
- frontend/src/components/rs-hero-status.ts   (target ~L403-415; mode pill
  ~L525-535; accent ~L475-484)
- frontend/src/components/rs-schedule-settings.ts (_getStatusText ~L388-428;
  view-temps ~L183-193; comfort/eco labels ~L334-345)
- frontend/src/utils/room-state.ts            (getModeClass, formatMode, RoomMode)
- frontend/src/types/index.ts                 (RoomMode + live-state type)
- custom_components/roommind/coordinator.py    (_build_room_state_dict; target/
  mode pick ~L671-677) -- add live.direction for auto+idle
- Add any new localize() strings to the frontend locale files (schedule.*,
  mode.*, plus a new direction/standby-cooling key as needed).

Build/release reminder: npm run build in frontend/; zip custom_components/
roommind at ROOT; bump BOTH manifest.json version AND const.py VERSION to the
same new value; gh release create + `gh release upload roommind.zip`; HACS
update; restart HA. Verify on the live box WITH the owner (use Office or Ula's;
keep the house cool). Keep this CLAUDE.md in the fork, exclude from any upstream PR.

## ============================================================
## STATUS UPDATE 2026-07-13 (2) -- v1.8.0 "intelligence release" SHIPPED
## ============================================================

Shipped in v1.8.0 (9 features, all tested, everything opt-in or safe-by-default):
- Economic price-aware MPC: settings price_entity (Nordpool/Tibber/ENTSO-E
  attr formats, utils/price_utils.py), hp_cop_at_minus7/plus7 COP curve,
  grid_export_entity + pv_export_threshold_w PV soak-up. Optimizer gets a
  normalized cost_series (mean 1.0); economic path uses a model-relative
  energy term (ECON_ENERGY_SCALE) because the legacy abs(Q)/1000 term is
  ~0 for C=1 EKF models. No price entity => byte-identical legacy behavior.
- Dew-point condensation guard (dewpoint_guard_enabled, default ON, radiant
  rooms only; dewpoint_margin default 2.0C): boost floor at dew+margin +
  hard cooling cut when air-dewpoint < margin. Complements the Rehau reset.
- Feels-like cool targets (feels_like_enabled, default OFF).
- Decision trace: coordinator._decision_traces ring buffer, WS
  roommind/decisions/get, decision_reason/_target_source in live state,
  'why' chip in hero.
- Shadow mode (room.shadow_mode): decisions traced, devices untouched,
  training/display from observed state.
- Cold-start priors (ThermalEKF._SYSTEM_PRIORS by heating_system_type).
- Model confidence chip + slab state-of-charge sensor
  (sensor.<area>_slab_charge, ResidualHeatTracker.get_charge_fraction —
  heating charge only, cooling "cold charge" not yet tracked).
- New settings panel "Energy optimization" (rs-settings-energy.ts).

Earlier the same day: 21 verified defect fixes from a full-codebase
multi-agent scan (commit 94a7df9) + the v1.7.x cooling work.

QUEUED (agreed roadmap, not yet built — each is a session-sized L/XL item):
1. Demand-driven minimum supply-temperature optimizer (OpenTherm/HP output)
2. Per-room energy attribution + counterfactual savings report
3. Operative-temperature control for radiant rooms (incl. solar MRT)
4. Guided onboarding wizard + physical-plausibility config validation
5. 2R2C slab-state thermal model (learned thermal mass — the big bet;
   upgrades dew-point guard, SoC and pre-heat timing from heuristic to physics)
6. Tier 3: capacity arbiter, occupancy profiles, sensorless window detection,
   NIS model-health monitoring, night purge, what-if simulator, bug-report
   bundle, adaptive comfort band.

Testing on Windows: use .venv, run pytest with -p no:homeassistant -p no:sugar
(HA imports Unix-only fcntl via the pytest plugin).
