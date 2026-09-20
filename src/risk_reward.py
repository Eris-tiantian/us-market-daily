"""Reference entry at the latest close, with no extrapolated price target."""
from __future__ import annotations

import numpy as np


def calculate_risk_reward(price: float, levels: dict, cfg: dict | None = None) -> dict:
    rc = (cfg or {}).get("rr", {})
    buffer = float(rc.get("stop_buffer", .01))
    support, target = levels.get("support", np.nan), levels.get("resistance", np.nan)
    stop = float(support * (1. - buffer)) if np.isfinite(support) else np.nan
    risk = price - stop if np.isfinite(stop) else np.nan
    reward = target - price if np.isfinite(target) else np.nan
    valid = np.isfinite(risk) and np.isfinite(reward) and 0 < stop < price < target
    return {"entry": price, "entry_low": price, "entry_high": price,
            "invalidation": stop, "target": target, "risk": risk, "reward": reward,
            "rr": float(reward / risk) if valid else np.nan,
            "rr_status": "可计算" if valid else "RR 无法可靠计算"}
