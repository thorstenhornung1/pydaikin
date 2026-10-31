# QA evidence for the BRP069 control-write / temperature-step PRs

Not part of the PRs. Scripts used to verify the stacked branches
`split/a-control-write` (layer A) and `split/b-temperature-step` (layer B).

## Offline: mutation check

```sh
git checkout split/a-control-write && python qa/mutate_layers.py A   # 12/12 killed
git checkout split/b-temperature-step && python qa/mutate_layers.py B # 25/25 killed
```

Each mutation removes or weakens one guarded behaviour, e.g. the lock, the
no-retry rule for writes, the pre-read check, the read-back, or the
dry/fan rule. The layer's tests must then fail. The baseline must be green
first.

## Live: guarded device tests (optional, needs a BRP069 unit)

```sh
DAIKIN_IP=192.168.x.y python qa/live_layer_a.py        # layer A checked out, unit in heat mode
DAIKIN_IP=192.168.x.y python qa/live_layer_b.py        # layer B checked out, unit in heat mode
DAIKIN_IP=192.168.x.y python qa/live_layer_b_modes.py  # layer B, unit running in heat mode
```

Safety: a class-level guard sits in front of every pydaikin request.
* Only `aircon/set_control_info` is let through. pow/mode/fan/swing must equal
  the snapshot (`live_layer_b_modes.py` additionally allows running fan
  mode), and stemp must be within 22–23 °C.
* Faults (PARAM NG, ret=NG pre-read, lost answer, different read-back, mode
  change) are injected client-side and never reach the device.
* The original state is restored and compared at the end.

Results on a Stylish 0FC1 (adapter 4_2_303, multi-split, 31 Oct 2026):

| Script | Python 3.12 | Python 3.13 |
|---|---|---|
| `live_layer_a.py` | 7/7 | – |
| `live_layer_b.py` | 15/15 | 15/15 |
| `live_layer_b_modes.py` | all cases as expected | – |

Every run ended without difference to the snapshot.
