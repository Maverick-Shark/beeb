#!/usr/bin/env python3
"""
bbcrom.py — Gestor de ROMs para ficheros BBC Micro (.rom)
==========================================================
Uso:
  bbcrom.py list    <fichero.rom>
  bbcrom.py extract <fichero.rom> <slot> [salida.rom]
  bbcrom.py add     <fichero.rom> <slot> <nueva.rom> [--force] [--no-dup]
  bbcrom.py replace <fichero.rom> <slot> <nueva.rom>
  bbcrom.py clear   <fichero.rom> <slot> [--no-dup]

Opciones globales:
  -h, --help        Muestra esta ayuda
  -v, --verbose     Información detallada (offsets, tipo ROM, etc.)

Ejemplos:
  bbcrom.py list bbc.rom
  bbcrom.py list bbc.rom -v
  bbcrom.py extract bbc.rom 6 mmfs.rom
  bbcrom.py add bbc.rom 3 TUBE.rom
  bbcrom.py add bbc.rom 3 TUBE.rom --force
  bbcrom.py replace bbc.rom 17 DNFS302.rom
  bbcrom.py clear bbc.rom 8
"""

import sys
import os
import argparse
import hashlib
import shutil
from datetime import datetime

# ── Constantes ────────────────────────────────────────────────────────────────

ROM_SIZE   = 16384          # 16 KB por slot
MAX_SLOTS  = 24             # slots máximos que soporta este gestor
FILL_BYTE  = 0xFF           # byte de relleno para slots vacíos / padding

# ── Utilidades básicas ────────────────────────────────────────────────────────

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
    """Aplica códigos ANSI si stdout es una terminal."""
    if sys.stdout.isatty():
        return "".join(codes) + text + RESET
    return text


