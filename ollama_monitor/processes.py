"""Find Ollama roots and all descendants from a single process snapshot."""
import os
import re
from .io import run, percent


def parse_processes(text):
    if text is None:
        return None
    rows = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 7)
        if len(parts) != 8:
            continue
        try:
            pid, ppid, cpu, mem, rss, elapsed, exe, command = parts
            rows[int(pid)] = dict(pid=int(pid), ppid=int(ppid), cpu=float(cpu),
                                  rss_bytes=int(rss) * 1024, elapsed=elapsed,
                                  exe=os.path.basename(exe), command=command)
        except ValueError:
            continue
    if not rows:  # ps failure/unsupported format must not turn into zero RAM.
        return None

    def belongs(row):
        seen = set()
        while row and row["pid"] not in seen:
            if row["exe"] in {"ollama", "Ollama"}:
                return True
            seen.add(row["pid"])
            row = rows.get(row["ppid"])
        return False

    return [row for row in rows.values() if belongs(row)]


def get_processes():
    return parse_processes(run(["ps", "-ww", "-axo",
                               "pid=,ppid=,%cpu=,%mem=,rss=,etime=,ucomm=,command="]))


def runner_processes(rows):
    result = []
    for row in rows or []:
        # MLX wrappers are eligible only after process-tree ownership was proven.
        cmd = row["command"]
        if not (row["exe"] == "llama-server" or
                re.search(r"(?:^|[/\s])ollama\s+runner(?:\s|$)", cmd) or
                re.search(r"(?:mlx|runner)", row["exe"], re.I) or
                re.search(r"(?:^|\s)-m\s+\S*mlx\S*", cmd)):
            continue
        port = re.search(r"(?:^|\s)--port(?:\s+|=)(\d+)(?=\s|$)", cmd)
        if port and 0 < int(port[1]) <= 65535:
            result.append({**row, "port": int(port[1])})
    return result


def get_ollama_memory_metrics(rows, total):
    # Each PID occurs once; do not add /api/ps sizes to the same resident pages.
    unique = {r["pid"]: r for r in rows or []}
    used = sum(r["rss_bytes"] for r in unique.values()) if rows is not None else None
    return {"used_bytes": used, "used_percent_of_system_ram": percent(used, total),
            "process_count": len(unique) if rows is not None else None,
            "cpu_percent": sum(r["cpu"] for r in unique.values()) if rows is not None else None,
            "source": "process_tree_rss", "estimated": True,
            "note": "Sum of resident sets; shared pages may overlap; compressed pages are excluded."}
