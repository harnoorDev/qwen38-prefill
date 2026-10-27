#!/usr/bin/env python3
"""Re-score the committed prefill samples without a GPU, model, or download.

Reads the ``data/samples_<tag>.jsonl`` files produced by ``sample_repro.py`` and
recomputes, per control point, the URL / Aliyun / routify-host rates using the
single canonical classifier in ``repro_common.classify``. This lets a reviewer
verify the published numbers from the committed data alone -- standard library
only, no ``pyarrow``/``tokenizers``/``requests`` and no running model.

It also surfaces two things the original summaries did not:

* ``err``   -- records whose text is a ``__ERROR__`` sampling failure. The
  original classifier bucketed these as ``no_url``, slightly inflating it;
  here they are counted separately and excluded from the rates.
* ``drift`` -- completions whose stored ``cat`` label differs from the current
  classifier. This is almost entirely the historical
  ``url_example_placeholder`` -> ``url_placeholder`` rename, and is reported so
  the discrepancy is explicit rather than silent.

By default it prints a table and writes nothing. Pass ``--write`` to regenerate
``data/summary_<tag>.json`` from the samples with the canonical classifier.

Usage:
  python analyze.py                 # re-score every data/samples_*.jsonl
  python analyze.py n1000 n10000    # only these tags
  python analyze.py --write n1000   # also rewrite data/summary_n1000.json
"""
import argparse
import glob
import json
import math
import os
from collections import Counter, defaultdict

from repro_common import classify

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def wilson(k, n, z=1.96):
    """95% Wilson score interval for a proportion, returned as (lo%, hi%)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(100 * (centre - half), 2), round(100 * (centre + half), 2))


def analyze_file(path):
    points = defaultdict(lambda: {"cats": Counter(), "hosts": Counter(), "errors": 0, "drift": 0})
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            pt = points[rec["point"]]
            text = rec.get("text", "") or ""
            if text.startswith("__ERROR__"):
                pt["errors"] += 1
                continue
            cat, host = classify(text)
            pt["cats"][cat] += 1
            if host:
                pt["hosts"][host] += 1
            if "cat" in rec and rec["cat"] != cat:
                pt["drift"] += 1
    return points


def summarize(points):
    out = {}
    for name, pt in points.items():
        cats, hosts = pt["cats"], pt["hosts"]
        n = sum(cats.values())
        if n == 0:
            continue
        n_url = sum(v for k, v in cats.items() if k.startswith("url_"))
        n_ali = cats["url_aliyun"] + cats["url_routify"]
        n_rout = sum(v for h, v in hosts.items() if "routify" in h)
        out[name] = {
            "n": n,
            "errors": pt["errors"],
            "label_drift": pt["drift"],
            "url_pct": round(100 * n_url / n, 2),
            "aliyun_pct": round(100 * n_ali / n, 2),
            "aliyun_ci95": list(wilson(n_ali, n)),
            "routify_host_pct": round(100 * n_rout / n, 2),
            "cats": dict(cats),
            "top_hosts": hosts.most_common(10),
        }
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tags", nargs="*", help="run tags to analyze (default: all under data/)")
    ap.add_argument("--write", action="store_true",
                    help="(re)write data/summary_<tag>.json from the samples")
    args = ap.parse_args()

    if args.tags:
        paths = [os.path.join(DATA, f"samples_{t}.jsonl") for t in args.tags]
    else:
        paths = sorted(glob.glob(os.path.join(DATA, "samples_*.jsonl")))

    if not paths:
        raise SystemExit(f"no samples_*.jsonl found under {DATA}")

    for path in paths:
        if not os.path.exists(path):
            print(f"!! missing: {path}")
            continue
        tag = os.path.basename(path)[len("samples_"):-len(".jsonl")]
        summary = summarize(analyze_file(path))
        print(f"\n=== {os.path.relpath(path, os.path.dirname(DATA))}  (tag={tag})")
        header = f"{'point':32s} {'n':>6} {'url%':>7} {'aliyun%':>8} {'aliyun 95% CI':>15} {'routify%':>9} {'err':>4} {'drift':>6}"
        print(header)
        print("-" * len(header))
        for name, s in summary.items():
            ci = f"{s['aliyun_ci95'][0]}-{s['aliyun_ci95'][1]}"
            print(f"{name:32s} {s['n']:6d} {s['url_pct']:7.2f} {s['aliyun_pct']:8.2f} "
                  f"{ci:>15} {s['routify_host_pct']:9.2f} {s['errors']:4d} {s['label_drift']:6d}")
        if args.write:
            dst = os.path.join(DATA, f"summary_{tag}.json")
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            print(f"   wrote {os.path.relpath(dst, os.path.dirname(DATA))}")


if __name__ == "__main__":
    main()
