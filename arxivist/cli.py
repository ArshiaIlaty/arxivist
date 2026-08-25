"""arxivist command-line interface."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import load_config
from .discover import find_pdfs
from .models import PlannedMove
from .organize import apply_moves, latest_manifest, plan_moves, undo

app = typer.Typer(
    add_completion=False,
    help="Identify research-paper PDFs, rename them to '<year> - <title>', "
         "and file them into topic folders.",
)
console = Console()


def _version_cb(value: bool):
    if value:
        console.print(f"arxivist {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    _version: bool = typer.Option(
        False, "--version", callback=_version_cb, is_eager=True, help="Show version and exit."
    ),
):
    """arxivist: sort research papers into '<year> - <title>' inside topic folders."""


@app.command()
def organize(
    source: Path = typer.Argument(..., exists=True, help="Folder (or PDF) to scan."),
    dest: Optional[Path] = typer.Option(
        None, "--dest", "-d", help="Library root to file papers into. Defaults to SOURCE."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Actually move files. Without this flag it's a dry run."
    ),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Recurse into SOURCE."),
    no_online: bool = typer.Option(False, "--no-online", help="Skip Crossref/arXiv lookups."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip the LLM topic classifier."),
):
    """Plan (and with --apply, perform) the rename+file operation."""
    dest_root = (dest or source if source.is_dir() else dest or source.parent).resolve()
    cfg = load_config(dest_root, config)
    if no_online:
        cfg.use_online = False
    if no_llm:
        cfg.use_llm = False

    pdfs = find_pdfs(source.resolve(), recursive=recursive)
    if not pdfs:
        console.print("[yellow]No PDF files found.[/yellow]")
        raise typer.Exit()

    console.print(
        f"Scanning [bold]{len(pdfs)}[/bold] PDF(s) → library [bold]{dest_root}[/bold]"
        + ("" if cfg.use_online else "  [dim](offline)[/dim]")
        + ("" if cfg.use_llm else "  [dim](no LLM)[/dim]")
    )

    plans: List[PlannedMove] = []
    with console.status("Analyzing…") as status:
        def _progress(p: Path):
            status.update(f"Analyzing {p.name}")
        plans = plan_moves(pdfs, cfg, progress=_progress)

    _render_table(plans, dest_root)
    movable = [p for p in plans if p.dest is not None and p.status not in {"not-paper", "error"}]

    if not apply:
        console.print(
            f"\n[bold]Dry run.[/bold] {len(movable)} of {len(plans)} file(s) would be filed. "
            "Re-run with [bold]--apply[/bold] to move them."
        )
        raise typer.Exit()

    if not movable:
        console.print("\n[yellow]Nothing to move.[/yellow]")
        raise typer.Exit()

    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    manifest = apply_moves(plans, cfg, run_id)
    console.print(
        f"\n[green]Moved {len(movable)} file(s).[/green] "
        f"Undo with: [bold]arxivist undo --dest {dest_root}[/bold]\n[dim]manifest: {manifest}[/dim]"
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (use 0.0.0.0 on a server)."),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml."),
    workdir: Optional[Path] = typer.Option(
        None, "--workdir", help="Where per-session upload/organize workspaces live."
    ),
):
    """Launch the web UI: upload PDFs, watch analysis stream, download the organized set."""
    try:
        import uvicorn
    except ImportError:
        console.print('[red]Web extra not installed.[/red] Run: pip install -e ".[web]"')
        raise typer.Exit(code=1)
    from .web import create_app

    app_ = create_app(config_path=config, workdir=workdir)
    console.print(f"arxivist web UI on [bold]http://{host}:{port}[/bold]")
    uvicorn.run(app_, host=host, port=port)


@app.command()
def doctor(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml."),
    dest: Optional[Path] = typer.Option(
        None, "--dest", "-d", help="Library root whose config to load. Defaults to CWD."
    ),
):
    """Check whether topic classification (the LLM) actually works on this machine.

    Renaming needs no key (arXiv/Crossref lookups are free), but filing papers into
    topic folders needs the LLM. This runs one real classification call and reports
    exactly why it does or doesn't work.
    """
    from rich.markup import escape

    from .llm import diagnose

    cfg = load_config((dest or Path.cwd()).resolve(), config)
    console.print(
        f"provider: [bold]{cfg.provider}[/bold]   "
        f"model: [bold]{cfg.bedrock_model if cfg.provider == 'bedrock' else cfg.model}[/bold]   "
        f"use_llm: [bold]{cfg.use_llm}[/bold]   use_online: [bold]{cfg.use_online}[/bold]"
    )
    report = diagnose(cfg)
    if report["ok"]:
        console.print(f"[green]✓ LLM classification works.[/green] {escape(report['detail'])}")
    else:
        console.print(f"[red]✗ LLM classification unavailable.[/red] {escape(report['detail'])}")
        for err in report.get("errors", []):
            console.print(f"  [dim]{escape(err)}[/dim]")
        console.print(
            "\n[yellow]Papers will fall back to [bold]_Unsorted[/bold]/[bold]_NeedsReview[/bold] "
            "until this is fixed.[/yellow]\n"
            "Fix one of:\n"
            r"  • Anthropic API: set [bold]ANTHROPIC_API_KEY[/bold] and install [bold]'.\[llm]'[/bold]"
            "\n  • Bedrock: set [bold]provider: bedrock[/bold], [bold]bedrock_model[/bold], and "
            "AWS creds/[bold]AWS_REGION[/bold]"
        )
        raise typer.Exit(code=1)


@app.command("undo")
def undo_cmd(
    dest: Path = typer.Option(..., "--dest", "-d", exists=True, help="Library root used previously."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml."),
    manifest: Optional[Path] = typer.Option(None, "--manifest", help="Specific manifest to undo."),
):
    """Reverse the most recent (or a specified) organize run."""
    cfg = load_config(dest.resolve(), config)
    target = manifest or latest_manifest(cfg)
    if not target or not target.exists():
        console.print("[yellow]No manifest found to undo.[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"Undoing [bold]{target.name}[/bold]…")
    for note in undo(target):
        console.print(f"  {note}")
    console.print("[green]Done.[/green]")


def _render_table(plans: List[PlannedMove], dest_root: Path):
    table = Table(show_lines=False, expand=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Source", overflow="fold")
    table.add_column("→ Topic / New name", overflow="fold")

    style = {
        "planned": "green", "collision": "yellow", "needs-review": "yellow",
        "not-paper": "dim", "error": "red",
    }
    for p in plans:
        color = style.get(p.status, "white")
        if p.dest is not None:
            try:
                shown = str(p.dest.relative_to(dest_root))
            except ValueError:
                shown = str(p.dest)
            right = shown
        else:
            right = f"[dim]{p.note or p.status}[/dim]"
        src_conf = f" [dim]({p.meta.source})[/dim]" if p.meta.source not in {"none", ""} else ""
        table.add_row(f"[{color}]{p.status}[/{color}]", p.src.name + src_conf, right)
    console.print(table)


if __name__ == "__main__":
    app()
