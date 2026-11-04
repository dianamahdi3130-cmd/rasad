"""Turns the "steps" of tools/probe-request.json into commands; the diagnosis workflow runs them
one after the other (each step's output is kept as probe-out/stepN.log)."""
import json
import shlex

r = json.load(open("tools/probe-request.json", encoding="utf-8"))
for i, step in enumerate(r.get("steps", []), 1):
    args = " ".join(shlex.quote(str(a)) for a in step)
    print(f"python -m rasad --state _state --site /tmp/probe-site {args} 2>&1 | tee probe-out/step{i}.log")
