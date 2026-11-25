"""The closer look (twopanel.render) on what the steady rule tells: a spot N levels above the sea in
EVERY picture (lit before dark), inc['first'] = the earlier of the two back-filled pictures."""
import copy, datetime as dt, os, tempfile
import numpy as np
from PIL import Image
from rasad import engine, twopanel
from rasad.clip import NotVisible

UTC = dt.timezone.utc
t_end = dt.datetime(2026, 11, 25, 17, 0, tzinfo=UTC)
first = t_end - dt.timedelta(minutes=20)

def look(above, size=3):
    cfg = copy.deepcopy(engine.DEFAULTS)
    class W:
        def getmap(self, name, bbox, w, h, t=None, style="", quiet=True, timeout=None):
            a = np.full((h, w, 4), 30, np.uint8)
            a[..., 3] = 255
            a[..., 0] += (np.arange(w)[None, :] % 2).astype(np.uint8) * int((t.minute // 10) % 2)  # pictures differ a little
            r, c = h // 2, w // 2
            a[r - size // 2:r + size // 2 + 1, c - size // 2:c + size // 2 + 1, 0] = 30 + above
            return Image.fromarray(a), t
    with tempfile.TemporaryDirectory() as folder:
        cfg["_site_dir"] = folder
        layer = {"name": "x", "cadence_min": 10, "channel": 0, "hot_is_bright": True}
        info = {"first": first.strftime("%Y-%m-%dT%H:%M:%SZ"), "place": "sea", "where": "في البحر قرب مضيق هرمز",
                "verdict": "مرجّح"}
        try:
            twopanel.render(cfg, W(), 26.3289, 56.2312, t_end, os.path.join(folder, "clips", "t"), layer, None, 8, info)
            return "shown (video made)"
        except NotVisible as e:
            return f"NotVisible: {e}"
        except Exception as e:
            return f"{type(e).__name__}: {e}"

for above in (8, 10, 15, 21, 24, 26, 40):
    print(f"steady spot {above:>2} above the sea in every picture -> closer look: {look(above)}")