def err(msg):
    print(color(f"Error: {msg}", RED, BOLD), file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(color(f"Aviso: {msg}", YELLOW), file=sys.stderr)


def ok(msg):
    print(color(f"✓ {msg}", GREEN))


def info(msg):
    print(color(msg, CYAN))


# ── Lógica de ROM ─────────────────────────────────────────────────────────────

def is_empty(data: bytes) -> bool:
    """Devuelve True si el slot está lleno de 0xFF o 0x00 (vacío)."""
    return all(b == 0xFF for b in data) or all(b == 0x00 for b in data)


def parse_header(data: bytes) -> dict:
    """
    Parsea el encabezado estándar de una sideways ROM BBC Micro.

    Offset  Tamaño  Campo
    0       3       JMP (language entry)  — o 0x00 si no es language ROM
    3       3       JMP (service entry)   — o 0x00 si no tiene service
    6       1       ROM type byte
    7       1       Copyright pointer (offset desde inicio del segmento $8000)
    8       1       Versión
    9       N       Título (ASCIIZ)
    9+N     …       Cadena de versión (ASCIIZ)   ← opcional
    …
    copyright_ptr+1  Copyright string (ASCIIZ)
    """
    h = {}

    # Tipo
    rtype = data[6]
    h["type_raw"]    = rtype
    h["is_language"] = bool(rtype & 0x40)
    h["is_service"]  = bool(rtype & 0x80)

    flags = []
    if h["is_language"]: flags.append("Language")
    if h["is_service"]:  flags.append("Service")
    h["flags"] = "+".join(flags) if flags else "—"

    # Versión
    h["version"] = data[8]

    # Título (ASCIIZ desde offset 9)
    title = bytearray()
    for i in range(9, min(9 + 64, len(data))):
        if data[i] == 0:
            break
        if 0x20 <= data[i] <= 0x7E:
            title.append(data[i])
    h["title"] = title.decode("ascii", errors="replace").strip()

    # Copyright (apunta con ptr relativo a $8000; la cadena real está tras un 0x00)
    cp_ptr = data[7]
    copyright = ""
    try:
        cp_start = cp_ptr + 1        # el byte en cp_ptr suele ser 0x00; la cadena empieza tras él
        if 0 < cp_start < len(data):
            for i in range(cp_start, min(cp_start + 80, len(data))):
                if data[i] == 0:
                    break
                if 0x20 <= data[i] <= 0x7E:
                    copyright += chr(data[i])
    except Exception:
        pass
    h["copyright"] = copyright

    # Puntos de entrada
    lang_lo, lang_hi = data[1], data[2]
    svc_lo,  svc_hi  = data[4], data[5]
    h["lang_entry"] = lang_hi << 8 | lang_lo
    h["svc_entry"]  = svc_hi  << 8 | svc_lo

    return h


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()[:8]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Lectura / escritura del fichero de ROMs ───────────────────────────────────

def load_rom_file(path: str) -> bytearray:
    if not os.path.isfile(path):
        err(f"Fichero no encontrado: {path}")
    size = os.path.getsize(path)
    if size % ROM_SIZE != 0:
        err(f"El tamaño del fichero ({size} bytes) no es múltiplo de {ROM_SIZE} (16 KB).\n"
            f"       Comprueba que sea un fichero de ROMs BBC Micro válido.")
    with open(path, "rb") as f:
        return bytearray(f.read())


def save_rom_file(path: str, data: bytearray, backup: bool = True):
    if backup and os.path.isfile(path):
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = f"{path}.{ts}.bak"
        shutil.copy2(path, bak)
        print(color(f"  Copia de seguridad: {bak}", GRAY))
    with open(path, "wb") as f:
        f.write(data)


def num_slots(data: bytearray) -> int:
    return len(data) // ROM_SIZE


def get_slot(data: bytearray, slot: int) -> bytes:
    offset = slot * ROM_SIZE
    return bytes(data[offset:offset + ROM_SIZE])


def set_slot(data: bytearray, slot: int, rom_data: bytes):
    """Escribe rom_data (≤16 KB) en el slot, rellenando con 0xFF si es necesario."""
    if len(rom_data) > ROM_SIZE:
        err(f"La ROM tiene {len(rom_data)} bytes y no cabe en un slot de {ROM_SIZE} bytes.")
    padded = rom_data + bytes([FILL_BYTE] * (ROM_SIZE - len(rom_data)))
    offset = slot * ROM_SIZE
    data[offset:offset + ROM_SIZE] = padded


def clear_slot(data: bytearray, slot: int):
    offset = slot * ROM_SIZE
    data[offset:offset + ROM_SIZE] = bytes([FILL_BYTE] * ROM_SIZE)


def find_duplicate(data: bytearray, slot: int) -> int | None:
    """
    Detecta si el contenido de `slot` es idéntico al de otro slot.
    Devuelve el índice del primer slot igual (distinto de `slot`) o None.
    """
    target = get_slot(data, slot)
    n = num_slots(data)
    for s in range(n):
        if s != slot and get_slot(data, s) == target:
            return s
    return None


# ── Comandos ──────────────────────────────────────────────────────────────────

def cmd_list(args):
    data  = load_rom_file(args.rom_file)
    n     = num_slots(data)
    path  = args.rom_file
    total = os.path.getsize(path)

    print()
    info(f"  {path}  ({total // 1024} KB · {n} slots × 16 KB)")
    print()

    # Detectar duplicados para marcarlos
    dup_map = {}   # slot → slot gemelo (primer gemelo encontrado)
    for s in range(n):
        if s in dup_map:
            continue
        chunk = get_slot(data, s)
        if is_empty(chunk):
            continue
        for t in range(s + 1, n):
            if get_slot(data, t) == chunk:
                dup_map[t] = s    # t es duplicado de s

    # Agrupar por banco (cada banco = 8 slots = 128 KB)
    banco_labels = {
        0: "Banco A  (Model B)   · slots  0– 7 · 0x000000–0x01FFFF",
        1: "Banco B  (Master 128)· slots  8–15 · 0x020000–0x03FFFF",
        2: "Banco C  (Master 128)· slots 16–23 · 0x040000–0x05FFFF",
    }

    filled = 0
    free   = 0

    for s in range(n):
        banco = s // 8
        if s % 8 == 0:
            # Cabecera de banco
            print(color(f"  ── {banco_labels.get(banco, f'Banco {banco}')} ──", BLUE, BOLD))

        offset = s * ROM_SIZE
        chunk  = get_slot(data, s)
        slot_label = color(f"  Slot {s:2d}", BOLD)
        off_label  = color(f"0x{offset:06X}", GRAY)

        if is_empty(chunk):
            free += 1
            print(f"{slot_label}  {off_label}  {color('[libre]', DIM)}")
            continue

        filled += 1
        h = parse_header(chunk)

        # Título con estado de duplicado
        title    = h["title"] or "—"
        dup_note = ""
        if s in dup_map:
            dup_note = color(f"  [≡ dup de slot {dup_map[s]}]", DIM)
        elif find_duplicate(data, s) is not None:
            gemelo = find_duplicate(data, s)
            dup_note = color(f"  [≡ dup → slot {gemelo}]", DIM)

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
    print(color(f"  Resumen: {filled} slots ocupados · {free} libres "
                f"({free * 16} KB disponibles)", BOLD))

    # Detectar duplicados totales
    n_dups = len(dup_map)
    if n_dups:
        dup_list = ", ".join(f"{t}≡{v}" for t, v in sorted(dup_map.items()))
        print(color(f"           {n_dups} slots son duplicados exactos: {dup_list}", DIM))
    print()


def cmd_extract(args):
    data = load_rom_file(args.rom_file)
    n    = num_slots(data)

    if args.slot < 0 or args.slot >= n:
        err(f"Slot {args.slot} fuera de rango (0–{n - 1}).")

    chunk = get_slot(data, args.slot)

    if is_empty(chunk):
        err(f"El slot {args.slot} está vacío (0xFF/0x00).")

    # Nombre de salida: si no se especifica, usar el título de la ROM
    if args.output:
        out_path = args.output
    else:
        h = parse_header(chunk)
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in h["title"]) or f"slot{args.slot}"
        out_path = f"{safe}.rom"

    # Calcular tamaño real (recortar trailing 0xFF)
    end = ROM_SIZE
    while end > 0 and chunk[end - 1] == 0xFF:
        end -= 1
    real_data = chunk[:end] if end > 0 else chunk

    with open(out_path, "wb") as f:
        f.write(real_data)

    h = parse_header(chunk)
    ok(f"Extraído slot {args.slot} → {out_path}  "
       f"({end} bytes útiles · '{h['title']}')")


