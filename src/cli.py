"""Единая точка входа: `grokbot <команда>`.

До этого модуля запуск, проверка, реплей, дашборд и подбор весов жили в
разных местах и вызывались по-разному. Одна команда с подкомандами — это
не украшение: в runbook и в unit-файле должно стоять что-то одно, что
человек вспомнит через месяц.

    grokbot run                # paper/dry-run pipeline (live refused)
    grokbot paper-monitor      # observation universe only (no agents/trades)
    grokbot evaluate-baselines # compare baseline arms on stored observations
    grokbot check              # проверить конфиг и выйти
    grokbot doctor             # предполётная проверка окружения
    grokbot replay [лог]       # сводка по логу
    grokbot dashboard [лог]    # живое состояние
    grokbot tune [лог]         # подбор весов и порога
    grokbot curve              # числа кривой: комиссия, влияние, потолок

`python -m src.pipeline` продолжает работать: старые unit-файлы и cron не
должны ломаться из-за переезда команды.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import runpy
import sys
from pathlib import Path

from .curve import sanity_check
from .doctor import run_checks, summary
from .models import Config, ConfigError
from .pipeline import amain as run_pipeline

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grokbot",
        description="Paper-only Pump.fun launch research pipeline",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="торговый цикл")
    run.add_argument("--config", default="config.yaml")
    run.add_argument("--i-understand-the-risk", action="store_true",
                     help="обязателен для запуска в режиме live")

    check = sub.add_parser("check", help="проверить конфиг и выйти")
    check.add_argument("--config", default="config.yaml")

    doctor = sub.add_parser("doctor", help="предполётная проверка окружения")
    doctor.add_argument("--config", default="config.yaml")
    doctor.add_argument("--offline", action="store_true", help="без сетевых проверок")
    doctor.add_argument("--json", action="store_true", help="машиночитаемый вывод")

    for name, help_text in (("replay", "сводка по логу"),
                            ("dashboard", "живое состояние"),
                            ("tune", "подбор весов и порога")):
        script = sub.add_parser(name, help=help_text)
        script.add_argument("args", nargs=argparse.REMAINDER)

    sub.add_parser("curve", help="числа кривой: комиссия, влияние, потолок заявки")

    pm = sub.add_parser("paper-monitor", help="observation universe monitor (paper-only)")
    pm.add_argument("--config", default="config.paper.yaml")
    pm.add_argument("--data-dir", default="data")
    pm.add_argument("--max-launches", type=int, default=0,
                    help="stop after N promoted launches (0=forever)")

    ev = sub.add_parser("evaluate-baselines", help="run baseline arms on data/observations")
    ev.add_argument("--data-dir", default="data")
    ev.add_argument("--json", action="store_true")
    return parser


def load(config_path: str) -> Config:
    path = Path(config_path)
    if not path.exists():
        raise SystemExit(f"Конфига {path} нет. Скопируйте config.example.yaml в config.yaml.")
    try:
        return Config.load(path)
    except Exception as exc:
        raise SystemExit(f"Конфиг {path} не читается: {exc}") from exc


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load(args.config)
    report = asyncio.run(run_checks(config, skip_network=args.offline))

    if args.json:
        print(json.dumps({
            "summary": summary(report),
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail}
                       for c in report.checks],
        }, ensure_ascii=False, indent=2))
    else:
        print()
        print("  ПРЕДПОЛЁТНАЯ ПРОВЕРКА")
        print("  " + "─" * 58)
        print(report.render())
        print()
    return 1 if report.failed else 0


def cmd_check(args: argparse.Namespace) -> int:
    config = load(args.config)
    try:
        warnings = config.check_ready()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"ВНИМАНИЕ: {warning}", file=sys.stderr)
    print(json.dumps(config.redacted(), ensure_ascii=False, indent=2))
    print("\nКонфиг пригоден для запуска.", file=sys.stderr)
    return 0


def cmd_curve() -> int:
    numbers = sanity_check()
    print("\n  Кривая pump.fun: во что обходится сделка на свежем токене\n")
    print(f"    цена в начале кривой         {numbers['spot_price']:.12f} SOL")
    print(f"    заявка 0.5 SOL двигает цену  {numbers['impact_0.5_sol']:.2f} %")
    print(f"    вход и выход 0.5 SOL стоят   {numbers['round_trip_0.5_sol']:.2f} %")
    print(f"    потолок заявки при 3%        {numbers['max_sol_for_3pct']:.3f} SOL")
    print(f"    за 1 SOL дают токенов        {numbers['tokens_for_1_sol']:,.0f}")
    print("\n  Константы взяты из программы pump.fun и могут устареть:")
    print("  перед включением live сверьте их с ончейном.\n")
    return 0


def run_script(name: str, args: list[str]) -> int:
    """Запустить скрипт из scripts/ так, будто его вызвали напрямую."""
    script = SCRIPTS / f"{name}.py"
    if not script.exists():
        raise SystemExit(f"Скрипт {script} не найден")
    sys.argv = [str(script), *args]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def cmd_paper_monitor(args: argparse.Namespace) -> int:
    """Run observation universe monitor: store all launches, no live trading."""
    from .observation_monitor import ObservationMonitor
    from .observation_store import ObservationStore
    from .enrichment import HolderEnricher, GlobalMarketMetrics
    from .log import setup_logging
    import httpx

    config = load(args.config)
    if config.is_live:
        print("Отказ: live mode запрещён", file=sys.stderr)
        return 1
    try:
        config.check_ready()
    except ConfigError as exc:
        # Allow paper monitor without grok key — observation only.
        if "grok.api_key" not in str(exc):
            print(str(exc), file=sys.stderr)
            return 1
        print("ВНИМАНИЕ: grok.api_key отсутствует — monitor-only OK", file=sys.stderr)
    setup_logging(config)
    store = ObservationStore(args.data_dir)
    
    # Wave1 enrichment: initialize enrichers for paper-monitor path
    global_metrics = GlobalMarketMetrics()

    async def _run() -> int:
        # Create holder enricher with async context
        async with HolderEnricher(config) as holder_enricher:
            monitor = ObservationMonitor(
                config, 
                store=store,
                holder_enricher=holder_enricher,
                global_metrics=global_metrics,
                on_migration=lambda mint: global_metrics.record_migration(),
            )
            
            n = 0
            async for token in monitor.stream():
                n += 1
                global_metrics.record_launch()  # Track launches for rate calculation
                oid = getattr(token, "observation_id", "")
                print(f"promoted {token.mint[:8]} obs={oid} buyers={token.unique_buyers}")
                if args.max_launches and n >= args.max_launches:
                    return 0
        return 0

    return asyncio.run(_run())


def cmd_evaluate_baselines(args: argparse.Namespace) -> int:
    from .observation_store import ObservationStore
    from .research.evaluate import evaluate_arms

    store = ObservationStore(args.data_dir)
    observations = list(store.iter_observations())
    results = evaluate_arms(observations)
    # Drop selected_ids from summary print unless json full
    summary = {
        k: {kk: vv for kk, vv in v.items() if kk != "selected_ids"}
        for k, v in results.items()
    }
    print(json.dumps(summary if args.json else summary, ensure_ascii=False, indent=2))
    print(
        "\nComparative only — do not claim agent value without OOS improvement.",
        file=sys.stderr,
    )
    return 0



def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "run"

    if command == "run":
        run_args = ["--config", getattr(args, "config", "config.yaml")]
        if getattr(args, "i_understand_the_risk", False):
            run_args.append("--i-understand-the-risk")
        return asyncio.run(run_pipeline(run_args))
    if command == "check":
        return cmd_check(args)
    if command == "doctor":
        return cmd_doctor(args)
    if command == "curve":
        return cmd_curve()
    if command == "paper-monitor":
        return cmd_paper_monitor(args)
    if command == "evaluate-baselines":
        return cmd_evaluate_baselines(args)
    if command in ("replay", "dashboard", "tune"):
        return run_script(command, [a for a in args.args if a != "--"])

    build_parser().print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
