#!/usr/bin/env python3
"""
bbcrom.py — ROM Manager for BBC Micro files (.rom)
==========================================================
Usage:
  bbcrom.py list    <file.rom>
  bbcrom.py extract <file.rom> <slot> [output.rom]
  bbcrom.py add     <file.rom> <slot> <new.rom> [--force] [--no-dup]
  bbcrom.py replace <file.rom> <slot> <new.rom>
  bbcrom.py clear   <file.rom> <slot> [--no-dup]

Global Options:
  -h, --help        Show this help message
  -v, --verbose     Detailed information (offsets, ROM type, etc.)

Examples:
  bbcrom.py list bbc.rom
  bbcrom.py list bbc.rom -v
  bbcrom.py extract bbc.rom 6 mmfs.rom
  bbcrom.py add bbc.rom 3 dfs.rom
  bbcrom.py add bbc.rom 3 dfs.rom --force
  bbcrom.py add bbc.rom 4 dfs.rom --force --no-dup
  bbcrom.py replace bbc.rom 17 DNFS302.rom
  bbcrom.py clear bbc.rom 8
"""

import sys
import os
import argparse
import hashlib
import shutil
from datetime import datetime

# ── Constants ────────────────────────────────────────────────────────────────

ROM_SIZE   = 16384          # 16 KB per slot
MAX_SLOTS  = 24             # Maximum slots supported by this manager
FILL_BYTE  = 0xFF           # Fill byte for empty slots / padding

# ── Basic Utilities ──────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
BLUE   = "\033[34m"
GRAY   = "\033[90m"

def color(text, *codes):
    """Applies ANSI codes if stdout is a terminal."""
    if sys.stdout.isatty():
        return "".join(codes) + text + RESET
    return text


