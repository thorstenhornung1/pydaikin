"""Mutation check per layer: each guarded behaviour must make the tests fail.

Usage (repo root, layer A or B checked out): python qa/mutate_layers.py A|B
"""
import pathlib, subprocess
def run():
    r = subprocess.run([".venv/bin/pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS], capture_output=True, text=True, timeout=300)
    return r.returncode
A = [
 ("no lock in set()", "pydaikin/daikin_brp069.py", "        async with self._control_lock:\n            current_val = await self._update_settings(settings)", "        if True:\n            current_val = await self._update_settings(settings)"),
 ("no lock in update_status()", "pydaikin/daikin_brp069.py", "        async with self._control_lock:\n            if force_refresh:", "        if True:\n            if force_refresh:"),
 ("tenacity retries writes", "pydaikin/daikin_base.py", '    return not retry_state.kwargs.get("strict", False)', "    return True"),
 ("404 counts as success", "pydaikin/daikin_base.py", "if response.status == 404 and not strict:", "if response.status == 404:"),
 ("strict ignores ret", "pydaikin/response.py", "            raise DaikinCommandRejected(ret)", "            pass"),
 ("no pre-read check", "pydaikin/daikin_brp069.py", "        if missing := [k for k in self.CONTROL_KEYS if k not in state]:", "        if missing := []:"),
 ("no restore on rejection", "pydaikin/daikin_brp069.py", "            self._restore_control_info(current_val)\n            raise", "            raise"),
 ("keep stemp after unknown", "pydaikin/daikin_brp069.py", '                    self.values.pop("stemp", None)', "                    pass"),
 ("no resync", "pydaikin/daikin_brp069.py", "                    await self._resync_control_info()", "                    pass"),
 ("resync not time-boxed", "pydaikin/daikin_brp069.py", "asyncio.timeout(self.RESYNC_TIMEOUT)", "asyncio.timeout(None)"),
 ("no invalidate on restore", "pydaikin/daikin_brp069.py", "        self.values.update_by_resource(resource, current_val)\n        self.values.invalidate_resource(resource)\n", "        self.values.update_by_resource(resource, current_val)\n"),
 ("AirBase requires shum", "pydaikin/daikin_airbase.py", '    CONTROL_KEYS = ("pow", "mode", "stemp")\n', ""),
]
B = [m if m[0] != "no restore on rejection" else ("no restore on rejection", "pydaikin/daikin_brp069.py", "            self._restore_control_info(current_val)\n            if not self._may_fall_back", "            if not self._may_fall_back") for m in A] + [
 ("no mode-change check", "pydaikin/daikin_brp069.py", '        if state["mode"] != mode:\n', "        if False:\n"),
 ("number in fan/dry confirms", "pydaikin/daikin_brp069.py", "        return not no_setpoint\n", "        return True\n"),
 ("non-numeric ok in heat", "pydaikin/daikin_brp069.py", "        if _temperature(actual) is None and no_setpoint:", "        if _temperature(actual) is None:"),
 ("fallback allowed in fan/dry", "pydaikin/daikin_brp069.py", '            or current_val.get("mode") in self.NO_SETPOINT_MODES\n', ""),
 ("downgrade after confirmed", "pydaikin/daikin_brp069.py", '            or self._step_status == "confirmed"\n', ""),
 ("fallback on any ret", "pydaikin/daikin_brp069.py", '            or rejection.ret != "PARAM NG"\n', ""),
 ("fallback for mode+stemp", "pydaikin/daikin_brp069.py", '            or settings.keys() != {"stemp"}\n', ""),
 ("no range guard", "pydaikin/daikin_brp069.py", "            and low < _temperature(stemp) < high\n", "            and True\n"),
 ("retry changes pow", "pydaikin/daikin_brp069.py", '{**params, "stemp": stemp}', '{**params, "stemp": stemp, "pow": "1"}'),
 ("banker rounding", "pydaikin/daikin_brp069.py", "rounding=ROUND_HALF_UP", "rounding='ROUND_HALF_EVEN'"),
 ("no state re-check", "pydaikin/daikin_brp069.py", "        if any(state.get(k) != current_val.get(k) for k in params if k in current_val):", "        if False:"),
 ("infer without read-back", "pydaikin/daikin_brp069.py", "        if not await self._verify_stemp(stemp, params[\"mode\"]):\n            raise rejection\n", ""),
 ("status setter unchecked", "pydaikin/daikin_brp069.py", "        if status not in self.TARGET_TEMPERATURE_STEP_STATUSES:", "        if False:"),
]
import sys
layer = sys.argv[1]
TESTS = ["tests/test_brp069_control_write.py"] + (["tests/test_brp069_temperature_step.py"] if layer == "B" else [])
assert run() == 0, "baseline must be green"
killed = 0
for name, f, old, new in (A if layer == "A" else B):
    p = pathlib.Path(f); orig = p.read_text()
    assert orig.count(old) == 1, name
    p.write_text(orig.replace(old, new))
    try:
        rc = run()
    except subprocess.TimeoutExpired:
        rc = "hang"
    p.write_text(orig)
    killed += rc != 0
    print(f"  {name:28} -> {'KILLED' if rc else 'SURVIVED'}")
print(f"  layer {layer}: {killed}/{len(A if layer == 'A' else B)} killed")
