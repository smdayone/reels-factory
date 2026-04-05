"""
System resource check before heavy operations.
Prevents OOM crashes on CPU-only Windows machines.
"""
import psutil
from rich.console import Console
from config.settings import RAM_SAFETY_GB

console = Console()


def check_ram(operation_name: str = "this operation") -> bool:
    """
    Check available RAM. Warn if below threshold.
    Returns True if safe to proceed, False if risky.
    """
    available_gb = psutil.virtual_memory().available / (1024 ** 3)
    total_gb = psutil.virtual_memory().total / (1024 ** 3)

    if available_gb < RAM_SAFETY_GB:
        console.print(
            f"[yellow]Warning: Low RAM:[/yellow] "
            f"{available_gb:.1f}GB available / {total_gb:.1f}GB total. "
            f"Minimum recommended for {operation_name}: {RAM_SAFETY_GB}GB"
        )
        console.print("[yellow]Close other applications before continuing.[/yellow]")
        response = input("Continue anyway? (y/N): ").strip().lower()
        return response == "y"

    console.print(
        f"[green]RAM OK:[/green] {available_gb:.1f}GB available"
    )
    return True


def check_disk_space(path, min_gb: float = 5.0) -> bool:
    """Check available disk space on SSD."""
    usage = psutil.disk_usage(str(path))
    free_gb = usage.free / (1024 ** 3)
    if free_gb < min_gb:
        console.print(
            f"[red]Low disk space:[/red] {free_gb:.1f}GB free on {path}. "
            f"Minimum: {min_gb}GB"
        )
        return False
    console.print(f"[green]Disk OK:[/green] {free_gb:.1f}GB free")
    return True


def system_report():
    """Print full system status."""
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_count(logical=False)
    console.print(f"\n[bold]System Status[/bold]")
    console.print(f"  CPU cores (physical): {cpu}")
    console.print(f"  RAM total: {mem.total / 1024**3:.1f}GB")
    console.print(f"  RAM available: {mem.available / 1024**3:.1f}GB")
    console.print(f"  RAM usage: {mem.percent}%\n")
