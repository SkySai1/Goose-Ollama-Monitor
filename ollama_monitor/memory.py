"""macOS memory metrics, with explicit definitions and missing-data semantics."""
import re
from .io import percent, run


def parse_vm_stat(text, total):
    empty = {"total_bytes": total, "used_bytes": None, "used_percent": None,
             "compressed_bytes": None, "source": "vm_stat"}
    if not text or not total:
        return empty
    page_match = re.search(r"page size of (\d+) bytes", text)
    fields = {m[1]: int(m[2]) for m in re.finditer(r"^([^:\n]+):\s+(\d+)\.", text, re.M)}
    needed = ("Anonymous pages", "Pages wired down", "Pages occupied by compressor")
    if not page_match or any(k not in fields for k in needed):
        return empty
    page = int(page_match[1])
    # Anonymous (active + inactive internal) + wired + physical compressor storage.
    # Clean file cache/free/speculative pages are reclaimable. Do not count logical
    # "Pages stored in compressor" a second time as physical RAM.
    used = sum(fields[k] for k in needed) * page
    if not 0 <= used <= total:
        return empty
    return {**empty, "used_bytes": used, "used_percent": percent(used, total),
            "compressed_bytes": fields["Pages occupied by compressor"] * page}


def parse_swap(text):
    match = re.search(r"\bused\s*=\s*([\d.]+)([KMGT]?)", text or "")
    return int(float(match[1]) * 1024 ** " KMGT".index(match[2] or " ")) if match else None


def get_system_memory_metrics():
    raw_total = run(["sysctl", "-n", "hw.memsize"])
    total = int(raw_total.strip()) if raw_total and raw_total.strip().isdigit() else None
    result = parse_vm_stat(run(["vm_stat"]), total)
    result["swap_used_bytes"] = parse_swap(run(["sysctl", "-n", "vm.swapusage"]))
    return result


def get_memory_pressure_metrics():
    text = run(["memory_pressure", "-Q"])
    match = re.search(r"System-wide memory free percentage\s*[:=]\s*(\d+(?:\.\d+)?)%", text or "")
    free = float(match[1]) if match else None
    if free is not None and not 0 <= free <= 100:
        free = None
    # Derived normalization, NOT Apple's Activity Monitor pressure value.
    memory_pressure_normalized_percent = 100 - free if free is not None else None
    return {"free_percent": free, "pressure_percent": memory_pressure_normalized_percent,
            "source": "memory_pressure -Q", "derived": True}
