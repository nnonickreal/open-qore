#!/usr/bin/env python3
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

# ================= configuration =================
firmware_file = "firmware.bin"
output_file = "firmware_patched.bin"
sounds_dir = "sounds_src"

align = 4
inject_back_offset = 0

ffmpeg_bin = "ffmpeg"

# search window for "neighboring" functions around the found sounds_manager switch
STEREO_SEARCH_BACK = 256
STEREO_SEARCH_FWD = 16384

# ================= utilities =================
def va_to_file_off(va: int, base_address: int) -> int:
    off = va - base_address
    if off < 0:
        raise ValueError(f"va 0x{va:x} < base_address 0x{base_address:x}")
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
    cmd = [
        ffmpeg_bin, "-y", "-v", "error",
        "-i", input_path,
        "-ar", "48000",
        "-ac", "2",
        "-b:a", "258k",
        "-f", "sbc",
        "-"
    ]
    data = run_cmd(cmd)
    if not data:
        raise RuntimeError("ffmpeg returned empty data")
    return data

# ================= thumb-2 movw (t3) =================
def decode_movw_thumb2(instr4: bytes) -> tuple[int, int]:
    if len(instr4) != 4:
        raise ValueError("instr4 must be exactly 4 bytes")
    hw1, hw2 = struct.unpack("<HH", instr4)

    if (hw1 & 0xf800) != 0xf000:
        raise ValueError("not a 32-bit thumb-2 instruction prefix")
    if (hw1 & 0xfbf0) != 0xf240:
        raise ValueError(f"not movw(t3): hw1=0x{hw1:04x}")

    i = (hw1 >> 10) & 0x1
    imm4 = hw1 & 0xf
    imm3 = (hw2 >> 12) & 0x7
    rd = (hw2 >> 8) & 0xf
    imm8 = hw2 & 0xff

    imm16 = (imm4 << 12) | (i << 11) | (imm3 << 8) | imm8
    return rd, imm16

def encode_movw_thumb2(rd: int, imm16: int) -> bytes:
    if not (0 <= rd <= 15):
        raise ValueError("rd must be 0..15")
    if not (0 <= imm16 <= 0xffff):
        raise ValueError("imm16 must be 0..0xffff")

    imm4 = (imm16 >> 12) & 0xf
    i = (imm16 >> 11) & 0x1
    imm3 = (imm16 >> 8) & 0x7
    imm8 = imm16 & 0xff

    hw1 = 0xf240 | (i << 10) | imm4
    hw2 = (imm3 << 12) | (rd << 8) | imm8
    return struct.pack("<HH", hw1, hw2)

# ================= generic masked-pattern matching (register-independent search) =================
def match_masked(fw: bytearray, off: int, pattern: bytes, mask: bytes) -> bool:
    if off < 0 or off + len(pattern) > len(fw):
        return False
    for i in range(len(pattern)):
        if (fw[off + i] & mask[i]) != pattern[i]:
            return False
    return True

def find_masked_all(fw: bytearray, pattern: bytes, mask: bytes, start: int, end: int, step: int = 2):
    start = max(0, start)
    end = min(end, len(fw) - len(pattern))
    res = []
    off = start
    while off < end:
        if match_masked(fw, off, pattern, mask):
            res.append(off)
        off += step
    return res

# ---- lsr.w rd, rm, #1  (thumb-2 shifted-register mov, type=lsr, imm5=1) ----
# bytes: 4f/5f ea | (0101 rm) (0000 rd)
LSR1_PATTERN = bytes([0x4F, 0xEA, 0x50, 0x00])
LSR1_MASK    = bytes([0xEF, 0xFF, 0xF0, 0xF0])

def decode_lsr1(fw: bytearray, off: int):
    if not match_masked(fw, off, LSR1_PATTERN, LSR1_MASK):
        return None
    rm = fw[off + 2] & 0x0F
    rd = fw[off + 3] & 0x0F
    return rd, rm

