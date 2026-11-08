"""Run the actual shell coordinator with offline workers and publishing commands."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class LoopRuntimeTests(unittest.TestCase):
    def test_scanning_is_bounded_and_feed_publishes_before_map_maintenance(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "loop.sh"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            commands = root / "bin"
            commands.mkdir()
            logfile = root / "calls.txt"
            helpers = {
                "python": '''#!/usr/bin/env python3
import os,sys
with open(os.environ['CALLS'], 'a') as out: out.write('PYTHON '+' '.join(sys.argv[1:])+'\\n')
''',
                "timeout": '''#!/usr/bin/env python3
import os,sys
with open(os.environ['CALLS'], 'a') as out: out.write('TIMEOUT '+' '.join(sys.argv[1:])+'\\n')
os.execvp(sys.argv[4], sys.argv[4:])
''',
                "bash": '''#!/usr/bin/env python3
import os,sys
with open(os.environ['CALLS'], 'a') as out: out.write('PUBLISH '+' '.join(sys.argv[1:])+'\\n')
''',
                "git": '''#!/usr/bin/env python3
import sys,time
args=sys.argv[1:]
if args==['rev-parse','HEAD']: print('old')
elif args==['rev-parse','FETCH_HEAD']: print('new')
elif args[:2]==['diff','--quiet']: time.sleep(.1); sys.exit(1)
''',
            }
            for name, content in helpers.items():
                target = commands / name
                target.write_text(content)
                target.chmod(0o755)
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                       CALLS=str(logfile), RASAD_LOOP_MINUTES="1", RASAD_EVERY_SECONDS="150",
                       RASAD_SCAN_TIMEOUT_SECONDS="73", RASAD_GEO_TIMEOUT_SECONDS="17")
            env.pop("RASAD_CLOUD_URL", None)
            result = subprocess.run(["/bin/bash", str(script)], cwd=root, env=env,
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = logfile.read_text().splitlines()
            scan = next(line for line in calls if line.startswith("PYTHON") and " cycle" in line)
            self.assertNotIn("--auto-geo", scan)
            self.assertTrue(any("TIMEOUT --signal=TERM --kill-after=5s 73s env" in line for line in calls))
            self.assertTrue(any("media-worker --interval 2 --max-minutes 1" in line for line in calls))
            publish = next(i for i, line in enumerate(calls) if line.endswith("save-live"))
            maintenance = next(i for i, line in enumerate(calls) if line.startswith("PYTHON") and "maintain-geo" in line)
            self.assertLess(publish, maintenance)
            self.assertIn("new code on main: stopping", result.stdout)


if __name__ == "__main__":
    unittest.main()
