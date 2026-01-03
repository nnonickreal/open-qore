#!/usr/bin/env python3
import os
import struct
import subprocess
import sys
from pathlib import Path

# ================= CONFIGURATION =================
FIRMWARE_FILE = "firmware.bin"
OUTPUT_FILE = "firmware_patched.bin"
SOUNDS_DIR = "sounds_src"

BASE_ADDRESS = 0x3C000000
ALIGN = 4
MIN_EMPTY_SPACE = 1024 * 1024  # 1 MB minimum empty space required

FFMPEG_BIN = "ffmpeg"

# ================= PATCH MAP =================
PATCH_MAP = {
    "ID_00.wav": {"size_instr_addr": 0x3c0d1a8c, "ptr_pool_addr": 0x3c0d1c54},
    "ID_01.wav": {"size_instr_addr": 0x3c0d1b8c, "ptr_pool_addr": 0x3c0d1c98},
    "ID_13.wav": {"size_instr_addr": 0x3c0d1a6c, "ptr_pool_addr": 0x3c0d1c4c},
    "ID_15.wav": {"size_instr_addr": 0x3c0d1a5c, "ptr_pool_addr": 0x3c0d1c48},
    "ID_23.wav": {"size_instr_addr": 0x3c0d1a38, "ptr_pool_addr": 0x3c0d1c40},
    "ID_29.wav": {"size_instr_addr": 0x3c0d1a7c, "ptr_pool_addr": 0x3c0d1c50},
    "ID_37.wav": {"size_instr_addr": 0x3c0d1bb0, "ptr_pool_addr": 0x3c0d1cac},
    "ID_38.wav": {"size_instr_addr": 0x3c0d1abc, "ptr_pool_addr": 0x3c0d1c60},
    "ID_40.wav": {"size_instr_addr": 0x3c0d1aac, "ptr_pool_addr": 0x3c0d1c5c},
    "ID_41.wav": {"size_instr_addr": 0x3c0d1a9c, "ptr_pool_addr": 0x3c0d1c58},
    "ID_42.wav": {"size_instr_addr": 0x3c0d1bf2, "ptr_pool_addr": 0x3c0d1cbc},
}

# ================= UTILITIES =================
def va_to_file_off(va: int) -> int:
    off = va - BASE_ADDRESS
    if off < 0:
        raise ValueError(f"va 0x{va:X} < base_address 0x{BASE_ADDRESS:X}")
    return off

def read_u32_le(buf: bytearray, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]

def write_u32_le(buf: bytearray, off: int, val: int) -> None:
    struct.pack_into("<I", buf, off, val)

def run_cmd(cmd: list[str]) -> bytes:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError(f"binary not found: {cmd[0]}")
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", errors="replace").strip())
    return p.stdout

def encode_wav_to_sbc(input_path: str) -> bytes:
    # simple conversion: 48khz mono -> raw sbc frames to stdout
    cmd = [
        FFMPEG_BIN, "-y", "-v", "error",
        "-i", input_path,
        "-ar", "48000",
        "-ac", "1",
        "-f", "sbc",
        "-"
    ]
    data = run_cmd(cmd)
    if not data:
        raise RuntimeError("ffmpeg returned empty data")
    # sbc frame syncword is usually 0x9c
    if data[0] != 0x9C:
        raise RuntimeError(f"sbc does not start with 0x9c (first byte: 0x{data[0]:02X}). "
                           f"looks like invalid output format.")
    return data

# ================= Thumb-2 MOVW (T3) =================
# encoding:
#  first halfword: 11110 i 100100 imm4   => base 0xf240, i in bit10, imm4 in bits[3:0]
#  second halfword: 0 imm3 rd imm8       => imm3 bits[14:12], rd bits[11:8], imm8 bits[7:0]
# imm16 = imm4<<12 | i<<11 | imm3<<8 | imm8