def err(msg):
    print(color(f"Error: {msg}", RED, BOLD), file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(color(f"Warning: {msg}", YELLOW), file=sys.stderr)


def ok(msg):
    print(color(f"✓ {msg}", GREEN))


def info(msg):
    print(color(msg, CYAN))


# ── ROM Logic ─────────────────────────────────────────────────────────────────

def is_empty(data: bytes) -> bool:
    """Returns True if the slot is filled with 0xFF or 0x00 (empty)."""
    return all(b == 0xFF for b in data) or all(b == 0x00 for b in data)


def parse_header(data: bytes) -> dict:
    """
    Parses the standard sideways ROM header for BBC Micro.

    Offset  Size    Field
    0       3       JMP (language entry)  — or 0x00 if not a language ROM
    3       3       JMP (service entry)   — or 0x00 if no service entry
    6       1       ROM type byte
    7       1       Copyright pointer (offset from start of segment $8000)
    8       1       Version
    9       N       Title (ASCIIZ)
    9+N     …       Version string (ASCIIZ)   ← optional
    …
    copyright_ptr+1  Copyright string (ASCIIZ)
    """
    h = {}

    # Type
    rtype = data[6]
    h["type_raw"]    = rtype
    h["is_language"] = bool(rtype & 0x40)
    h["is_service"]  = bool(rtype & 0x80)

    flags = []
    if h["is_language"]: flags.append("Language")
    if h["is_service"]:  flags.append("Service")
    h["flags"] = "+".join(flags) if flags else "—"

    # Version
    h["version"] = data[8]

    # Title (ASCIIZ from offset 9)
    title = bytearray()
    for i in range(9, min(9 + 64, len(data))):
        if data[i] == 0:
            break
        if 0x20 <= data[i] <= 0x7E:
            title.append(data[i])
    h["title"] = title.decode("ascii", errors="replace").strip()

    # Copyright (points with relative ptr to $8000; the actual string starts after a 0x00)
    cp_ptr = data[7]
    copyright = ""
    try:
        cp_start = cp_ptr + 1        # The byte at cp_ptr is usually 0x00; the string follows
        if 0 < cp_start < len(data):
            for i in range(cp_start, min(cp_start + 80, len(data))):
                if data[i] == 0:
                    break
                if 0x20 <= data[i] <= 0x7E:
                    copyright += chr(data[i])
    except Exception:
        pass
    h["copyright"] = copyright

    # Entry Points
    lang_lo, lang_hi = data[1], data[2]
    svc_lo,  svc_hi  = data[4], data[5]
    h["lang_entry"] = lang_hi << 8 | lang_lo
    h["svc_entry"]  = svc_hi  << 8 | svc_lo

    return h


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()[:8]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Reading / Writing the ROM file ────────────────────────────────────────────

def load_rom_file(path: str) -> bytearray:
    if not os.path.isfile(path):
        err(f"File not found: {path}")
    size = os.path.getsize(path)
    if size % ROM_SIZE != 0:
        err(f"The file size ({size} bytes) is not a multiple of {ROM_SIZE} (16 KB).\n"
            f"       Check that it is a valid BBC Micro ROM file.")
    with open(path, "rb") as f:
        return bytearray(f.read())


def save_rom_file(path: str, data: bytearray, backup: bool = True):
    if backup and os.path.isfile(path):
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = f"{path}.{ts}.bak"
        shutil.copy2(path, bak)
        print(color(f"  Backup created: {bak}", GRAY))
    with open(path, "wb") as f:
        f.write(data)


def num_slots(data: bytearray) -> int:
    return len(data) // ROM_SIZE


def get_slot(data: bytearray, slot: int) -> bytes:
    offset = slot * ROM_SIZE
    return bytes(data[offset:offset + ROM_SIZE])


def set_slot(data: bytearray, slot: int, rom_data: bytes):
    """Writes rom_data (≤16 KB) into the slot, padding with 0xFF if necessary."""
    if len(rom_data) > ROM_SIZE:
        err(f"The ROM has {len(rom_data)} bytes and does not fit in a {ROM_SIZE} byte slot.")
    padded = rom_data + bytes([FILL_BYTE] * (ROM_SIZE - len(rom_data)))
    offset = slot * ROM_SIZE
    data[offset:offset + ROM_SIZE] = padded


def clear_slot(data: bytearray, slot: int):
    offset = slot * ROM_SIZE
    data[offset:offset + ROM_SIZE] = bytes([FILL_BYTE] * ROM_SIZE)


def find_duplicate(data: bytearray, slot: int) -> int | None:
    """
    Detects if the content of `slot` is identical to another slot.
    Returns the index of the first matching slot (other than `slot`) or None.
    """
    target = get_slot(data, slot)
    n = num_slots(data)
    for s in range(n):
        if s != slot and get_slot(data, s) == target:
            return s
    return None


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list(args):
    data  = load_rom_file(args.rom_file)
    n     = num_slots(data)
    path  = args.rom_file
    total = os.path.getsize(path)

    print()
    info(f"  {path}  ({total // 1024} KB · {n} slots × 16 KB)")
    print()

    # Detect duplicates for marking
    dup_map = {}   # slot → duplicate-of slot (first twin found)
    for s in range(n):
        if s in dup_map:
            continue
        chunk = get_slot(data, s)
        if is_empty(chunk):
            continue
        for t in range(s + 1, n):
            if get_slot(data, t) == chunk:
                dup_map[t] = s    # t is a duplicate of s

    # Group by bank (each bank = 8 slots = 128 KB)
    bank_labels = {
        0: "Bank A   (Model B)    · slots  0– 7 · 0x000000–0x01FFFF",
        1: "Bank B   (Master 128) · slots  8–15 · 0x020000–0x03FFFF",
        2: "Bank C   (Master 128) · slots 16–23 · 0x040000–0x05FFFF",
    }

    filled = 0
    free   = 0

    for s in range(n):
        bank = s // 8
        if s % 8 == 0:
            # Bank header
            print(color(f"  ── {bank_labels.get(bank, f'Bank {bank}')} ──", BLUE, BOLD))

        offset = s * ROM_SIZE
        chunk  = get_slot(data, s)
        slot_label = color(f"  Slot {s:2d}", BOLD)
        off_label  = color(f"0x{offset:06X}", GRAY)

        if is_empty(chunk):
            free += 1
            print(f"{slot_label}  {off_label}  {color('[empty]', DIM)}")
            continue

        filled += 1
        h = parse_header(chunk)

        # Title with duplicate status
        title    = h["title"] or "—"
        dup_note = ""
        if s in dup_map:
            dup_note = color(f"  [≡ dup of slot {dup_map[s]}]", DIM)
        elif find_duplicate(data, s) is not None:
            twin = find_duplicate(data, s)
            dup_note = color(f"  [≡ dup → slot {twin}]", DIM)

        type_str = color(h["flags"], YELLOW if h["flags"] != "—" else GRAY)
        ver_str  = color(f"v{h['version']}", GRAY)
        cp_str   = color(h["copyright"][:40] if h["copyright"] else "", GRAY)

        print(f"{slot_label}  {off_label}  {color(title, GREEN, BOLD):<30} "
              f"{type_str:<22} {ver_str}  {cp_str}{dup_note}")

        if args.verbose:
            print(color(f"           Lang: ${h['lang_entry']:04X}  "
                        f"Svc: ${h['svc_entry']:04X}  "
                        f"Type: 0x{h['type_raw']:02X}  "
                        f"MD5: {md5(chunk)}", GRAY))

    print()
    print(color(f"  Summary: {filled} slots occupied · {free} free "
                f"({free * 16} KB available)", BOLD))

    # Detect total duplicates
    n_dups = len(dup_map)
    if n_dups:
        dup_list = ", ".join(f"{t}≡{v}" for t, v in sorted(dup_map.items()))
        print(color(f"           {n_dups} slots are exact duplicates: {dup_list}", DIM))
    print()


def cmd_extract(args):
    data = load_rom_file(args.rom_file)
    n    = num_slots(data)

    if args.slot < 0 or args.slot >= n:
        err(f"Slot {args.slot} out of range (0–{n - 1}).")

    chunk = get_slot(data, args.slot)

    if is_empty(chunk):
        err(f"Slot {args.slot} is empty (0xFF/0x00).")

    # Output filename: if not specified, use the ROM title
    if args.output:
        out_path = args.output
    else:
        h = parse_header(chunk)
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in h["title"]) or f"slot{args.slot}"
        out_path = f"{safe}.rom"

    # Calculate real size (trim trailing 0xFF)
    end = ROM_SIZE
    while end > 0 and chunk[end - 1] == 0xFF:
        end -= 1
    real_data = chunk[:end] if end > 0 else chunk

    with open(out_path, "wb") as f:
        f.write(real_data)

    h = parse_header(chunk)
    ok(f"Extracted slot {args.slot} → {out_path}  "
       f"({end} useful bytes · '{h['title']}')")


