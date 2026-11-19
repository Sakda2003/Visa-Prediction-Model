"""Versioned, deterministic financial policy. All money is integer minor units."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

D = Decimal
DEFAULT_CONFIG = {
    "version": "1.0",
    "anchors": [[0, 0], [50000, 10], [100000, 20], [200000, 30], [300000, 40],
                [400000, 45], [500000, 55], [700000, 70], [1000000, 85], [1500000, 100]],
    "weights": {"average": "0.35", "ending": "0.20", "floor": "0.15", "flow": "0.15", "established": "0.15"},
    "flow_neutral": 50, "flow_multiplier": 250,
    "cap": {"ending_min": 400000, "average_max": 200000, "uplift_min": "0.50", "score_max": "5.9"},
    "minimum_days": 30, "final_days": 60, "reference_days": 30, "excluded_final_days": 7,
    "labels": [["2.9", "Very weak"], ["4.9", "Weak"], ["6.4", "Average"],
               ["7.9", "Good"], ["8.9", "Strong"], ["10", "Exceptional"]],
}


def validate_config(config):
    c = deepcopy(config)
    if set(c["weights"]) != set(DEFAULT_CONFIG["weights"]) or sum(D(str(w)) for w in c["weights"].values()) != 1:
        raise ValueError("The five component weights must sum to exactly 1.")
    if any(D(str(w)) < 0 for w in c["weights"].values()):
        raise ValueError("Weights cannot be negative.")
    anchors = c["anchors"]
    if len(anchors) < 2 or anchors[0] != [0, 0] or anchors[-1][1] != 100:
        raise ValueError("Balance anchors must start at [0,0] and end at 100 points.")
    if any(a[0] >= b[0] or a[1] > b[1] or not 0 <= a[1] <= 100 for a, b in zip(anchors, anchors[1:])):
        raise ValueError("Balance anchors must increase monotonically.")
    if not 1 <= int(c["excluded_final_days"]) < int(c["reference_days"]) <= int(c["minimum_days"]) <= int(c["final_days"]):
        raise ValueError("History windows must be ordered: excluded < reference <= minimum <= final.")
    if not 1 <= D(str(c["cap"]["score_max"])) <= 10 or not 0 <= D(str(c["cap"]["uplift_min"])) <= 1:
        raise ValueError("Cap values are invalid.")
    if not c.get("version") or not c["labels"] or D(c["labels"][-1][0]) != 10:
        raise ValueError("A model version and labels ending at 10 are required.")
    if any(D(a[0]) >= D(b[0]) for a, b in zip(c["labels"], c["labels"][1:])):
        raise ValueError("Score label boundaries must increase.")
    if not 0 <= D(str(c["flow_neutral"])) <= 100 or D(str(c["flow_multiplier"])) < 0:
        raise ValueError("Cash flow settings are invalid.")
    return c


def points(value, config=DEFAULT_CONFIG):
    x = D(str(value))
    if x <= 0:
        return D(0)
    for (a, sa), (b, sb) in zip(config["anchors"], config["anchors"][1:]):
        if x <= b:
            return D(sa) + (x-a) / (b-a) * (sb-sa)
    return D(100)


def score(metrics, config=DEFAULT_CONFIG):
    c = validate_config(config)
    if metrics["days"] < c["minimum_days"]:
        return {"value": None, "label": "Insufficient history for standard score", "components": [], "config": c}
    a, e, p = D(str(metrics["average"])), D(metrics["ending"]), D(metrics["p20"])
    i, o = D(metrics["money_in"]), D(metrics["money_out"])
    s = max(D(0), min(e, D(str(metrics["reference_median"]))))
    u = max(D(0), min(D(1), (e-s)/max(e, D(100))))
    flow = D(str(c["flow_neutral"])) if max(i, o) == 0 else max(D(0), min(D(100), D(str(c["flow_neutral"])) + D(str(c["flow_multiplier"]))*(i-o)/max(i, o)))
    values = {"average": a, "ending": e, "floor": p, "flow": i-o, "established": s}
    labels = {"average": "Average daily balance", "ending": "Ending balance", "floor": "Balance floor (20th percentile)", "flow": "Cash flow", "established": "Established-balance proxy"}
    parts = []
    total = D(0)
    for key, weight in c["weights"].items():
        pts = flow if key == "flow" else points(values[key], c)
        contribution = pts * D(str(weight))
        total += contribution
        parts.append({"key": key, "name": labels[key], "metric": str(values[key]), "points": float(pts), "weight": float(D(str(weight))), "contribution": float(contribution)})
    raw = D(1) + D(9)*total/100
    before_cap = raw
    reasons = []
    if a <= 0 and e <= 0:
        raw = D(1)
        reasons.append("No positive average or ending balance.")
    cap = c["cap"]
    if e >= cap["ending_min"] and a < cap["average_max"] and u >= D(str(cap["uplift_min"])):
        raw = min(raw, D(str(cap["score_max"])))
        reasons.append("Ending balance is materially above the sustained balance; recent funds need more history.")
    value = max(D(1), min(D(10), raw)).quantize(D('.1'), rounding=ROUND_HALF_UP)
    label = next(label for ceiling, label in c["labels"] if value <= D(ceiling))
    return {"value": float(value), "label": label, "provisional": metrics["days"] < c["final_days"],
            "weighted_points": float(total), "raw_score": str(before_cap), "caps": reasons,
            "established": str(s), "uplift_share": float(u), "components": parts, "config": c}
