"""
optimised/ - the production analytics package
=============================================
Plain-Python re-implementation of the project's MOST USEFUL analytics,
extracted from notebooks 05/06/07/08 and rebuilt for speed. The dashboard
imports from here instead of depending on notebook outputs for its charts.

Division of labour (why this is fast):
  - The HEAVY, slow steps (ticker extraction, sentiment scoring) still run
    in the pipeline (run_daily.py -> notebooks 01/02/06/07) and land in
    small daily parquets. Nothing here re-does them.
  - THIS package does the light maths (rolling sums, trailing z-scores,
    pivots) on those small daily tables - fully vectorised pandas, no
    Python loops over rows - and builds the charts. Typical call: a few
    milliseconds; a full dashboard render touches < 5 MB of parquet.

Modules (they call each other, bottom-up):
  data.py    - cached parquet loaders + the ticker->theme rollup
  metrics.py - trailing z, velocity, momentum stats, conviction, heatmap
  charts.py  - Altair chart builders (take-offs, sentiment, momentum map,
               conviction heat) used by the dashboard

The notebooks remain the RESEARCH environment (full narrative, tunable
parameters, matplotlib annotations); this package is the SERVING layer.
Keep any logic change mirrored in both, or better: change it here and
import it from the notebook.
"""

from . import data, metrics, charts  # noqa: F401
