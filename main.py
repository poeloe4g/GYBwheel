"""Pipeline orchestration (F12 / B9, Spec 2.4).

regime -> positions -> universe -> select -> filter -> size -> score -> report.

``--paper`` is the default and the only mode: the screener never places trades,
it only prints and writes a CSV.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import regime as regime_mod
import report as report_mod
import score as score_mod
import size as size_mod
import universe as universe_mod
from config import ConfigError, load_config, load_secrets
from data import DataProvider

log = logging.getLogger("main")

# Fallback seed universe (mega-caps across sectors), used only when neither
# --tickers is given nor the --tickers-file exists.
DEFAULT_CANDIDATES = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "JPM", "V", "MA", "UNH",
    "HD", "PG", "KO", "PEP", "COST", "WMT", "XOM", "CVX", "JNJ", "ABBV",
]

DEFAULT_TICKERS_FILE = "data/universe_sp100.txt"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="BORING Wheel Screener — cash-secured-put candidate screener (paper-only).",
    )
    p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    p.add_argument("--positions", default="positions.yaml", help="Path to positions.yaml (B3)")
    p.add_argument("--output", default="candidates.csv", help="CSV output path")
    p.add_argument("--json-out", help="Write a run snapshot JSON here (dashboard feed).")
    p.add_argument("--tickers", help="Comma-separated tickers to screen (overrides --tickers-file)")
    p.add_argument("--tickers-file", default=DEFAULT_TICKERS_FILE,
                   help="File of tickers to screen, one per line, # comments allowed "
                        "(missing file falls back to the built-in seed list)")
    p.add_argument("--sp500-file", help="File of S&P 500 tickers (one per line) for breadth")
    p.add_argument("--max-rows", type=int, default=25, help="Max ranked rows to display")
    p.add_argument(
        "--paper", action="store_true", default=True,
        help="Paper/dry-run mode (default and only mode — never trades).",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return p


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    secrets = load_secrets()
    provider = DataProvider(config, secrets)
    session = _market_session()
    if session != "regular":
        log.warning("run is outside regular US market hours — option quotes may be "
                    "stale or zeroed; snapshot will be stamped quotes_trusted=false")

    # 1. Regime check ------------------------------------------------------
    spy_hist = [h["close"] for h in provider.get_price_history("SPY", period="1y")]
    vix = provider.get_vix()
    members = _load_lines(args.sp500_file) if args.sp500_file else None
    breadth = provider.get_breadth(members) if members else None
    regime = regime_mod.assess(spy_hist, vix, breadth, config)

    account = size_mod.load_positions(args.positions)

    if regime.is_red:
        header = report_mod.build_header(regime, account, config)
        print(report_mod.render_console(header, []))
        print("\nRED regime — manage existing positions only. No new screening.")
        if args.json_out:
            report_mod.write_json(
                header, [], regime, config, args.json_out,
                near_misses=[],
                meta_extra={
                    "data_source": "yfinance",
                    "tickers_screened": [],
                    "breadth_evaluated": bool(members),
                    "max_rows": args.max_rows,
                    "rejections_by_reason": {},
                    "flags_by_reason": {},
                    "contracts_evaluated": 0,
                    "contract_gate_failures": {},
                    "market_session": session,
                    "quotes_trusted": session == "regular",
                },
            )
        return 0

    # 2/3. Universe --------------------------------------------------------
    candidates = _resolve_candidates(args)
    passing, universe_rejects = universe_mod.build_universe(candidates, provider, config)

    dte_cfg, delta_cfg, quality = config["dte"], config["delta"], config["quality"]
    earnings_policy = _resolve_earnings_policy(quality)
    require_affordable = bool(config["account"].get("require_affordable", False))
    prefer_affordable = bool(config.get("scoring", {}).get("prefer_affordable", False))
    scored_rows = []
    near_miss_rows = []
    rejection_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    contracts_evaluated = 0
    contract_gate_failures: dict[str, int] = {}

    def _count(code: str) -> None:
        rejection_counts[code] = rejection_counts.get(code, 0) + 1

    def _count_flag(code: str) -> None:
        flag_counts[code] = flag_counts.get(code, 0) + 1

    for _ in universe_rejects:
        _count("universe")

    for f in passing:
        ticker = f["ticker"]
        spot = f.get("price")
        if not spot:
            hist = provider.get_price_history(ticker, period="5d")
            spot = hist[-1]["close"] if hist else None
        if not spot:
            log.info("skip %s: no spot price", ticker)
            _count("no_spot")
            continue

        # 4+5. Gate every in-band contract, then select (filter-then-select):
        # earnings and quality gates run per contract inside evaluate_puts, so
        # one illiquid strike no longer rejects a ticker whose adjacent in-band
        # strikes pass.
        next_earn = provider.get_next_earnings(ticker)
        res = provider.get_put_candidate(
            ticker, spot,
            dte_min=dte_cfg["min"], dte_max=dte_cfg["max"],
            target_delta=delta_cfg["target"], delta_min=delta_cfg["min"], delta_max=delta_cfg["max"],
            quality=quality, next_earnings=next_earn,
        )
        contracts_evaluated += res.get("n_in_band", 0)
        for code, n in (res.get("gate_failures") or {}).items():
            contract_gate_failures[code] = contract_gate_failures.get(code, 0) + n

        put = res.get("selected") or res.get("fallback")
        if not put:
            reason_code = res.get("reason", "no_put_in_band")
            log.info("skip %s: %s", ticker, reason_code)
            _count(reason_code)
            continue
        rejections = list(put.get("rejections") or [])
        flags = list(put.get("flags") or [])
        put = {k: v for k, v in put.items() if k not in ("rejections", "flags")}

        if earnings_policy == "reject":
            for e in [f for f in flags if f["code"] == "earnings_unknown"]:
                flags.remove(e)
                rejections.append(e)

        # Contracts without a usable premium/strike/DTE can't be sized or scored
        # at all — count them, but they don't make meaningful near-miss rows.
        unsizeable = {"no_premium", "missing_strike_dte"}
        if any(e["code"] in unsizeable for e in rejections):
            log.info("reject %s: %s", ticker, "; ".join(e["message"] for e in rejections))
            for e in rejections:
                _count(e["code"])
            continue

        # 6. Sizing + 7. Score — near-misses too, so they carry the full row shape.
        candidate = {**put, "ticker": ticker, "sector": f.get("sector", "Unknown"),
                     "spot": spot}
        sized = size_mod.size_candidate(candidate, account, config)
        scored = score_mod.score_candidate(sized, config, spot)

        if require_affordable and not sized["affordable"]:
            rejections.append({
                "code": "unaffordable",
                "message": (f"collateral ${sized['collateral_per_contract']:,.0f} exceeds "
                            f"available headroom (min account "
                            f"${sized['min_account_for_1_contract']:,.0f})"),
            })

        # Flags-only rows whose every flag is the earnings one are promotable
        # under policy=flag; all other flags (iv_missing, spread_unknown,
        # oi_unknown) keep the near-miss route — they never pass silently.
        promotable = (
            earnings_policy == "flag"
            and not rejections
            and flags
            and all(e["code"] == "earnings_unknown" for e in flags)
        )
        if rejections or (flags and not promotable):
            log.info("reject %s: %s", ticker,
                     "; ".join(e["message"] for e in rejections + flags))
            for e in rejections or flags:
                _count(e["code"])
            near_miss_rows.append(
                {**scored, "rejection_reasons": rejections, "data_flags": flags}
            )
        else:
            if flags:
                log.info("flag %s: %s", ticker, "; ".join(e["message"] for e in flags))
                for e in flags:
                    _count_flag(e["code"])
            scored_rows.append({**scored, "data_flags": flags})

    ranked = score_mod.rank(scored_rows, prefer_affordable=prefer_affordable)[: args.max_rows]
    near_misses = score_mod.rank(near_miss_rows)[: args.max_rows]

    # B1 capital sanity check — over every sized row (candidates AND near
    # misses): the warning matters most when everything breaches the cap.
    warn = size_mod.sanity_check_capital(
        [r["strike"] for r in scored_rows + near_miss_rows], config)
    if warn:
        log.warning(warn)

    # 8. Report
    header = report_mod.build_header(regime, account, config)
    print(report_mod.render_console(header, ranked))
    out = report_mod.write_csv(ranked, args.output)
    print(f"\nWrote {len(ranked)} rows to {out}")
    if args.json_out:
        jout = report_mod.write_json(
            header, ranked, regime, config, args.json_out,
            near_misses=near_misses,
            meta_extra={
                "data_source": "yfinance",
                "tickers_screened": candidates,
                "breadth_evaluated": bool(members),
                "max_rows": args.max_rows,
                "rejections_by_reason": rejection_counts,
                "flags_by_reason": flag_counts,
                "contracts_evaluated": contracts_evaluated,
                "contract_gate_failures": contract_gate_failures,
                "market_session": session,
                "quotes_trusted": session == "regular",
                "capital_warning": warn,
            },
        )
        print(f"Wrote run snapshot to {jout}")
    return 0


def _market_session(now: datetime | None = None) -> str:
    """Approximate US equity session from UTC alone (stdlib, no tz database).

    Weekdays 13:30–20:00 UTC covers regular hours in both EDT and EST with ~1h
    drift at the edges — the same caveat as the UTC-only CI cron. Holidays are
    not modeled; a holiday run is merely stamped "regular" with stale quotes,
    which the freshness badge already covers.
    """
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return "closed"
    minutes = now.hour * 60 + now.minute
    return "regular" if 13 * 60 + 30 <= minutes < 20 * 60 else "closed"


_EARNINGS_POLICIES = ("flag", "near_miss", "reject")


def _resolve_earnings_policy(quality: dict) -> str:
    policy = str(quality.get("unknown_earnings_policy", "near_miss")).lower()
    if policy not in _EARNINGS_POLICIES:
        log.warning("unknown_earnings_policy %r is not one of %s; falling back to near_miss",
                    policy, "|".join(_EARNINGS_POLICIES))
        return "near_miss"
    return policy


def _resolve_candidates(args: argparse.Namespace) -> list[str]:
    """--tickers (explicit) > --tickers-file (if it exists) > built-in seed."""
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    tickers_file = getattr(args, "tickers_file", None)
    if tickers_file and Path(tickers_file).exists():
        return _load_lines(tickers_file)
    return DEFAULT_CANDIDATES


def _load_lines(path: str) -> list[str]:
    lines = []
    for ln in Path(path).read_text().splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            lines.append(ln.upper())
    return lines


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        return run(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