def decode_movw_thumb2(instr4: bytes) -> tuple[int, int]:
    """returns (rd, imm16). raises valueerror if not movw (t3)."""
    if len(instr4) != 4:
        raise ValueError("instr4 must be exactly 4 bytes")
    hw1, hw2 = struct.unpack("<HH", instr4)

    # check pattern for movw:
    # hw1 should be 0xf240..0xf64f accounting for i and imm4 (i.e. base 0xf240, fixed bits)
    # fixed bits: bits15..11=11110, bits9..4=100100
    if (hw1 & 0xF800) != 0xF000:
        raise ValueError("not a 32-bit thumb-2 instruction prefix")
    if (hw1 & 0xFBF0) != 0xF240:  # mask i (bit10) and imm4 (bits3..0)
        raise ValueError(f"not movw(t3): hw1=0x{hw1:04X}")

    if (hw2 & 0x8000) != 0x0000:  # bit15 should be 0
        raise ValueError(f"not movw(t3): hw2=0x{hw2:04X}")

    i = (hw1 >> 10) & 0x1
    imm4 = hw1 & 0xF
    imm3 = (hw2 >> 12) & 0x7
    rd = (hw2 >> 8) & 0xF
    imm8 = hw2 & 0xFF

    imm16 = (imm4 << 12) | (i << 11) | (imm3 << 8) | imm8
    return rd, imm16

def encode_movw_thumb2(rd: int, imm16: int) -> bytes:
    """encodes movw rd, #imm16 (thumb-2 t3)."""
    if not (0 <= rd <= 15):
        raise ValueError("rd must be 0..15")
    if not (0 <= imm16 <= 0xFFFF):
        raise ValueError("imm16 must be 0..0xffff")

    imm4 = (imm16 >> 12) & 0xF
    i = (imm16 >> 11) & 0x1
    imm3 = (imm16 >> 8) & 0x7
    imm8 = imm16 & 0xFF

    hw1 = 0xF240 | (i << 10) | imm4
    hw2 = (imm3 << 12) | (rd << 8) | imm8
    return struct.pack("<HH", hw1, hw2)

# ================= CORE LOGIC =================
def find_injection_offset(fw: bytearray) -> int:
    """
    find a suitable injection offset in the firmware.
    looks for at least 1mb of continuous empty space (0xff or 0x00).
    returns offset minus 16 bytes for safety margin.
    """
    empty_start = None
    empty_count = 0
    
    for i in range(len(fw)):
        byte = fw[i]
        
        # check if byte is empty (0xff or 0x00)
        if byte == 0xFF or byte == 0x00:
            if empty_start is None:
                empty_start = i
            empty_count += 1
            
            # check if we found enough empty space
            if empty_count >= MIN_EMPTY_SPACE:
                # subtract 24 bytes for safety margin
                injection_offset = empty_start + 24
                print(f"[INFO] found {empty_count} bytes of empty space at 0x{empty_start:X}")
                print(f"[INFO] using injection offset: 0x{injection_offset:X}")
                return injection_offset
        else:
            # reset counter if we hit non-empty byte
            empty_start = None
            empty_count = 0
    
    raise RuntimeError(f"could not find {MIN_EMPTY_SPACE} bytes of continuous empty space in firmware")

def sort_key(name: str) -> int:
    # ID_00.wav -> 0
    stem = Path(name).stem
    try:
        return int(stem.split("_")[1])
    except Exception:
        return 10**9