def cmd_add(args):
    """
    Adds a ROM to a free slot. With --force it overwrites even if not empty.
    With --no-dup it does not duplicate in the Bank A twin slot.
    """
    data = load_rom_file(args.rom_file)
    n    = num_slots(data)

    if args.slot < 0 or args.slot >= n:
        err(f"Slot {args.slot} out of range (0–{n - 1}).")

    chunk = get_slot(data, args.slot)

    if not is_empty(chunk) and not args.force:
        h = parse_header(chunk)
        err(f"Slot {args.slot} already contains '{h['title']}'. "
            f"Use --force to overwrite, or 'replace' to explicitly replace.")

    # Read the new ROM
    if not os.path.isfile(args.new_rom):
        err(f"ROM file not found: {args.new_rom}")
    with open(args.new_rom, "rb") as f:
        new_data = f.read()

    if len(new_data) > ROM_SIZE:
        err(f"The ROM ({len(new_data)} bytes) is larger than a slot ({ROM_SIZE} bytes).")

    # Validate that it looks like a sideways ROM (basic heuristic)
    if len(new_data) >= 10:
        h_new = parse_header(new_data)
        if not h_new["title"]:
            warn("The ROM does not have a title in the header. "
                 "It may not be a standard sideways ROM.")
    else:
        warn("The ROM is very small; check that it is the correct file.")

    # Write slot
    set_slot(data, args.slot, new_data)
    h_new = parse_header(new_data)

    print()
    info(f"  Writing '{h_new['title']}' in slot {args.slot} "
         f"(0x{args.slot * ROM_SIZE:06X})…")

    # Duplicate in Bank A twin pair if applicable (slots 0-7 are pairs 0=2, 1=3, 4=5, 6=7)
    dup_slot = None
    if not args.no_dup and args.slot < 8:
        # Bank A duplicate pattern: (0,2),(1,3),(4,5),(6,7)
        pair_map = {0: 2, 2: 0, 1: 3, 3: 1, 4: 5, 5: 4, 6: 7, 7: 6}
        if args.slot in pair_map:
            dup_slot = pair_map[args.slot]
            peer_chunk = get_slot(data, dup_slot)
            if is_empty(peer_chunk) or args.force:
                set_slot(data, dup_slot, new_data)
                info(f"  Automatic duplication → slot {dup_slot} "
                     f"(Bank A pattern). Use --no-dup to avoid this.")
            else:
                h_peer = parse_header(peer_chunk)
                warn(f"The twin slot {dup_slot} contains '{h_peer['title']}' "
                     f"and has not been duplicated (use --force to overwrite).")
                dup_slot = None

    save_rom_file(args.rom_file, data)
    msg = f"'{h_new['title']}' added to slot {args.slot}"
    if dup_slot is not None:
        msg += f" (and duplicated in slot {dup_slot})"
    ok(msg)
    print()


def cmd_replace(args):
    """
    Replaces the ROM in a slot regardless of whether it is empty or not.
    Alias for add --force, but more semantic: expects the slot NOT to be empty.
    """
    data = load_rom_file(args.rom_file)
    n    = num_slots(data)

    if args.slot < 0 or args.slot >= n:
        err(f"Slot {args.slot} out of range (0–{n - 1}).")

    chunk = get_slot(data, args.slot)
    if is_empty(chunk):
        warn(f"Slot {args.slot} is empty. Use 'add' to insert a new ROM.")

    if not os.path.isfile(args.new_rom):
        err(f"ROM file not found: {args.new_rom}")
    with open(args.new_rom, "rb") as f:
        new_data = f.read()

    if len(new_data) > ROM_SIZE:
        err(f"The ROM ({len(new_data)} bytes) is larger than a slot ({ROM_SIZE} bytes).")

    h_old = parse_header(chunk) if not is_empty(chunk) else {"title": "[empty]"}
    h_new = parse_header(new_data)

    print()
    info(f"  Replacing slot {args.slot}: '{h_old['title']}'"
         f"  →  '{h_new['title']}'")

    set_slot(data, args.slot, new_data)
    save_rom_file(args.rom_file, data)
    ok(f"Slot {args.slot} replaced with '{h_new['title']}'")

    # Warn if there are duplicates of the original slot that were not updated
    old_twin = find_duplicate(data, args.slot)
    if old_twin is not None:
        warn(f"Slot {old_twin} still contains the PREVIOUS content. "
             f"Use 'replace' on that slot as well if you want to maintain consistency.")
    print()


