"""Guarded live QA of layer B (adaptive temperature step) against a real BRP069.

Safety: a class-level guard sits in front of every pydaikin HTTP request. Only
aircon/set_control_info may be sent, with every parameter except stemp equal to
the snapshot (so pow/mode/fan/swing never change) and stemp within 22..23.
Faults are injected client-side; injected requests never reach the device.
The original setpoint is restored and compared at the end.
"""

import asyncio
import os
import logging
import sys
import time

from aiohttp import ClientSession

from pydaikin.daikin_base import Appliance
from pydaikin.daikin_brp069 import DaikinBRP069
from pydaikin.factory import DaikinFactory

IP = os.environ["DAIKIN_IP"]  # e.g. DAIKIN_IP=192.168.x.y
CTRL = "aircon/get_control_info"
KEYS = ("pow", "mode", "stemp", "shum", "f_rate", "f_dir", "adv", "dt4", "dt5", "b_stemp")
WARNINGS = []


class Collect(logging.Handler):
    def emit(self, record):
        WARNINGS.append(record.getMessage())


logging.getLogger("pydaikin").addHandler(Collect(level=logging.WARNING))

ORIGINAL = Appliance._do_get_resource
SNAP = {}
LOG = []  # (kind, path, stemp, note)
INJ = {"reject_half": False, "empty_read": 0, "lost_answer": False, "fake_readback": None}


async def guarded(self, path, params, strict=False):
    is_write = "/set" in path or path.startswith("common/notify") or "register" in path
    if is_write:
        fixed = {k: v for k, v in params.items() if k != "stemp"}
        if path != "aircon/set_control_info" or fixed != {k: SNAP[k] for k in fixed if k in SNAP} or len(fixed) != len([k for k in fixed if k in SNAP]):
            LOG.append(("BLOCKED", path, params.get("stemp"), str(params)))
            raise RuntimeError(f"GUARD blocked {path} {params}")
        stemp = params["stemp"]
        if INJ["reject_half"] and float(stemp) % 1:
            LOG.append(("W", path, stemp, "PARAM NG injected, not sent"))
            from pydaikin.exceptions import DaikinCommandRejected

            raise DaikinCommandRejected("PARAM NG")
        if not 22 <= float(stemp) <= 23:
            LOG.append(("BLOCKED", path, stemp, "out of range"))
            raise RuntimeError(f"GUARD stemp out of range {stemp}")
        try:
            result = await ORIGINAL(self, path, params, strict=strict)
        except Exception as exc:
            LOG.append(("W", path, stemp, f"device: {exc}"))
            raise
        LOG.append(("W", path, stemp, "OK sent"))
        if INJ["lost_answer"]:
            LOG.append(("W", path, stemp, "answer dropped (injected TimeoutError)"))
            raise asyncio.TimeoutError()
        return result
    if path == CTRL and INJ["empty_read"]:
        INJ["empty_read"] -= 1
        LOG.append(("R", path, None, "ret=NG injected"))
        return {}
    result = await ORIGINAL(self, path, params, strict=strict)
    if path == CTRL and INJ["fake_readback"] is not None and any(e[0] == "W" for e in LOG):
        result = {**result, "stemp": INJ["fake_readback"]}
        INJ["fake_readback"] = None
        LOG.append(("R", path, result["stemp"], "readback faked"))
    else:
        LOG.append(("R", path, result.get("stemp"), ""))
    return result


Appliance._do_get_resource = guarded

RESULTS = []


async def device_stemp(session):
    raw = DaikinBRP069(IP, session=session)
    state = await ORIGINAL(raw, CTRL, {})
    return state