def encode_mov_w_noshift(rd: int, rm: int) -> bytes:
    # mov.w rd, rm  (same encoding as lsr.w, but imm5=0 -> no shift)
    return bytes([0x4F, 0xEA, rm & 0x0F, rd & 0x0F])

# ---- sub.w rd, rn, rm  (register form, no shift) ----
SUBW_PATTERN = bytes([0xA0, 0xEB, 0x00, 0x00])
SUBW_MASK    = bytes([0xE0, 0xFF, 0xF0, 0xF0])

def decode_subw(fw: bytearray, off: int):
    if not match_masked(fw, off, SUBW_PATTERN, SUBW_MASK):
        return None
    rn = fw[off] & 0x0F
    rm = fw[off + 2] & 0x0F
    rd = fw[off + 3] & 0x0F
    return rd, rn, rm

# ---- add.w rd, rn, rm, lsl #1 ----
ADDW_LSL1_PATTERN = bytes([0x00, 0xEB, 0x40, 0x00])
ADDW_LSL1_MASK    = bytes([0xE0, 0xFF, 0xF0, 0xF0])

def decode_addw_lsl1(fw: bytearray, off: int):
    if not match_masked(fw, off, ADDW_LSL1_PATTERN, ADDW_LSL1_MASK):
        return None
    rn = fw[off] & 0x0F
    rm = fw[off + 2] & 0x0F
    rd = fw[off + 3] & 0x0F
    return rd, rn, rm

def strip_addw_shift(fw: bytearray, off: int) -> None:
    # zero out imm2:type (high nibble of byte +2) -> shift removed, register preserved
    fw[off + 2] = fw[off + 2] & 0x0F

# ---- lsrs rd, rm, #2  (thumb-1, r0..r7 only) ----
LSRS2_PATTERN = bytes([0x80, 0x08])
LSRS2_MASK    = bytes([0xC0, 0xFF])

def decode_lsrs2(fw: bytearray, off: int):
    if not match_masked(fw, off, LSRS2_PATTERN, LSRS2_MASK):
        return None
    b0 = fw[off]
    rm = (b0 >> 3) & 0x7
    rd = b0 & 0x7
    return rd, rm

def encode_mov_reg(rd: int, rm: int) -> bytes:
    # mov rd, rm (thumb-1 "special data", works for any registers 0..15)
    byte0 = ((rd >> 3 & 1) << 7) | ((rm & 0xF) << 3) | (rd & 0x7)
    return bytes([byte0, 0x46])

