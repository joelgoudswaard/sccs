# SCCS Socket Event Contract

This document defines every Socket.IO event used by the lighting backend in this pass.

## Offline overlay

When Socket.IO disconnects (or fails to load), `#offline-overlay` appears after 800ms and blocks interaction until reconnect. Implemented in `offline-overlay.js` + `socket-client.js`.

## Client → Server

### `light_change`

User moved a dimmer slider or toggled a dimmer pill.

```json
{ "name": "kitchen_bench", "brightness": 65 }
```

RGB bug lights include mode:

```json
{ "name": "kitchen_panel", "brightness": 55, "mode": "red" }
```

- `name` must be in `compiled.light_names`
- `brightness` clamped 0–100 server-side
- `mode` only applied for RGB lights

**HTTP fallback:** `POST /api/light` with the same JSON body.

### `relay_change`

User toggled a relay control (socket only — no HTTP fallback).

```json
{ "name": "floodlights", "on": true }
```

Relays use `expires: manual` — they persist through scenes, reed changes, and phase changes until toggled again.

### `get_reeds`

Request effective reed states. Server replies with `reed_update`.

### `set_scene`

Activate a lighting scene (one-shot reconcile with ramp).

```json
{ "scene": "bedtime" }
```

**HTTP fallback:** `POST /api/scene` with the same JSON body.

### `force_phase`

Lock or clear phase override.

```json
{ "phase": "Evening" }
```

Clear: `{ "phase": null }`

### `force_reed`

Diagnostics override (also affects main UI effective states).

```json
{ "name": "rooftop_tent", "closed": true }
```

Clear force: `{ "name": "rooftop_tent", "closed": null }`

## Server → Client

### `lights_config`

Sent on `connect`. Ordered list of UI controls from `sccs.conf`:

```json
[
  { "name": "rooftop_tent", "label": "Rooftop Tent", "type": "dimmer", "icon": "fa-tent", "has_mode": false, "order": 10 }
]
```

`type` is `dimmer` or `relay`.

### `state_update`

Authoritative light/relay levels after reconcile.

```json
{
  "kitchen_bench": 65,
  "kitchen_panel": 55,
  "kitchen_panel_mode": "white",
  "floodlights": 1
}
```

- Dimmers: 0–100 integer brightness
- Relays: truthy/falsey on state
- RGB lights: `{name}_mode` key with `white` or `red`

### `reeds_config`

Sent on `connect`. Reed metadata for the system tile:

```json
[{ "name": "kitchen_panel", "label": "Kitchen Panel", "icon": "fa-utensils", "order": 10 }]
```

### `reed_update` (main UI)

Effective reed states — hardware merged with operator forces.

```json
{ "states": { "rooftop_tent": true, "kitchen_bench": false } }
```

`true` = closed (secure), `false` = open.

**Main UI rule:** clients must not guess forced vs raw. Use this event only.

### `reed_diag_update` (diagnostics — future tile)

Raw hardware + force metadata. Not consumed by the lighting tab yet.

```json
{
  "states": { "rooftop_tent": true },
  "forced": { "kitchen_bench": false }
}
```

### `phase_update` (main UI)

```json
{ "phase": "evening", "day_start": "06:12", "evening_start": "18:45", "night_start": "21:30" }
```

### `gps_update`

Full GPS telemetry from `modules/gps` (also drives home location tile).

```json
{
  "fix_quality": 2,
  "satellites": 9,
  "latitude": -37.191,
  "longitude": 145.711,
  "suburb": "Alexandra",
  "hardware_missing": false
}
```

### `sensor_update`

Water tank and 1-wire temperatures.

```json
{
  "water_percent": 68,
  "outside_temp_c": 12.4,
  "fridge_temp_c": null,
  "temp_valid": true
}
```

### `set_gps_simulation`

Diagnostics: force no-fix mode.

```json
{ "no_fix": true }
```

### `phase_diag_update` (diagnostics)

```json
{ "forced": false }
```

`forced: true` when operator has locked phase via diag controls.

## REST

| Route | Purpose |
|-------|---------|
| `POST /api/light` | HTTP fallback for `light_change` |
| `GET /api/scenes` | Scene list for Scenes tab |
| `POST /api/scene` | HTTP fallback for `set_scene` |
| `GET /api/reeds` | Reed labels/icons for system tile |
| `GET /api/explain` | Policy snapshot: sources, desired vs observed, drift |