async def case(session, name, coro, expect_exc, check):
    LOG.clear()
    WARNINGS.clear()
    t0 = time.monotonic()
    try:
        await coro()
        exc = None
    except Exception as err:  # noqa: BLE001
        exc = err
    dt = time.monotonic() - t0
    for k in INJ:
        INJ[k] = 0 if k == "empty_read" else (None if k == "fake_readback" else False)
    state = await device_stemp(session)
    writes = [(s, n) for kind, _, s, n in LOG if kind == "W"]
    blocked = [e for e in LOG if e[0] == "BLOCKED"]
    exc_name = type(exc).__name__ if exc else None
    ok_exc = exc_name == expect_exc
    try:
        ok_check, detail = check(exc, writes, state)
    except Exception as err:  # noqa: BLE001
        ok_check, detail = False, f"check crashed: {err!r}"
    ok = ok_exc and ok_check and not blocked and state["pow"] == SNAP["pow"] and state["mode"] == SNAP["mode"]
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name} ({dt:.2f}s)")
    print(f"     exc={exc_name} (want {expect_exc}) writes={writes} reads={sum(1 for e in LOG if e[0]=='R')}")
    print(f"     device: pow={state['pow']} mode={state['mode']} stemp={state['stemp']} | {detail}")
    if WARNINGS:
        print(f"     log: {WARNINGS}")
    if blocked:
        print(f"     GUARD BLOCKED: {blocked}")


async def main():
    async with ClientSession() as session:
        dev0 = DaikinBRP069(IP, session=session)
        SNAP.update(await ORIGINAL(dev0, CTRL, {}))
        print("snapshot:", {k: SNAP.get(k) for k in KEYS})
        if SNAP.get("mode") != "4":
            sys.exit("ABORT: expected heat mode")  # pow stays as found (guard)

        try:
            await run_cases(session)
        finally:
            INJ.update({"reject_half": False, "empty_read": 0, "lost_answer": False, "fake_readback": None})
            restore = DaikinBRP069(IP, session=session)
            await restore.set({"stemp": SNAP["stemp"]})
            final = await ORIGINAL(dev0, CTRL, {})
            diff = {k: (SNAP.get(k), final.get(k)) for k in KEYS if SNAP.get(k) != final.get(k)}
            print("restored:", {k: final.get(k) for k in KEYS})
            print("DIFF vs snapshot:", diff or "none")
            print(f"SUMMARY: {sum(RESULTS)}/{len(RESULTS)} passed")


