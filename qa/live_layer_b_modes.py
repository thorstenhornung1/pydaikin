"""Guarded live test of layer B: mode change during verification, real fan mode.

Start state must be: running in heat mode. Allowed writes only: running
heat or running fan (never on/off), stemp 22..23. Read-backs can be faked
client-side (FAKE_FIELDS) to simulate a mode change between write and
read-back. Ends back in running heat mode and compares with the snapshot.
"""

import asyncio
import os
import logging
import sys
import time

from aiohttp import ClientSession

from pydaikin.daikin_base import Appliance
from pydaikin.daikin_brp069 import DaikinBRP069

IP = os.environ["DAIKIN_IP"]  # e.g. DAIKIN_IP=192.168.x.y
CTRL = "aircon/get_control_info"
ORIGINAL = Appliance._do_get_resource
SNAP = {}
WARN = []
logging.getLogger("pydaikin").addHandler(
    type("H", (logging.Handler,), {"emit": lambda self, r: WARN.append(r.getMessage())})(level=logging.WARNING)
)
ALLOWED = {("1", "4"), ("1", "6")}
RANGE = {"4": (22, 23), "6": (22, 23)}


async def guarded(self, path, params, strict=False):
    if "/set" in path or "notify" in path or "register" in path:
        mode, pow_ = params.get("mode"), params.get("pow")
        lo, hi = RANGE.get(mode, (0, -1))
        try:
            stemp_ok = lo <= float(params["stemp"]) <= hi
        except ValueError:
            stemp_ok = mode == "6" and params["stemp"] == "--"
        ok = (
            path == "aircon/set_control_info"
            and (pow_, mode) in ALLOWED
            and stemp_ok
            and params.get("shum") in {SNAP.get(f"dh{mode}", SNAP["shum"])} | ({"--"} if mode == "6" else set())
            and params.get("f_dir") == SNAP["f_dir"]
            and params.get("f_rate") in {SNAP["f_rate"], SNAP.get("dfr4"), SNAP.get("dfr6")}
            and set(params) == {"mode", "pow", "shum", "stemp", "f_rate", "f_dir"}
        )
        if not ok:
            print(f"   GUARD BLOCKED {path} {params}")
            raise RuntimeError("guard")
        print(f"   -> WRITE {params}")
        FAKE["armed"] = FAKE["value"] is not None
        return await ORIGINAL(self, path, params, strict=strict)
    result = await ORIGINAL(self, path, params, strict=strict)
    if path == CTRL and FAKE["armed"]:
        FAKE.update(armed=False, value=None)
        print(f"   <- read-back faked {FAKE_FIELDS} (device has mode={result.get('mode')} stemp={result.get('stemp')})")
        return {**result, **FAKE_FIELDS}
    return result


FAKE = {"value": None, "armed": False}
FAKE_FIELDS = {}



Appliance._do_get_resource = guarded


async def observe(dev, label, seconds=0):
    end = time.monotonic() + seconds
    while True:
        c = await ORIGINAL(dev, CTRL, {})
        s = await ORIGINAL(dev, "aircon/get_sensor_info", {})
        print(f"   [{label}] pow={c['pow']} mode={c['mode']} stemp={c['stemp']} alert={c.get('alert')} "
              f"err={s.get('err')} cmpfreq={s.get('cmpfreq')} htemp={s.get('htemp')}")
        if time.monotonic() >= end:
            return c
        await asyncio.sleep(15)


async def step(dev, label, settings):
    WARN.clear()
    try:
        await dev.set(settings)
        res = "ok"
    except Exception as exc:  # noqa: BLE001  - never abort the restore path
        res = f"{type(exc).__name__}: {exc}"
    print(f"{label}: set({settings}) -> {res}; step={dev.target_temperature_step} "
          f"status={dev.target_temperature_step_status} cache stemp={dev.values.get('stemp', invalidate=False)}"
          + (f" log={WARN}" if WARN else ""))


async def main():
    async with ClientSession() as session:
        dev = DaikinBRP069(IP, session=session)
        SNAP.update(await ORIGINAL(dev, CTRL, {}))
        keys = ("pow", "mode", "stemp", "dt3", "dt4", "f_rate", "dfr4", "dfr6", "f_dir", "shum")
        print("snapshot:", {k: SNAP.get(k) for k in keys})
        if (SNAP["pow"], SNAP["mode"]) != ("1", "4"):
            sys.exit("ABORT: unexpected start state")
        try:
            await observe(dev, "start")
            FAKE["value"] = 1; FAKE_FIELDS.clear(); FAKE_FIELDS.update(mode="3")
            dev.target_temperature_step_status = "unknown"
            await step(dev, "M1 heat 22.5, read-back faked mode=3 (changed meanwhile)", {"stemp": "22.5"})
            await step(dev, "M2 heat 22 (no read-back, nothing faked)", {"stemp": "22"})
            FAKE["value"] = 1; FAKE_FIELDS.clear(); FAKE_FIELDS.update(mode="6", stemp="--")
            await step(dev, "M2b heat 22.5, read-back faked fan/-- (changed meanwhile)", {"stemp": "22.5"})
            assert not FAKE["armed"], "fake must be consumed by the read-back"
            await step(dev, "M3 restore 22", {"stemp": SNAP["dt4"]})
            await step(dev, "G1 fan mode (running)", {"mode": "fan"})
            await observe(dev, "fan", 15)
            dev.target_temperature_step_status = "unknown"
            await step(dev, "G2 22.5 in real fan mode", {"stemp": "22.5"})
            await observe(dev, "fan")
        finally:
            FAKE["value"] = None
            await step(dev, "BACK to heat (running, as found)", {"mode": "hot"})
            final = await observe(dev, "end")
            diff = {k: (SNAP.get(k), final.get(k)) for k in keys if SNAP.get(k) != final.get(k)}
            print("DIFF vs snapshot:", diff or "none")

asyncio.run(main())
