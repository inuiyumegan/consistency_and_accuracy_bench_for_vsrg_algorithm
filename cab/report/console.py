"""Rich console UI reporting for CAB-VA (6 Core Dimensions, 100-Point Scale)."""

from __future__ import annotations

from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cab.runner.suite import BenchmarkRunResult


def print_console_report(result: BenchmarkRunResult, console: Optional[Console] = None):
    """Print beautifully styled benchmark results to terminal."""
    con = console or Console()

    score = result.score
    acc = result.accuracy
    inv = result.inversion
    cont = result.continuity
    jit = result.jitter
    sln = result.short_ln
    spd = getattr(result, "speed", None)

    # Color mapping by score band (purely cosmetic; no letter grade is shown)
    def _score_color(value: float) -> str:
        if value >= 90.0:
            return "bold magenta"
        if value >= 80.0:
            return "bold green"
        if value >= 70.0:
            return "bold cyan"
        if value >= 60.0:
            return "bold yellow"
        if value >= 45.0:
            return "bold red"
        return "bold white on red"

    color = _score_color(score.total_score)

    header_lines = [
        f"[bold white]Target Algorithm:[/bold white] [cyan]{result.algorithm_name}[/cyan]  |  "
        f"[bold white]Scale:[/bold white] [yellow]{result.scale_type}[/yellow]  |  "
        f"[bold white]Mode:[/bold white] [green]{result.config.mode.upper()}[/green]"
    ]
    if result.config.has_filters():
        tot = result.sample_stats.get("total", len(result.accuracy.calibrated_predictions))
        header_lines.append(
            f"[bold white]Filters:[/bold white] [magenta]{result.filter_summary}[/magenta] "
            f"([bold cyan]{tot} maps[/bold cyan])"
        )
    header_lines.append(
        f"[bold white]Total CAB-Score:[/bold white] [{color}]{score.total_score:.1f} / 100[/{color}]"
    )

    con.print("")
    con.print(
        Panel(
            "\n".join(header_lines),
            title="[bold blue]CAB-VA Benchmark Summary[/bold blue]",
            expand=False,
            border_style="bright_blue",
        )
    )

    # Dimension Scores Table (6 Dimensions)
    table = Table(title="Six Core Benchmark Dimensions (Normalized 0 - 100)", header_style="bold cyan")
    table.add_column("Dimension", style="bold white")
    table.add_column("Beta", justify="center", style="yellow")
    table.add_column("Score (Max 100)", justify="right")
    table.add_column("Key Metric", justify="left")
    table.add_column("Evaluation Status", justify="center")

    _betas = getattr(score, "dimension_betas", {}) or {}

    def _wpct(name: str, fallback: str) -> str:
        v = _betas.get(name)
        return f"{round(v * 100, 2):g}%" if v is not None else fallback

    table.add_row(
        "1. Ground-Truth Accuracy",
        "backbone",
        f"{score.s_accuracy:.1f}",
        f"RMSE: {acc.global_rmse:.3f} ND | Spearman rho: {acc.spearman_rho:.3f}",
        "[green]PASS[/green]" if acc.spearman_rho > 0.8 else "[yellow]MODERATE[/yellow]",
    )
    if score.s_inversion is not None and inv is not None:
        table.add_row(
            "2. Anti-Rate-Inversion",
            _wpct("Anti-Rate-Inversion", "15%"),
            f"{score.s_inversion:.1f}",
            f"IF: {inv.inversion_frequency*100:.2f}% | Max Drop: {inv.max_relative_drop*100:.1f}%",
            "[green]PASS[/green]" if inv.inversion_frequency < 0.05 else "[yellow]INVERTED[/yellow]",
        )
    else:
        table.add_row(
            "2. Anti-Rate-Inversion",
            "15%",
            "-",
            "Not evaluated in sub-tiers (All Range only)",
            "[dim]N/A[/dim]",
        )
    table.add_row(
        "3. Anti-Short LN Robustness",
        _wpct("Anti-Short LN", "15%"),
        f"{score.s_short_ln:.1f}",
        f"Inflation: {sln.inflation_tendency_ratio:+.3f}% | {sln.immunity_grade}",
        "[bold green]IMMUNE (S+)[/bold green]" if sln.has_native_immunity else "[cyan]PASS[/cyan]",
    )
    if score.s_continuity is not None and cont is not None:
        table.add_row(
            "4. Local Continuity",
            _wpct("Local Continuity", "10%"),
            f"{score.s_continuity:.1f}",
            f"Cliffs: {cont.cliff_events_count} | Smoothness: {cont.smoothness_score:.1f}/100",
            "[green]SMOOTH[/green]" if cont.cliff_events_count <= 2 else "[yellow]CLIFFS[/yellow]",
        )
    else:
        table.add_row(
            "4. Local Continuity",
            "10%",
            "-",
            "Not evaluated in sub-tiers (All Range only)",
            "[dim]N/A[/dim]",
        )
    if spd is not None:
        table.add_row(
            "5. Computation Speed",
            _wpct("Computation Speed", "5%"),
            f"{score.s_speed:.1f}",
            f"Speed: {spd.average_speed_ms_per_s:.3f} ms/s ({spd.realtime_factor:.0f}x realtime)",
            "[bold green]BLAZING[/bold green]" if spd.average_speed_ms_per_s <= 1.0 else "[green]FAST[/green]" if spd.average_speed_ms_per_s <= 5.0 else "[yellow]MODERATE[/yellow]",
        )
    table.add_row(
        "6. Anti-Jitter Robustness",
        _wpct("Anti-Jitter", "5%"),
        f"{score.s_jitter:.1f}",
        f"Self-RMSE: {jit.jitter_rmse:.5f} | MAPE: {jit.mape:.3f}%",
        "[green]ROBUST[/green]" if jit.mape < 2.0 else "[yellow]SENSITIVE[/yellow]",
    )

    con.print(table)

    # Warnings if macro inversions exist
    if acc.calibration and acc.calibration.macro_alerts:
        con.print(
            Panel(
                f"[bold red]Detected {len(acc.calibration.macro_alerts)} Macro Inversions across difficulty tiers![/bold red]\n"
                + "\n".join(f"• {a}" for a in acc.calibration.macro_alerts[:3]),
                title="[bold yellow]Macro Inversion Alarm[/bold yellow]",
                border_style="red",
            )
        )
