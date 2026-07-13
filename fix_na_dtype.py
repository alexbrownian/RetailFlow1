# fix_na_dtype.py (one-shot fix, delete after running)
# The share-normalisation must never touch pd.NA: dividing by
# totals.replace(0, pd.NA) creates an object-dtype series that neither
# rolling/resample nor astype can handle ("float() argument must be ...
# not NAType"). totals.where(totals > 0) keeps everything float64
# (NaN where zero). Patches notebooks 11, 12 and 13 in place.
import json

V0 = """        m = (m / totals.replace(0, pd.NA)) * 100
        m[totals < MIN_TOTAL] = pd.NA"""
V1 = """        # float('nan') keeps the series numeric (pd.NA would flip it to
        # object dtype, which rolling/resample refuse to aggregate)
        m = ((m / totals.replace(0, pd.NA)) * 100).astype('float64')
        m[totals < MIN_TOTAL] = float('nan')"""
V2 = """        # keep everything float64: where() puts NaN where totals is zero,
        # so no pd.NA ever enters the series (rolling/resample need floats)
        totals = totals.astype('float64')
        m = (m.astype('float64') / totals.where(totals > 0)) * 100
        m[totals < MIN_TOTAL] = float('nan')"""

for nb in ["11_overlay_ticker_mentions", "12_overlay_ticker_first_derivative",
           "13_overlay_theme_first_derivative"]:
    path = f"notebooks/{nb}.ipynb"
    d = json.load(open(path, encoding="utf-8"))
    hits = 0
    for c in d["cells"]:
        if c["cell_type"] != "code":
            continue
        s = "".join(c["source"])
        for old in (V1, V0):                 # newer pattern first
            if old in s:
                s = s.replace(old, V2, 1)
                c["source"] = s.splitlines(keepends=True)
                hits += 1
                break
    if hits:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, ensure_ascii=False)
            f.write("\n")
        print(f"fixed {nb}")
    else:
        print(f"already on v2 (or pattern not found): {nb}")
print("done - delete this file afterwards.")