def patch_audio_prompts() -> int:
    """patch audio prompt files into the firmware."""
    if not os.path.exists(FIRMWARE_FILE):
        print(f"[ERROR] file {FIRMWARE_FILE} not found.")
        return 1
    if not os.path.isdir(SOUNDS_DIR):
        print(f"[ERROR] directory {SOUNDS_DIR} not found.")
        return 1

    with open(FIRMWARE_FILE, "rb") as f:
        fw = bytearray(f.read())

    print(f"loading {FIRMWARE_FILE} ({len(fw)} bytes)")
    
    # automatically find injection offset
    injection_offset = find_injection_offset(fw)
    
    cur = injection_offset
    print(f"injection offset: 0x{injection_offset:X} (va=0x{BASE_ADDRESS + injection_offset:X})")

    # process only files that exist in patch_map
    targets = [fn for fn in sorted(PATCH_MAP.keys(), key=sort_key)]
    for fn in targets:
        wav_path = os.path.join(SOUNDS_DIR, fn)
        if not os.path.exists(wav_path):
            print(f"[SKIP] {fn}: file not found in {SOUNDS_DIR}")
            continue

        print(f"\n=== {fn} ===")
        sbc = encode_wav_to_sbc(wav_path)
        sbc_size = len(sbc)

        if sbc_size > 0xFFFF:
            print(f"[ERROR] sbc size 0x{sbc_size:X} > 0xffff, movw cannot fit.")
            return 1

        # check space in firmware
        end = cur + sbc_size
        if end > len(fw):
            print(f"[ERROR] not enough space in firmware: need end=0x{end:X}, fw_len=0x{len(fw):X}")
            return 1

        # write audio data
        fw[cur:end] = sbc
        new_va = BASE_ADDRESS + cur
        print(f"write: file_off=0x{cur:X} .. 0x{end:X}  (size={sbc_size} / 0x{sbc_size:X})")
        print(f"new va: 0x{new_va:X}")

        # patch pointer (literal pool)
        ptr_pool_va = PATCH_MAP[fn]["ptr_pool_addr"]
        ptr_pool_off = va_to_file_off(ptr_pool_va)
        old_ptr = read_u32_le(fw, ptr_pool_off)
        write_u32_le(fw, ptr_pool_off, new_va)
        print(f"ptr patch: va 0x{ptr_pool_va:X} (file_off=0x{ptr_pool_off:X}) "
              f"{old_ptr:#010x} -> {new_va:#010x}")

        # patch movw size
        size_va = PATCH_MAP[fn]["size_instr_addr"]
        size_off = va_to_file_off(size_va)
        old_instr = bytes(fw[size_off:size_off + 4])

        try:
            rd, old_imm = decode_movw_thumb2(old_instr)
        except ValueError as e:
            print(f"[ERROR] at size_instr_addr 0x{size_va:X} not movw: {e}")
            print(f"bytes there: {old_instr.hex(' ')}")
            return 1

        new_instr = encode_movw_thumb2(rd, sbc_size)
        fw[size_off:size_off + 4] = new_instr
        print(f"size patch: va 0x{size_va:X} (file_off=0x{size_off:X}) "
              f"rd=r{rd} imm 0x{old_imm:X} -> 0x{sbc_size:X}")

        # align position for next file
        cur = end
        pad = (ALIGN - (cur % ALIGN)) % ALIGN
        if pad:
            if cur + pad > len(fw):
                print("[ERROR] not enough space for alignment padding.")
                return 1
            fw[cur:cur + pad] = b"\x00" * pad
            cur += pad

    # apply additional patches
    patch_prompt_sample_rate(fw)

    with open(OUTPUT_FILE, "wb") as f:
        f.write(fw)

    print(f"\n[SUCCESS] saved: {OUTPUT_FILE}")
    return 0

def patch_prompt_sample_rate(fw: bytearray) -> None:
    """set sample rate for audio prompts to 48000 hz."""
    def poke(va: int, old_hex: str, new_hex: str, name: str):
        off = va - BASE_ADDRESS
        old = bytes.fromhex(old_hex)
        cur = bytes(fw[off:off+len(old)])
        if cur != old:
            raise RuntimeError(f"{name}: unexpected bytes @0x{va:X}: {cur.hex(' ')} expected {old.hex(' ')}")
        fw[off:off+len(old)] = bytes.fromhex(new_hex)
        print(f"[PATCH] {name} @0x{va:X}: {old.hex(' ')} -> {new_hex}")

    # set sample rate to 48000 hz
    poke(0x3C0D21C4, "4F F4 7A 52", "4B F6 80 32", "prompt sample_rate=48000")

# ================= MENU SYSTEM =================
def print_banner():
    """print welcome banner."""
    print("=" * 60)
    print("  hello! c:")
    print("  open-qore patcher script")
    print("=" * 60)
    print()

def print_menu():
    """display main menu."""
    print("\nwhat do you want to do?:")
    print("  1. patch audio prompts")
    print("  2. exit")
    print()

def main() -> int:
    """main entry point with menu system."""
    print_banner()
    
    while True:
        print_menu()
        try:
            choice = input("select an option (1-2): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n[INFO] exiting...")
            return 0
        
        if choice == "1":
            print("\n" + "=" * 60)
            print("  audio prompt patcher")
            print("=" * 60 + "\n")
            result = patch_audio_prompts()
            if result != 0:
                print(f"\n[ERROR] operation failed with code {result}")
            input("\npress enter to continue...")
        elif choice == "2":
            print("\n[INFO] exiting...")
            return 0
        else:
            print("[ERROR] invalid option. please select 1 or 2.")

if __name__ == "__main__":
    raise SystemExit(main())