def cmd_clear(args):
    """Clears a slot (fills it with 0xFF)."""
    data = load_rom_file(args.rom_file)
    n    = num_slots(data)

    if args.slot < 0 or args.slot >= n:
        err(f"Slot {args.slot} out of range (0–{n - 1}).")

    chunk = get_slot(data, args.slot)
    if is_empty(chunk):
        warn(f"Slot {args.slot} is already empty.")
        return

    h = parse_header(chunk)
    print()
    info(f"  Clearing slot {args.slot}: '{h['title']}'…")

    clear_slot(data, args.slot)

    # Optionally clear the duplicated twin slot in Bank A
    if not args.no_dup and args.slot < 8:
        pair_map = {0: 2, 2: 0, 1: 3, 3: 1, 4: 5, 5: 4, 6: 7, 7: 6}
        if args.slot in pair_map:
            dup_slot = pair_map[args.slot]
            peer = get_slot(data, dup_slot)
            if peer == chunk:     # Only if it was identical (actual duplicate)
                clear_slot(data, dup_slot)
                info(f"  Also cleared the duplicate in slot {dup_slot}.")

    save_rom_file(args.rom_file, data)
    ok(f"Slot {args.slot} cleared.")
    print()


# ── Parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bbcrom.py",
        description="ROM Manager for BBC Micro files (.rom)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list bbc.rom
  %(prog)s list bbc.rom -v
  %(prog)s extract bbc.rom 6
  %(prog)s extract bbc.rom 6 mmfs_slot6.rom
  %(prog)s add bbc.rom 3 TUBE.rom
  %(prog)s add bbc.rom 3 TUBE.rom --force
  %(prog)s add bbc.rom 3 TUBE.rom --no-dup
  %(prog)s replace bbc.rom 17 DNFS302.rom
  %(prog)s clear bbc.rom 8
        """,
    )

    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ── list ──────────────────────────────────────────────────────
    pl = sub.add_parser("list", help="List all ROMs in the file")
    pl.add_argument("rom_file", metavar="file.rom")
    pl.add_argument("-v", "--verbose", action="store_true",
                    help="Show offsets, entry vectors, and MD5")

    # ── extract ───────────────────────────────────────────────────
    pe = sub.add_parser("extract", help="Extract a ROM from a slot to a file")
    pe.add_argument("rom_file", metavar="file.rom")
    pe.add_argument("slot",     metavar="SLOT", type=int)
    pe.add_argument("output",   metavar="output.rom", nargs="?", default=None,
                    help="Output filename (default: <title>.rom)")

    # ── add ───────────────────────────────────────────────────────
    pa = sub.add_parser("add", help="Add a ROM to a free slot")
    pa.add_argument("rom_file", metavar="file.rom")
    pa.add_argument("slot",     metavar="SLOT", type=int)
    pa.add_argument("new_rom",  metavar="new.rom")
    pa.add_argument("--force",  action="store_true",
                    help="Overwrite even if the slot is not empty")
    pa.add_argument("--no-dup", action="store_true",
                    help="Do not duplicate in the twin Bank A slot")

    # ── replace ───────────────────────────────────────────────────
    pr = sub.add_parser("replace", help="Replace the ROM in an existing slot")
    pr.add_argument("rom_file", metavar="file.rom")
    pr.add_argument("slot",     metavar="SLOT", type=int)
    pr.add_argument("new_rom",  metavar="new.rom")

    # ── clear ─────────────────────────────────────────────────────
    pc = sub.add_parser("clear", help="Clear a slot (fill with 0xFF)")
    pc.add_argument("rom_file", metavar="file.rom")
    pc.add_argument("slot",     metavar="SLOT", type=int)
    pc.add_argument("--no-dup", action="store_true",
                    help="Do not clear the duplicate twin slot in Bank A")

    return p


# ── Main ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "list":    cmd_list,
    "extract": cmd_extract,
    "add":     cmd_add,
    "replace": cmd_replace,
    "clear":   cmd_clear,
}


def main():
    parser = build_parser()
    args   = parser.parse_args()
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()