def cmd_add(args):
    """
    Añade una ROM en un slot libre. Con --force escribe aunque no esté vacío.
    Con --no-dup no duplica en el par gemelo del Banco A.
    """
    data = load_rom_file(args.rom_file)
    n    = num_slots(data)

    if args.slot < 0 or args.slot >= n:
        err(f"Slot {args.slot} fuera de rango (0–{n - 1}).")

    chunk = get_slot(data, args.slot)

    if not is_empty(chunk) and not args.force:
        h = parse_header(chunk)
        err(f"El slot {args.slot} ya contiene '{h['title']}'. "
            f"Usa --force para sobreescribir, o 'replace' para reemplazar explícitamente.")

    # Leer la nueva ROM
    if not os.path.isfile(args.new_rom):
        err(f"Fichero ROM no encontrado: {args.new_rom}")
    with open(args.new_rom, "rb") as f:
        new_data = f.read()

    if len(new_data) > ROM_SIZE:
        err(f"La ROM ({len(new_data)} bytes) es mayor que un slot ({ROM_SIZE} bytes).")

    # Validar que parece una sideways ROM (heurística básica)
    if len(new_data) >= 10:
        h_new = parse_header(new_data)
        if not h_new["title"]:
            warn("La ROM no tiene título en el encabezado. "
                 "Puede que no sea una sideways ROM estándar.")
    else:
        warn("La ROM es muy pequeña; comprueba que sea el fichero correcto.")

    # Escribir slot
    set_slot(data, args.slot, new_data)
    h_new = parse_header(new_data)

    print()
    info(f"  Escribiendo '{h_new['title']}' en slot {args.slot} "
         f"(0x{args.slot * ROM_SIZE:06X})…")

    # Duplicar en par gemelo del Banco A si aplica (slots 0-7 van en pares 0=2, 1=3, 4=5, 6=7)
    dup_slot = None
    if not args.no_dup and args.slot < 8:
        # Patrón de duplicados en Banco A: (0,2),(1,3),(4,5),(6,7)
        pair_map = {0: 2, 2: 0, 1: 3, 3: 1, 4: 5, 5: 4, 6: 7, 7: 6}
        if args.slot in pair_map:
            dup_slot = pair_map[args.slot]
            peer_chunk = get_slot(data, dup_slot)
            if is_empty(peer_chunk) or args.force:
                set_slot(data, dup_slot, new_data)
                info(f"  Duplicado automático → slot {dup_slot} "
                     f"(patrón Banco A). Usa --no-dup para evitarlo.")
            else:
                h_peer = parse_header(peer_chunk)
                warn(f"El slot par {dup_slot} contiene '{h_peer['title']}' "
                     f"y no se ha duplicado (usa --force para sobreescribir).")
                dup_slot = None

    save_rom_file(args.rom_file, data)
    msg = f"'{h_new['title']}' añadida en slot {args.slot}"
    if dup_slot is not None:
        msg += f" (y duplicada en slot {dup_slot})"
    ok(msg)
    print()


