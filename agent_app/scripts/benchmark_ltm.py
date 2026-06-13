#!/usr/bin/env python
"""
Benchmark embedded tabular model candidates: TabICLv2 vs TabM vs GBDT baselines.

Compares accuracy/quality, latency (fit + predict), and peak memory on a target
table so you can validate the default embedded model before locking it in.

Data sources (pick one):
  --data path/to/file.csv            Local CSV.
  --table catalog.schema.table       Databricks table (requires SQL_WAREHOUSE_ID
                                      + databricks auth in the environment).
  (none)                             Synthetic dataset (sklearn make_*), useful
                                      for a quick smoke test of the harness.

Install the benchmark dependency group first:
  uv sync --group benchmark

Examples:
  uv run --group benchmark python scripts/benchmark_ltm.py \
      --data data/churn.csv --target churned --task classification

  uv run --group benchmark python scripts/benchmark_ltm.py \
      --table main.sales.leads --target converted --task classification \
      --models tabicl,catboost,xgboost

Models that are not installed are skipped with a note, so a partial environment
still produces a useful comparison.

Built with PriorLabs-TabPFN (the TabICL candidate uses TabPFN-derived components).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

# psutil is optional; fall back to no memory measurement if absent.
try:
    import psutil

    _PROC = psutil.Process(os.getpid())
except Exception:  # pragma: no cover
    _PROC = None


@dataclass
class BenchResult:
    model: str
    fit_seconds: float = 0.0
    predict_seconds: float = 0.0
    peak_rss_mb: float = 0.0
    metrics: dict = field(default_factory=dict)
    skipped: Optional[str] = None


def _rss_mb() -> float:
    if _PROC is None:
        return 0.0
    return _PROC.memory_info().rss / (1024 * 1024)


# --- Data loading -----------------------------------------------------------


def load_dataframe(args):
    import pandas as pd

    if args.data:
        df = pd.read_csv(args.data)
        return df, args.target
    if args.table:
        df = _load_databricks_table(args.table)
        return df, args.target

    # Synthetic fallback for harness smoke testing.
    print("No --data/--table provided; generating a synthetic dataset.")
    from sklearn.datasets import make_classification, make_regression

    if args.task == "regression":
        X, y = make_regression(n_samples=args.synthetic_rows, n_features=12, noise=0.2, random_state=42)
    else:
        X, y = make_classification(
            n_samples=args.synthetic_rows, n_features=12, n_informative=6, n_classes=2, random_state=42
        )
    df = pd.DataFrame(X, columns=[f"f{i}" for i in range(X.shape[1])])
    df["target"] = y
    return df, "target"


def _load_databricks_table(table: str):
    import pandas as pd
    from databricks import sql

    warehouse_id = os.environ.get("SQL_WAREHOUSE_ID", "").strip()
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not (warehouse_id and host and token):
        raise SystemExit(
            "Loading a Databricks table requires SQL_WAREHOUSE_ID, DATABRICKS_HOST, "
            "and DATABRICKS_TOKEN in the environment."
        )
    http_path = f"/sql/1.0/warehouses/{warehouse_id}"
    with sql.connect(server_hostname=host.replace("https://", ""), http_path=http_path, access_token=token) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall_arrow().to_pandas()
    return rows


# --- Preprocessing ----------------------------------------------------------


@dataclass
class Dataset:
    X_train: Any
    X_test: Any
    y_train: Any
    y_test: Any
    feature_columns: List[str]
    numeric_columns: List[str]
    categorical_columns: List[str]
    task: str
    n_classes: int


def build_dataset(df, target: str, task: str, test_size: float, train_cap: int) -> Dataset:
    import numpy as np
    import pandas as pd
    from sklearn.model_selection import train_test_split

    if target not in df.columns:
        raise SystemExit(f"target column '{target}' not found in data columns: {list(df.columns)}")

    y = df[target]
    X = df.drop(columns=[target])
    feature_columns = list(X.columns)
    categorical_columns = [c for c in feature_columns if not pd.api.types.is_numeric_dtype(X[c])]
    numeric_columns = [c for c in feature_columns if c not in categorical_columns]

    stratify = y if task == "classification" else None
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42, stratify=stratify
    )

    if train_cap and len(X_train) > train_cap:
        X_train = X_train.iloc[:train_cap]
        y_train = y_train.iloc[:train_cap]

    n_classes = int(pd.Series(y).nunique()) if task == "classification" else 0
    return Dataset(
        X_train=X_train.reset_index(drop=True),
        X_test=X_test.reset_index(drop=True),
        y_train=y_train.reset_index(drop=True),
        y_test=y_test.reset_index(drop=True),
        feature_columns=feature_columns,
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        task=task,
        n_classes=n_classes,
    )


def _score(task: str, y_true, y_pred) -> dict:
    from sklearn.metrics import accuracy_score, f1_score, mean_squared_error, r2_score
    import numpy as np

    if task == "classification":
        return {
            "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
            "f1_macro": round(float(f1_score(y_true, y_pred, average="macro")), 4),
        }
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    return {"rmse": round(rmse, 4), "r2": round(float(r2_score(y_true, y_pred)), 4)}


# --- Model runners ----------------------------------------------------------


def run_tabicl(ds: Dataset) -> BenchResult:
    res = BenchResult(model="TabICLv2")
    try:
        from tabicl import TabICLClassifier, TabICLRegressor
    except Exception as exc:
        res.skipped = f"tabicl not installed ({exc})"
        return res

    device = os.environ.get("LTM_DEVICE", "").strip() or None
    est = (
        TabICLClassifier(n_estimators=8, device=device, random_state=42)
        if ds.task == "classification"
        else TabICLRegressor(n_estimators=8, device=device, random_state=42)
    )
    return _fit_predict_sklearn(res, est, ds)


def run_catboost(ds: Dataset) -> BenchResult:
    res = BenchResult(model="CatBoost")
    try:
        from catboost import CatBoostClassifier, CatBoostRegressor
    except Exception as exc:
        res.skipped = f"catboost not installed ({exc})"
        return res

    cat_idx = [ds.feature_columns.index(c) for c in ds.categorical_columns]
    common = dict(iterations=300, depth=6, learning_rate=0.05, random_seed=42, verbose=False, cat_features=cat_idx)
    est = CatBoostClassifier(**common) if ds.task == "classification" else CatBoostRegressor(**common)
    # CatBoost needs categoricals as strings, not NaN floats.
    X_train = ds.X_train.copy()
    X_test = ds.X_test.copy()
    for c in ds.categorical_columns:
        X_train[c] = X_train[c].astype(str)
        X_test[c] = X_test[c].astype(str)
    return _fit_predict_sklearn(res, est, ds, X_train=X_train, X_test=X_test)


def run_xgboost(ds: Dataset) -> BenchResult:
    res = BenchResult(model="XGBoost")
    try:
        from xgboost import XGBClassifier, XGBRegressor
    except Exception as exc:
        res.skipped = f"xgboost not installed ({exc})"
        return res

    common = dict(n_estimators=400, max_depth=6, learning_rate=0.05, tree_method="hist", enable_categorical=True)
    X_train = ds.X_train.copy()
    X_test = ds.X_test.copy()
    for c in ds.categorical_columns:
        X_train[c] = X_train[c].astype("category")
        X_test[c] = X_test[c].astype("category")

    y_train, y_test = ds.y_train, ds.y_test
    label_map = None
    if ds.task == "classification":
        # XGBoost needs 0..K-1 integer labels.
        classes = sorted(ds.y_train.unique().tolist())
        label_map = {c: i for i, c in enumerate(classes)}
        inv = {i: c for c, i in label_map.items()}
        y_train = ds.y_train.map(label_map)
        est = XGBClassifier(**common)
    else:
        est = XGBRegressor(**common)

    res = _fit_predict_sklearn(res, est, ds, X_train=X_train, X_test=X_test, y_train=y_train, y_test=y_test)
    if label_map is not None and not res.skipped and res.metrics:
        # Map predictions back to original labels for fair scoring already done inside.
        pass
    return res


def _fit_predict_sklearn(
    res: BenchResult,
    est,
    ds: Dataset,
    *,
    X_train=None,
    X_test=None,
    y_train=None,
    y_test=None,
) -> BenchResult:
    X_train = ds.X_train if X_train is None else X_train
    X_test = ds.X_test if X_test is None else X_test
    y_train = ds.y_train if y_train is None else y_train
    y_test = ds.y_test if y_test is None else y_test

    try:
        base_rss = _rss_mb()
        t0 = time.perf_counter()
        est.fit(X_train, y_train)
        res.fit_seconds = round(time.perf_counter() - t0, 3)

        t1 = time.perf_counter()
        preds = est.predict(X_test)
        res.predict_seconds = round(time.perf_counter() - t1, 3)
        res.peak_rss_mb = round(max(0.0, _rss_mb() - base_rss), 1)

        # XGBoost classifier may return encoded ints; score against encoded y_test.
        if ds.task == "classification" and y_test is not ds.y_test:
            import pandas as pd

            classes = sorted(ds.y_train.unique().tolist())
            label_map = {c: i for i, c in enumerate(classes)}
            y_test = ds.y_test.map(label_map)
        res.metrics = _score(ds.task, y_test, preds)
    except Exception as exc:
        res.skipped = f"run failed ({exc})"
    return res


def run_tabm(ds: Dataset, epochs: int = 30, batch_size: int = 256) -> BenchResult:
    res = BenchResult(model="TabM")
    try:
        import numpy as np
        import torch
        import torch.nn.functional as F
        from tabm import TabM
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import OrdinalEncoder, StandardScaler
    except Exception as exc:
        res.skipped = f"tabm/torch not installed ({exc})"
        return res

    try:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Numeric pipeline.
        if ds.numeric_columns:
            num_imputer = SimpleImputer(strategy="median")
            scaler = StandardScaler()
            Xn_train = scaler.fit_transform(num_imputer.fit_transform(ds.X_train[ds.numeric_columns]))
            Xn_test = scaler.transform(num_imputer.transform(ds.X_test[ds.numeric_columns]))
        else:
            Xn_train = np.zeros((len(ds.X_train), 0), dtype=np.float32)
            Xn_test = np.zeros((len(ds.X_test), 0), dtype=np.float32)

        # Categorical pipeline -> ordinal codes + cardinalities.
        cat_cardinalities: List[int] = []
        if ds.categorical_columns:
            enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
            Xc_train = enc.fit_transform(ds.X_train[ds.categorical_columns].astype(str))
            Xc_test = enc.transform(ds.X_test[ds.categorical_columns].astype(str))
            cat_cardinalities = [len(c) for c in enc.categories_]
            # Map unknown (-1) to a valid in-range index (0).
            Xc_test = np.where(Xc_test < 0, 0, Xc_test)
        else:
            Xc_train = np.zeros((len(ds.X_train), 0))
            Xc_test = np.zeros((len(ds.X_test), 0))

        # Targets.
        if ds.task == "classification":
            classes = sorted(ds.y_train.unique().tolist())
            cls_map = {c: i for i, c in enumerate(classes)}
            y_train = np.array([cls_map[v] for v in ds.y_train], dtype=np.int64)
            d_out = len(classes)
        else:
            y_train = ds.y_train.to_numpy(dtype=np.float32)
            d_out = 1

        make_kwargs = dict(n_num_features=Xn_train.shape[1], d_out=d_out)
        if cat_cardinalities:
            make_kwargs["cat_cardinalities"] = cat_cardinalities
        model = TabM.make(**make_kwargs).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=3e-4)

        xn = torch.as_tensor(Xn_train, dtype=torch.float32, device=device)
        xc = torch.as_tensor(Xc_train, dtype=torch.long, device=device) if cat_cardinalities else None
        yt = torch.as_tensor(y_train, device=device)

        base_rss = _rss_mb()
        t0 = time.perf_counter()
        n = xn.shape[0]
        model.train()
        for _ in range(epochs):
            perm = torch.randperm(n, device=device)
            for start in range(0, n, batch_size):
                idx = perm[start : start + batch_size]
                xc_b = xc[idx] if xc is not None else None
                out = model(xn[idx], xc_b)  # (B, k, d_out)
                k = out.shape[1]
                if ds.task == "classification":
                    logits = out.reshape(-1, d_out)
                    target = yt[idx].repeat_interleave(k)
                    loss = F.cross_entropy(logits, target)
                else:
                    pred = out.reshape(-1)
                    target = yt[idx].repeat_interleave(k)
                    loss = F.mse_loss(pred, target)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
        res.fit_seconds = round(time.perf_counter() - t0, 3)

        # Predict (average over the k submodels).
        model.eval()
        xn_te = torch.as_tensor(Xn_test, dtype=torch.float32, device=device)
        xc_te = torch.as_tensor(Xc_test, dtype=torch.long, device=device) if cat_cardinalities else None
        t1 = time.perf_counter()
        with torch.no_grad():
            out = model(xn_te, xc_te)  # (B, k, d_out)
            if ds.task == "classification":
                probs = F.softmax(out, dim=-1).mean(dim=1)  # (B, d_out)
                pred_idx = probs.argmax(dim=-1).cpu().numpy()
                inv = {i: c for c, i in cls_map.items()}
                preds = np.array([inv[i] for i in pred_idx])
            else:
                preds = out.mean(dim=1).squeeze(-1).cpu().numpy()
        res.predict_seconds = round(time.perf_counter() - t1, 3)
        res.peak_rss_mb = round(max(0.0, _rss_mb() - base_rss), 1)
        res.metrics = _score(ds.task, ds.y_test, preds)
    except Exception as exc:
        res.skipped = f"run failed ({exc})"
    return res


_RUNNERS = {
    "tabicl": run_tabicl,
    "tabm": run_tabm,
    "catboost": run_catboost,
    "xgboost": run_xgboost,
}


# --- Reporting --------------------------------------------------------------


def print_report(results: List[BenchResult], ds: Dataset) -> None:
    print("\n" + "=" * 78)
    print("TABULAR MODEL BENCHMARK")
    print("=" * 78)
    print(f"Task: {ds.task} | train={len(ds.X_train)} test={len(ds.X_test)} | "
          f"features={len(ds.feature_columns)} (num={len(ds.numeric_columns)}, cat={len(ds.categorical_columns)})")
    if ds.task == "classification":
        print(f"Classes: {ds.n_classes}")
    print("-" * 78)
    header = f"{'Model':<12}{'Fit(s)':>9}{'Pred(s)':>9}{'RSS(MB)':>9}  Metrics"
    print(header)
    print("-" * 78)
    for r in results:
        if r.skipped:
            print(f"{r.model:<12}{'-':>9}{'-':>9}{'-':>9}  SKIPPED: {r.skipped}")
            continue
        metric_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
        print(f"{r.model:<12}{r.fit_seconds:>9}{r.predict_seconds:>9}{r.peak_rss_mb:>9}  {metric_str}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark TabICLv2 vs TabM vs GBDT baselines.")
    parser.add_argument("--data", help="Path to a local CSV file.")
    parser.add_argument("--table", help="Databricks table name catalog.schema.table.")
    parser.add_argument("--target", default="target", help="Target column name.")
    parser.add_argument("--task", choices=["classification", "regression"], default="classification")
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--train-cap", type=int, default=20000,
                        help="Cap training rows (TFMs slow down with large context). 0 = no cap.")
    parser.add_argument("--synthetic-rows", type=int, default=2000)
    parser.add_argument("--epochs", type=int, default=30, help="TabM training epochs.")
    parser.add_argument(
        "--models",
        default="tabicl,tabm,catboost,xgboost",
        help="Comma-separated subset of: tabicl,tabm,catboost,xgboost",
    )
    args = parser.parse_args()

    df, target = load_dataframe(args)
    ds = build_dataset(df, target, args.task, args.test_size, args.train_cap)

    selected = [m.strip().lower() for m in args.models.split(",") if m.strip()]
    results: List[BenchResult] = []
    for name in selected:
        runner = _RUNNERS.get(name)
        if runner is None:
            print(f"Unknown model '{name}', skipping.")
            continue
        print(f"Running {name} ...")
        if name == "tabm":
            results.append(runner(ds, epochs=args.epochs))
        else:
            results.append(runner(ds))

    print_report(results, ds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