# ---- bl (thumb-2) ----
def is_bl_opcode(fw: bytearray, off: int) -> bool:
    if off + 4 > len(fw):
        return False
    hw1 = struct.unpack_from("<H", fw, off)[0]
    hw2 = struct.unpack_from("<H", fw, off + 2)[0]
    return (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000

def decode_bl_target_offset(fw: bytearray, off: int):
    if not is_bl_opcode(fw, off):
        return None
    hw1 = struct.unpack_from("<H", fw, off)[0]
    hw2 = struct.unpack_from("<H", fw, off + 2)[0]
    s = (hw1 >> 10) & 1
    j1 = (hw2 >> 13) & 1
    j2 = (hw2 >> 11) & 1
    i1 = (~(j1 ^ s)) & 1
    i2 = (~(j2 ^ s)) & 1
    imm10 = hw1 & 0x3FF
    imm11 = hw2 & 0x7FF
    imm32 = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
    if s:
        imm32 -= (1 << 25)
    return off + 4 + imm32

def find_bl_after(fw: bytearray, start: int, max_search: int = 40):
    end = min(start + max_search, len(fw) - 4)
    off = start
    while off < end:
        if is_bl_opcode(fw, off):
            return off
        off += 2
    return None

# ================= dynamic pattern finders (sounds prompts) =================
def analyze_prompt_block(fw: bytearray, start_off: int, base_address: int):
    pc = start_off
    found_ptr = None
    found_size = None

    # scan up to 30 instructions inside the handler
    for _ in range(30):
        if pc >= len(fw) - 4:
            break

        instr16 = struct.unpack_from("<H", fw, pc)[0]

        # 1. load from literal pool: ldr rx, [pc, #imm]
        if (instr16 & 0xf800) == 0x4800:
            imm8 = instr16 & 0xff
            va_instr = base_address + pc
            pc_align = (va_instr + 4) & ~3
            lit_va = pc_align + (imm8 << 2)
            lit_off = lit_va - base_address

            if 0 <= lit_off <= len(fw) - 4:
                val = read_u32_le(fw, lit_off)
                # check that the pointer points into rom (skip ram and the first 64kb of firmware)
                if base_address + 0x10000 <= val < base_address + len(fw):
                    if found_ptr is None:
                        found_ptr = lit_va

        # 2. size assignment: movw (thumb-2)
        if (instr16 & 0xfbf0) == 0xf240:
            b = fw[pc:pc+4]
            try:
                rd, imm = decode_movw_thumb2(b)
                if imm > 0x100:  # sound size is definitely greater than 256 bytes
                    if found_size is None:
                        found_size = base_address + pc
            except ValueError:
                pass

        # 3. size assignment: mov.w (thumb-2) - used by the compiler for small sizes
        elif (instr16 & 0xfbef) == 0xf04f:
            if found_size is None:
                found_size = base_address + pc

        # advance pc (32-bit or 16-bit)
        if (instr16 & 0xe000) == 0xe000 and (instr16 & 0x1800) != 0x0000:
            pc += 4
        else:
            pc += 2

        if found_ptr is not None and found_size is not None:
            return found_ptr, found_size

    return found_ptr, found_size

def generate_patch_map(fw: bytearray, base_address: int):
    best_map = {}
    best_unique_ptrs = 0
    best_tbh_offset = 0

    for i in range(0, len(fw) - 4, 2):
        # look for switch pattern: tbh [pc, rm] or tbb [pc, rm]
        if fw[i] == 0xdf and fw[i+1] == 0xe8:
            op2 = fw[i+2]
            if (op2 & 0xf0) in (0x00, 0x10) and fw[i+3] == 0xf0:
                is_tbh = (op2 & 0xf0) == 0x10
                rm = op2 & 0x0f

                # look for cmp rm, #max_case instruction slightly above
                max_case = 0
                pc = i - 2
                while pc >= max(0, i - 60):
                    # cmp rm, #imm in thumb-1: 0x2800..0x2f00
                    if 0x28 <= fw[pc+1] <= 0x2f:
                        cmp_rm = fw[pc+1] - 0x28
                        if cmp_rm == rm:
                            max_case = fw[pc]
                            break
                    pc -= 2

                # discard switches that are too small or too large
                if max_case < 10 or max_case > 250:
                    continue

                # offset table starts exactly 4 bytes after the tbh/tbb instruction
                table_start = i + 4
                tbh_pc = i + 4
                num_cases = max_case + 1

                if table_start + (num_cases * (2 if is_tbh else 1)) > len(fw):
                    continue

                current_map = {}
                for case_idx in range(num_cases):
                    if is_tbh:
                        offset = struct.unpack_from("<H", fw, table_start + case_idx * 2)[0] * 2
                    else:
                        offset = fw[table_start + case_idx] * 2

                    target_file_off = tbh_pc + offset
                    if target_file_off < 0 or target_file_off >= len(fw):
                        continue

                    ptr, size = analyze_prompt_block(fw, target_file_off, base_address)
                    if ptr is not None and size is not None:
                        fn = f"ID_{case_idx:02d}.wav"
                        current_map[fn] = {
                            "ptr_pool_addr": ptr,
                            "size_instr_addr": size
                        }

                # estimate how many unique sounds we found
                unique_ptrs = len(set(x["ptr_pool_addr"] for x in current_map.values()))

                if unique_ptrs > 0:
                    print(f"[debug] found switch at 0x{i:x} with {unique_ptrs} valid audio cases.")

                if unique_ptrs > best_unique_ptrs:
                    best_unique_ptrs = unique_ptrs
                    best_map = current_map
                    best_tbh_offset = i

    return best_map, best_tbh_offset

# ================= sample rate (16000 -> 48000), register-independent =================
SR16000_PATTERN = bytes([0x4F, 0xF4, 0x7A, 0x50])
SR16000_MASK    = bytes([0xFF, 0xFF, 0xFF, 0xF0])

def patch_prompt_sample_rate_dynamic(fw: bytearray, tbh_offset: int) -> int:
    matches = find_masked_all(fw, SR16000_PATTERN, SR16000_MASK, 0, len(fw))

    if not matches:
        print("[warning] could not find sample rate pattern.")
        return -1

    if len(matches) == 1:
        match_off = matches[0]
    else:
        # take the one closest to the sound table
        match_off = min(matches, key=lambda x: abs(x - tbh_offset))
        print(f"[debug] {len(matches)} sample-rate candidates found, picked closest to switch (0x{match_off:x}).")

    rd = fw[match_off + 3] & 0x0F
    new_bytes = bytes([0x4B, 0xF6, 0x80, 0x30 | rd])  # mov.w rx, #48000
    fw[match_off:match_off+4] = new_bytes
    print(f"[patch] sample rate patched at offset 0x{match_off:x} (r{rd}: 16000 -> 48000).")
    return match_off

# ================= stereo patches (structural, register-independent search) =================
def find_stereo_chain(fw: bytearray, tbh_offset: int):
    """
    searches "structurally" for the sequence:
      lsr.w half, len, #1           (len/2)
      ...
      sub.w  remain, half, cnt      (remain = half - cnt)
      add.w  ptr, ptr, cnt, lsl#1   (offset*2)
      ...
      lsrs   mlen, len, #2          (len/4, mixer argument)
      bl     mixer_func
    all registers are determined dynamically; anchoring is based on location - a
    window around the already-found sounds_manager switch (functions are located nearby).
    """
    search_start = max(0, tbh_offset - STEREO_SEARCH_BACK)
    search_end = min(len(fw), tbh_offset + STEREO_SEARCH_FWD)

    candidates = find_masked_all(fw, LSR1_PATTERN, LSR1_MASK, search_start, search_end)
    if not candidates:
        # fallback: search the whole firmware (less reliable, but better than nothing)
        candidates = find_masked_all(fw, LSR1_PATTERN, LSR1_MASK, 0, len(fw))

    for pos1 in candidates:
        decoded = decode_lsr1(fw, pos1)
        if decoded is None:
            continue
        half_reg, len_reg = decoded

        # look for all sub.w with rn == half_reg in a reasonable window after pos1
        sub_hits = []
        end2 = min(pos1 + 2048, len(fw) - 4)
        off = pos1
        while off < end2:
            d = decode_subw(fw, off)
            if d is not None and d[1] == half_reg:
                sub_hits.append((off, d[0], d[2]))  # (pos, sub_rd, cnt_reg)
            off += 2

        add_hit = None
        used_sub = None
        for pos2, sub_rd, cnt_reg in sub_hits:
            end3 = min(pos2 + 64, len(fw) - 4)
            off = pos2
            while off < end3:
                d = decode_addw_lsl1(fw, off)
                if d is not None and d[2] == cnt_reg:
                    add_hit = off
                    used_sub = (pos2, sub_rd, cnt_reg)
                    break
                off += 2
            if add_hit is not None:
                break

        if add_hit is None:
            continue

        # look for lsrs mlen, len_reg, #2 (mixer argument) in the window after pos1
        lsrs_hit = None
        end4 = min(pos1 + 2048, len(fw) - 2)
        off = pos1
        while off < end4:
            d = decode_lsrs2(fw, off)
            if d is not None and d[1] == len_reg:
                lsrs_hit = (off, d[0])
                break
            off += 2

        if lsrs_hit is None:
            continue

        pos4, mixer_len_reg = lsrs_hit
        bl_off = find_bl_after(fw, pos4 + 2, max_search=24)
        if bl_off is None:
            continue

        mixer_target = decode_bl_target_offset(fw, bl_off)
        if mixer_target is None or mixer_target < 0 or mixer_target + 14 > len(fw):
            continue

        return {
            "pos1": pos1, "half_reg": half_reg, "len_reg": len_reg,
            "pos_add": add_hit,
            "pos_lsrs": pos4,
            "mixer_target": mixer_target,
        }

    return None

def apply_stereo_patches(fw: bytearray, tbh_offset: int, sr_offset: int) -> None:
    print("\n=== stereo patches (dynamic structure search) ===")

    chain = find_stereo_chain(fw, tbh_offset)
    if chain is None:
        print("[warning] could not dynamically find the mono/stereo mixer structure.")
    else:
        # 1. len/2 -> remove division
        pos1 = chain["pos1"]
        fw[pos1:pos1+4] = encode_mov_w_noshift(chain["half_reg"], chain["len_reg"])
        print(f"[patch] 1. len/2 division disabled (0x{pos1:x}) "
              f"r{chain['half_reg']} = r{chain['len_reg']}")

        # 2. offset*2 -> remove shift
        pos_add = chain["pos_add"]
        strip_addw_shift(fw, pos_add)
        print(f"[patch] 2. offset*2 multiplication disabled (0x{pos_add:x})")

        # 3. len/4 (mixer argument) -> just copy the register
        pos4 = chain["pos_lsrs"]
        d = decode_lsrs2(fw, pos4)
        if d is not None:
            rd, rm = d
            fw[pos4:pos4+2] = encode_mov_reg(rd, rm)
            print(f"[patch] 3. length division for mixer disabled (0x{pos4:x}) "
                  f"mov r{rd}, r{rm}")

        # 4. mixer function -> replace with a fast memcpy (abi: r0=dst, r1=src, r2=len)
        mixer_off = chain["mixer_target"]
        memcpy_payload = b'\x00\x2a\x03\xd0\x01\x3a\x8b\x5c\x83\x54\xf9\xe7\x70\x47'
        fw[mixer_off:mixer_off+len(memcpy_payload)] = memcpy_payload
        print(f"[patch] 4. mixer function replaced with direct stereo copy (0x{mixer_off:x})")

    # 5. cache size: look for mov.w r?, #0x400 in a window around sample rate (before and after)
    if sr_offset != -1:
        window_lo = max(0, sr_offset - 256)
        window_hi = min(len(fw), sr_offset + 256)
        found_cache = None
        off = window_lo
        while off < window_hi - 4:
            if (fw[off] == 0x4F and fw[off+1] == 0xF4 and
                    fw[off+2] == 0x80 and (fw[off+3] & 0xF0) == 0x60):
                found_cache = off
                break
            off += 2

        if found_cache is not None:
            reg_byte = fw[found_cache + 3]
            fw[found_cache:found_cache+4] = bytes([0x4F, 0xF4, 0x00, reg_byte])
            print(f"[patch] 5. cache buffer size expanded to 2048 bytes (0x{found_cache:x})")
        else:
            print("[warning] could not find the cache size instruction near the sample rate.")
    else:
        print("[warning] skipping cache patch because sample rate was not found.")

# ================= core logic =================
def find_injection_offset(fw: bytearray) -> int:
    # append new audio data starting `inject_back_offset` bytes before the end
    # of the firmware image, instead of requiring pre-existing empty space
    offset = len(fw) - inject_back_offset
    return offset if offset > 0 else 0

def sort_key(name: str) -> int:
    stem = Path(name).stem
    try:
        return int(stem.split("_")[1])
    except Exception:
        return 10**9

def patch_audio_prompts(base_address: int) -> int:
    if not os.path.exists(firmware_file):
        print(f"[error] file {firmware_file} not found.")
        return 1
    if not os.path.isdir(sounds_dir):
        print(f"[error] directory {sounds_dir} not found.")
        return 1

    with open(firmware_file, "rb") as f:
        fw = bytearray(f.read())

    print(f"loading {firmware_file} ({len(fw)} bytes)")

    # look for the sound table
    patch_map, tbh_offset = generate_patch_map(fw, base_address)
    if not patch_map:
        print("[error] could not dynamically find prompt blocks in the firmware.")
        return 1

    print(f"[info] found sound manager switch at offset 0x{tbh_offset:x}.")
    print(f"[info] dynamically generated patch_map with {len(patch_map)} possible targets.")

    injection_offset = find_injection_offset(fw)
    cur = injection_offset
    print(f"append offset: 0x{injection_offset:x} (va=0x{base_address + injection_offset:x}, "
          f"{inject_back_offset} bytes before the end of the original firmware)")

    targets = [fn for fn in sorted(patch_map.keys(), key=sort_key)]
    patched_count = 0
    seen_ptrs = set()

    for fn in targets:
        wav_path = os.path.join(sounds_dir, fn)
        if not os.path.exists(wav_path):
            continue

        ptr_pool_va = patch_map[fn]["ptr_pool_addr"]

        # some sounds share the same handler. don't patch it twice.
        if ptr_pool_va in seen_ptrs:
            print(f"\n[skip] {fn.lower()}: shares the same handler as a previous file.")
            continue
        seen_ptrs.add(ptr_pool_va)

        print(f"\n=== {fn.lower()} ===")
        sbc = encode_wav_to_sbc(wav_path)
        sbc_size = len(sbc)

        if sbc_size > 0xffff:
            print(f"[error] sbc size 0x{sbc_size:x} > 0xffff, movw cannot fit.")
            return 1

        end = cur + sbc_size
        # slice assignment automatically grows the bytearray past its current length
        fw[cur:end] = sbc
        new_va = base_address + cur
        print(f"write: file_off=0x{cur:x} .. 0x{end:x}  (size={sbc_size} / 0x{sbc_size:x})")

        ptr_pool_off = va_to_file_off(ptr_pool_va, base_address)
        old_ptr = read_u32_le(fw, ptr_pool_off)
        write_u32_le(fw, ptr_pool_off, new_va)
        print(f"ptr patch: va 0x{ptr_pool_va:x} (file_off=0x{ptr_pool_off:x}) {old_ptr:#010x} -> {new_va:#010x}")

        size_va = patch_map[fn]["size_instr_addr"]
        size_off = va_to_file_off(size_va, base_address)
        old_instr = bytes(fw[size_off:size_off + 4])

        try:
            rd, old_imm = decode_movw_thumb2(old_instr)
            old_imm_str = f"0x{old_imm:x}"
        except ValueError as e:
            # if this is not movw, it must be mov.w. extract the destination register.
            hw1, hw2 = struct.unpack("<HH", old_instr)
            if (hw1 & 0xfbef) == 0xf04f:
                rd = (hw2 >> 8) & 0xf
                old_imm_str = "unknown (mov.w)"
            else:
                print(f"[error] at size_instr_addr 0x{size_va:x} not movw or mov.w: {e}")
                return 1

        new_instr = encode_movw_thumb2(rd, sbc_size)
        fw[size_off:size_off + 4] = new_instr
        print(f"size patch: va 0x{size_va:x} (file_off=0x{size_off:x}) rd=r{rd} imm {old_imm_str} -> 0x{sbc_size:x}")

        cur = end
        pad = (align - (cur % align)) % align
        if pad:
            fw[cur:cur + pad] = b"\x00" * pad
            cur += pad

        patched_count += 1

    if patched_count == 0:
        print("\n[warning] no files were patched! check that wav files are located in the sounds_src folder.")
    else:
        print(f"\n[info] successfully patched {patched_count} files.")
        print(f"[info] firmware grew to {len(fw)} bytes.")

    print("\n=== sample rate ===")
    sr_offset = patch_prompt_sample_rate_dynamic(fw, tbh_offset)

    apply_stereo_patches(fw, tbh_offset, sr_offset)

    with open(output_file, "wb") as f:
        f.write(fw)

    print(f"\n[success] saved: {output_file}")
    return 0

# ================= bluetooth flashing helper =================
def choose_firmware_file() -> str | None:
    bin_files = sorted(Path(".").glob("*.bin"))
    if bin_files:
        print("\nfound firmware files in the current directory:")
        for i, f in enumerate(bin_files, 1):
            print(f"  {i}. {f.name}")
        custom_idx = len(bin_files) + 1
        print(f"  {custom_idx}. enter a custom path")
        while True:
            choice = input(f"select a file (1-{custom_idx}): ").strip()
            try:
                idx = int(choice)
            except ValueError:
                print("[error] invalid choice.")
                continue
            if 1 <= idx <= len(bin_files):
                return str(bin_files[idx - 1])
            elif idx == custom_idx:
                break
            else:
                print("[error] invalid choice.")

    path = input("enter path to the firmware file to flash: ").strip()
    return path if path else None

def flash_via_bluetooth() -> None:
    script_path = Path(__file__).resolve().parent / "besota.py"
    if not script_path.exists():
        print(f"[error] besota.py not found next to this script ({script_path}).")
        return

    fw_path = choose_firmware_file()
    if not fw_path or not os.path.exists(fw_path):
        print("[error] file not found.")
        return

    # launch as a separate process with the same interpreter, avoiding any
    # import-path/module issues in this script's own process
    cmd = [sys.executable, str(script_path), fw_path]
    print(f"\n[info] launching: {' '.join(cmd)}\n")

    try:
        subprocess.run(cmd)
    except Exception as e:
        print(f"[error] failed to launch besota.py: {e}")

# ================= menu system =================
def print_banner():
    print("=" * 60)
    print("  hello! c:")
    print("  qorepatcher by nnonick (beta-build-1)")
    print("=" * 60)
    print()

def print_menu():
    print("\nwhat do you want to do?:")
    print("  1. patch audio prompts")
    print("  2. flash firmware via bluetooth")
    print("  3. exit")
    print()

def get_base_address() -> int:
    print("\nwhat firmware do you have?")
    print("  1. without ota boot (+0x18000 offset, base 0x3c018000)")
    print("  2. with ota boot (base 0x3c000000)")
    while True:
        try:
            choice = input("select (1-2): ").strip()
            if choice == "1":
                return 0x3c018000
            elif choice == "2":
                return 0x3c000000
            else:
                print("[error] invalid choice. select 1 or 2.")
        except (EOFError, KeyboardInterrupt):
            print("\n\n[info] exiting...")
            sys.exit(0)

def main() -> int:
    print_banner()
    while True:
        print_menu()
        try:
            choice = input("select an option (1-3): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n[info] exiting...")
            return 0

        if choice == "1":
            base_address = get_base_address()
            print("\n" + "=" * 60)
            print("  audio prompt patcher")
            print("=" * 60 + "\n")
            result = patch_audio_prompts(base_address)
            if result != 0:
                print(f"\n[error] operation failed with code {result}")
            input("\npress enter to continue...")
        elif choice == "2":
            print("\n" + "=" * 60)
            print("  bluetooth firmware flasher")
            print("=" * 60 + "\n")
            flash_via_bluetooth()
            input("\npress enter to continue...")
        elif choice == "3":
            print("\n[info] exiting...")
            return 0
        else:
            print("[error] invalid option. please select 1, 2 or 3.")

if __name__ == "__main__":
    raise SystemExit(main())