async def run_cases(session):
    if True:
        # L1: HA path through the factory, init must not write
        holder = {}

        async def l1():
            holder["dev"] = await DaikinFactory(IP, session)

        await case(session, "L1 DaikinFactory + init: no writes, optimistic 0.5", l1, None,
                   lambda e, w, s: (not w and type(holder["dev"]).__name__ == "DaikinBRP069"
                                    and holder["dev"].target_temperature_step == 0.5,
                                    f"class={type(holder.get('dev')).__name__} step={holder['dev'].target_temperature_step} status={holder['dev'].target_temperature_step_status}"))
        dev = holder["dev"]

        def st(expected):
            return lambda e, w, s: (dev.target_temperature_step_status == expected,
                                    f"status={dev.target_temperature_step_status} step={dev.target_temperature_step} cache={dev.values.get('stemp', invalidate=False)}")

        await case(session, "L2 22.5 real: confirmed", lambda: dev.set({"stemp": "22.5"}), None,
                   lambda e, w, s: (w == [("22.5", "OK sent")] and s["stemp"] == "22.5" and dev.target_temperature_step_status == "confirmed", st("confirmed")(e, w, s)[1]))

        # L3: immediate read-back is consistent (QS-04 evidence), 5 cycles
        mism = []

        async def l3():
            for _ in range(5):
                await dev.set({"stemp": "22"})
                await dev.set({"stemp": "22.5"})
                if dev.values.get("stemp", invalidate=False) != "22.5":
                    mism.append(dev.values.get("stemp", invalidate=False))

        await case(session, "L3 5x 22 -> 22.5, immediate read-back always matches", l3, None,
                   lambda e, w, s: (not mism and len(w) == 10, f"mismatches={mism} status={dev.target_temperature_step_status}"))

        await case(session, "L4 22.2 real: PARAM NG from device, no retry", lambda: dev.set({"stemp": "22.2"}), "DaikinCommandRejected",
                   lambda e, w, s: (len(w) == 1 and s["stemp"] == "22.5" and dev.values.get("stemp", invalidate=False) == "22.5",
                                    f"cache={dev.values.get('stemp', invalidate=False)} status={dev.target_temperature_step_status}"))

        await case(session, "L5 22 whole degree: one write, no read-back", lambda: dev.set({"stemp": "22"}), None,
                   lambda e, w, s: (w == [("22", "OK sent")] and sum(1 for x in LOG if x[0] == "R") == 1, f"status={dev.target_temperature_step_status}"))

        dev.target_temperature_step_status = "unknown"

        async def l6():
            INJ["reject_half"] = True
            await dev.set({"stemp": "22.5"})

        await case(session, "L6 whole-degree adapter simulated: fallback 22.5 -> 23", l6, None,
                   lambda e, w, s: (w == [("22.5", "PARAM NG injected, not sent"), ("23", "OK sent")] and s["stemp"] == "23.0"
                                    and dev.target_temperature_step == 1.0, st("inferred")(e, w, s)[1]))

        dev.target_temperature_step_status = "unknown"

        async def l7():
            INJ["reject_half"] = True
            dev.whole_degree_fallback = False
            try:
                await dev.set({"stemp": "22.5"})
            finally:
                dev.whole_degree_fallback = True

        await case(session, "L7 fallback disabled: rejection raised, nothing sent", l7, "DaikinCommandRejected",
                   lambda e, w, s: (w == [("22.5", "PARAM NG injected, not sent")] and s["stemp"] == "23.0", st("unknown")(e, w, s)[1]))

        await case(session, "L8a 22.5 real: confirmed again", lambda: dev.set({"stemp": "22.5"}), None, st("confirmed"))

        async def l8b():
            INJ["reject_half"] = True
            await dev.set({"stemp": "22.5"})

        await case(session, "L8b reject after confirmed: no fallback", l8b, "DaikinCommandRejected",
                   lambda e, w, s: (len(w) == 1 and dev.target_temperature_step_status == "confirmed", st("confirmed")(e, w, s)[1]))

        dev.target_temperature_step_status = "unknown"

        async def l9():
            INJ["reject_half"] = True
            await dev.set({"stemp": "17.5"})

        await case(session, "L9 17.5 rejected (outside 18-30): no fallback", l9, "DaikinCommandRejected",
                   lambda e, w, s: (len(w) == 1 and s["stemp"] == "22.5", st("unknown")(e, w, s)[1]))

        async def l10():
            INJ["empty_read"] = 1
            await dev.set({"stemp": "23"})

        before = {}

        async def l10_wrap():
            before.update(dict(dev.values))
            await l10()

        await case(session, "L10 pre-read ret=NG simulated: no write, cache unchanged", l10_wrap, "DaikinException",
                   lambda e, w, s: (not w and dict(dev.values) == before and s["stemp"] == "22.5", f"cache unchanged={dict(dev.values) == before}"))

        async def l11():
            INJ["lost_answer"] = True
            await dev.set({"stemp": "23"})

        await case(session, "L11 answer lost after real write: resync shows applied 23", l11, "TimeoutError",
                   lambda e, w, s: (w == [("23", "OK sent"), ("23", "answer dropped (injected TimeoutError)")]
                                    and dev.values.get("stemp", invalidate=False) == "23.0" and s["stemp"] == "23.0",
                                    f"cache={dev.values.get('stemp', invalidate=False)}"))

        async def l12():
            INJ["fake_readback"] = "23.0"
            await dev.set({"stemp": "22.5"})

        await case(session, "L12 read-back differs (faked 23.0): DaikinVerificationError", l12, "DaikinVerificationError",
                   lambda e, w, s: (w == [("22.5", "OK sent")] and dev.values.get("stemp", invalidate=False) == "23.0"
                                    and (e.requested, e.actual) == ("22.5", "23.0"),
                                    f"err={e} cache={dev.values.get('stemp', invalidate=False)} status={dev.target_temperature_step_status}"))

        async def l12b():
            await dev.update_status([CTRL], force_refresh=True)

        await case(session, "L12b next poll corrects the cache to the device value", l12b, None,
                   lambda e, w, s: (dev.values.get("stemp", invalidate=False) == s["stemp"] == "22.5", f"cache={dev.values.get('stemp', invalidate=False)}"))

        async def l13():
            await dev.set({"stemp": "22"})
            LOG.clear()
            await asyncio.gather(dev.set({"stemp": "22.5"}), dev.update_status([CTRL], force_refresh=True))

        await case(session, "L13 set() and poll concurrently: transaction not interleaved", l13, None,
                   lambda e, w, s: ([(k, sv) for k, _, sv, _ in LOG] == [("R", "22.0"), ("W", "22.5"), ("R", "22.5"), ("R", "22.5")],
                                    f"order={[(k, sv) for k, _, sv, _ in LOG]}"))



asyncio.run(main())
