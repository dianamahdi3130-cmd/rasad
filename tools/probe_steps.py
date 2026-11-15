"""Turns the "steps" of tools/probe-request.json into commands; the diagnosis workflow runs them
one after the other (each step's output is kept as probe-out/stepN.log). Leading NAME=value items
of a step are set in that step's environment (e.g. RASAD_BACKGROUND_MEDIA=1 to run a check the
way the watching loop does)."""
import json
import re
import shlex

r = json.load(open("tools/probe-request.json", encoding="utf-8"))
for i, step in enumerate(r.get("steps", []), 1):
    step = [str(a) for a in step]
    env = []
    while step and re.fullmatch(r"[A-Z][A-Z0-9_]*=[^\s]*", step[0]):
        env.append(shlex.quote(step.pop(0)))
    args = " ".join(shlex.quote(a) for a in step)
    prefix = ("env " + " ".join(env) + " ") if env else ""
    print(f"{prefix}python -m rasad --state _state --site /tmp/probe-site {args} 2>&1 | tee probe-out/step{i}.log")