def cmd_replace(args):
    """
    Reemplaza la ROM de un slot sin importar si está vacío o no.
    Alias de add --force, pero más semántico: exige que el slot NO esté vacío.
    """
    data = load_rom_file(args.rom_file)
    n    = num_slots(data)

    if args.slot < 0 or args.slot >= n:
        err(f"Slot {args.slot} fuera de rango (0–{n - 1}).")

    chunk = get_slot(data, args.slot)
    if is_empty(chunk):
        warn(f"El slot {args.slot} está vacío. Usa 'add' para insertar una ROM nueva.")

    if not os.path.isfile(args.new_rom):
        err(f"Fichero ROM no encontrado: {args.new_rom}")
    with open(args.new_rom, "rb") as f:
        new_data = f.read()

    if len(new_data) > ROM_SIZE:
        err(f"La ROM ({len(new_data)} bytes) es mayor que un slot ({ROM_SIZE} bytes).")

    h_old = parse_header(chunk) if not is_empty(chunk) else {"title": "[vacío]"}
    h_new = parse_header(new_data)

    print()
    info(f"  Reemplazando slot {args.slot}: '{h_old['title']}'"
         f"  →  '{h_new['title']}'")

    set_slot(data, args.slot, new_data)
    save_rom_file(args.rom_file, data)
    ok(f"Slot {args.slot} reemplazado con '{h_new['title']}'")

    # Advertir si hay duplicados del slot original que no se han actualizado
    old_twin = find_duplicate(data, args.slot)
    if old_twin is not None:
        warn(f"El slot {old_twin} sigue siendo el contenido ANTERIOR. "
             f"Usa 'replace' también en ese slot si quieres mantener la coherencia.")
    print()


def cmd_clear(args):
    """Borra un slot (lo rellena con 0xFF)."""
    data = load_rom_file(args.rom_file)
    n    = num_slots(data)

    if args.slot < 0 or args.slot >= n:
        err(f"Slot {args.slot} fuera de rango (0–{n - 1}).")

    chunk = get_slot(data, args.slot)
    if is_empty(chunk):
        warn(f"El slot {args.slot} ya está vacío.")
        return

    h = parse_header(chunk)
    print()
    info(f"  Borrando slot {args.slot}: '{h['title']}'…")

    clear_slot(data, args.slot)

    # Opcionalmente borrar el slot par del Banco A
    if not args.no_dup and args.slot < 8:
        pair_map = {0: 2, 2: 0, 1: 3, 3: 1, 4: 5, 5: 4, 6: 7, 7: 6}
        if args.slot in pair_map:
            dup_slot = pair_map[args.slot]
            peer = get_slot(data, dup_slot)
            if peer == chunk:     # sólo si era idéntico (duplicado real)
                clear_slot(data, dup_slot)
                info(f"  También borrado el duplicado en slot {dup_slot}.")

    save_rom_file(args.rom_file, data)
    ok(f"Slot {args.slot} borrado.")
    print()


# ── Parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bbcrom.py",
        description="Gestor de ROMs para ficheros BBC Micro (.rom)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
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

    sub = p.add_subparsers(dest="command", metavar="COMANDO")
    sub.required = True

    # ── list ──────────────────────────────────────────────────────
    pl = sub.add_parser("list", help="Lista todas las ROMs del fichero")
    pl.add_argument("rom_file", metavar="fichero.rom")
    pl.add_argument("-v", "--verbose", action="store_true",
                    help="Muestra offsets, vectores de entrada y MD5")

    # ── extract ───────────────────────────────────────────────────
    pe = sub.add_parser("extract", help="Extrae la ROM de un slot a un fichero")
    pe.add_argument("rom_file", metavar="fichero.rom")
    pe.add_argument("slot",     metavar="SLOT", type=int)
    pe.add_argument("output",   metavar="salida.rom", nargs="?", default=None,
                    help="Nombre del fichero de salida (por defecto: <título>.rom)")

    # ── add ───────────────────────────────────────────────────────
    pa = sub.add_parser("add", help="Añade una ROM en un slot libre")
    pa.add_argument("rom_file", metavar="fichero.rom")
    pa.add_argument("slot",     metavar="SLOT", type=int)
    pa.add_argument("new_rom",  metavar="nueva.rom")
    pa.add_argument("--force",  action="store_true",
                    help="Sobreescribe aunque el slot no esté vacío")
    pa.add_argument("--no-dup", action="store_true",
                    help="No duplica en el slot par del Banco A")

    # ── replace ───────────────────────────────────────────────────
    pr = sub.add_parser("replace", help="Reemplaza la ROM de un slot existente")
    pr.add_argument("rom_file", metavar="fichero.rom")
    pr.add_argument("slot",     metavar="SLOT", type=int)
    pr.add_argument("new_rom",  metavar="nueva.rom")

    # ── clear ─────────────────────────────────────────────────────
    pc = sub.add_parser("clear", help="Borra un slot (lo rellena con 0xFF)")
    pc.add_argument("rom_file", metavar="fichero.rom")
    pc.add_argument("slot",     metavar="SLOT", type=int)
    pc.add_argument("--no-dup", action="store_true",
                    help="No borra el slot par duplicado en el Banco A")

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
