"""A4: human-marshal baseline timing model.

Delay from the incident until the flag is out:
    see it  (fast if the post can see the spot, slow on a blind crest)
  + marshal reaction  0.8-2.5 s
  + flag deployment   0.5-1.5 s
Used by the dashboard timeline and by the Monte Carlo, so it lives in one place.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from flagzero import config


def sample(n: Optional[int] = None, rng: Optional[np.random.Generator] = None,
           visibility: float = config.MARSHAL_VISIBILITY,
           react_s: tuple[float, float] = config.MARSHAL_REACT_S,
           flag_s: tuple[float, float] = config.MARSHAL_FLAG_S) -> dict:
    """Sample marshal delays. n=None gives scalars, otherwise numpy arrays.

    Returns {"see", "react", "flag", "total", "visible"} in seconds.
    """
    rng = rng or np.random.default_rng()
    size = 1 if n is None else n
    visible = rng.random(size) < visibility
    see = np.where(visible,
                   rng.uniform(*config.MARSHAL_SEE_VISIBLE_S, size),
                   rng.uniform(*config.MARSHAL_SEE_BLIND_S, size))
    react = rng.uniform(*react_s, size)
    flag = rng.uniform(*flag_s, size)
    out = {"see": see, "react": react, "flag": flag, "total": see + react + flag, "visible": visible}
    if n is None:
        out = {k: (bool(v[0]) if k == "visible" else float(v[0])) for k, v in out.items()}
    return out


def typical(visibility: float = config.MARSHAL_VISIBILITY) -> dict:
    """Median of each stage, for drawing one representative timeline."""
    s = sample(20_000, np.random.default_rng(0), visibility=visibility)
    return {k: float(np.median(s[k])) for k in ("see", "react", "flag", "total")}
