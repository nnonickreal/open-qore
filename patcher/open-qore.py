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
min_empty_space = 1024 * 256

ffmpeg_bin = "ffmpeg"

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
        "-b:a", "328k",
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

# ================= dynamic pattern finders =================
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
                # check that the pointer points to rom (skip ram and the first 64kb of firmware)
                if base_address + 0x10000 <= val < base_address + len(fw):
                    if found_ptr is None:
                        found_ptr = lit_va
        
        # 2. size setup: movw (thumb-2)
        if (instr16 & 0xfbf0) == 0xf240:
            b = fw[pc:pc+4]
            try:
                rd, imm = decode_movw_thumb2(b)
                if imm > 0x100: # sound size is definitely > 256 bytes
                    if found_size is None:
                        found_size = base_address + pc
            except ValueError:
                pass
                
        # 3. size setup: mov.w (thumb-2) - used by compiler for small sizes
        elif (instr16 & 0xfbef) == 0xf04f:
            if found_size is None:
                found_size = base_address + pc
        
        # step pc (32-bit or 16-bit)
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
        # search for switch pattern: tbh [pc, rm] or tbb [pc, rm]
        if fw[i] == 0xdf and fw[i+1] == 0xe8:
            op2 = fw[i+2]
            if (op2 & 0xf0) in (0x00, 0x10) and fw[i+3] == 0xf0:
                is_tbh = (op2 & 0xf0) == 0x10
                rm = op2 & 0x0f
                
                # search for cmp rm, #max_case instruction a bit above
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
                    
                # filter out too small and too large switches
                if max_case < 10 or max_case > 250:
                    continue
                    
                # the offset table starts exactly 4 bytes after the tbh/tbb instruction
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
                        
                # evaluate how many unique sounds we found
                unique_ptrs = len(set(x["ptr_pool_addr"] for x in current_map.values()))
                
                if unique_ptrs > 0:
                    print(f"[debug] found switch at 0x{i:x} with {unique_ptrs} valid audio cases.")
                
                if unique_ptrs > best_unique_ptrs:
                    best_unique_ptrs = unique_ptrs
                    best_map = current_map
                    best_tbh_offset = i
                    
    return best_map, best_tbh_offset

def patch_prompt_sample_rate_dynamic(fw: bytearray, tbh_offset: int) -> int:
    target = b"\x4f\xf4\x7a\x52" # mov.w r2, #16000
    new_bytes = b"\x4b\xf6\x80\x32" # mov.w r2, #48000
    
    matches = []
    start = 0
    while True:
        idx = fw.find(target, start)
        if idx == -1:
            break
        matches.append(idx)
        start = idx + 1
        
    if not matches:
        print("[warning] could not find sample rate pattern.")
        return -1
        
    if len(matches) == 1:
        match_off = matches[0]
        print(f"[patch] sample rate patched at offset 0x{match_off:x}.")
        fw[match_off:match_off+4] = new_bytes
        return match_off
    else:
        # take the one closest to the sounds table
        closest_off = min(matches, key=lambda x: abs(x - tbh_offset))
        print(f"[patch] sample rate patched at offset 0x{closest_off:x}.")
        fw[closest_off:closest_off+4] = new_bytes
        return closest_off

def apply_stereo_patches(fw: bytearray, sr_offset: int) -> None:
    print("\n=== stereo patches ===")
    
    # 1. patch len / 2 division in decoder (lsr.w r9, r4, #1 -> mov.w r9, r4)
    sig1 = b'\x4f\xea\x54\x09\x49\x46\x23\xb3'
    idx1 = fw.find(sig1)
    if idx1 != -1:
        fw[idx1:idx1+4] = b'\x4f\xea\x04\x09'
        print(f"[patch] 1. len / 2 division disabled (0x{idx1:x})")
    else:
        print("[warning] signature 1 not found!")

    # 2. patch multiplication by 2 for memset offset
    sig2 = b'\x38\x68\xa9\xeb\x05\x02\x00\xeb\x45\x00\x00\x21'
    idx2 = fw.find(sig2)
    if idx2 != -1:
        fw[idx2+6:idx2+10] = b'\x00\xeb\x05\x00'
        print(f"[patch] 2. buffer clear offset fixed (0x{idx2:x})")
    else:
        print("[warning] signature 2 not found!")

    # 3. patch len / 4 division for channel mixer
    sig3 = b'\x39\x68\xa2\x08\x30\x46'
    idx3 = fw.find(sig3)
    if idx3 != -1:
        fw[idx3+2:idx3+4] = b'\x22\x46'
        print(f"[patch] 3. length division for channel mixer disabled (0x{idx3:x})")
        
        # 4. mixer function -> turn into fast stereo-copying (memcpy)
        bl_offset = idx3 + 6
        bl_bytes = fw[bl_offset:bl_offset+4]
        
        # dynamically calculate function address from thumb-2 branch instruction (bl)
        hw1 = (bl_bytes[1] << 8) | bl_bytes[0]
        hw2 = (bl_bytes[3] << 8) | bl_bytes[2]
        s = (hw1 >> 10) & 1
        j1 = (hw2 >> 13) & 1
        j2 = (hw2 >> 11) & 1
        i1 = ~(j1 ^ s) & 1
        i2 = ~(j2 ^ s) & 1
        imm10 = hw1 & 0x3ff
        imm11 = hw2 & 0x7ff
        imm32 = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
        if s: imm32 -= (1 << 25)
        
        mixer_offset = bl_offset + 4 + imm32
        
        # assembly micro-loop of ldrb/strb copying 
        memcpy_payload = b'\x00\x2a\x03\xd0\x01\x3a\x8b\x5c\x83\x54\xf9\xe7\x70\x47'
        fw[mixer_offset:mixer_offset+14] = memcpy_payload
        print(f"[patch] 4. mixer function replaced with direct stereo copy algorithm (0x{mixer_offset:x})")
    else:
        print("[warning] signature 3 not found!")

    # 5. increase cache size
    # look for mov.w r?, #0x400 right before sample rate instruction (in a 64-byte window)
    if sr_offset != -1:
        start_idx = max(0, sr_offset - 64)
        window = fw[start_idx:sr_offset]
        
        match = re.search(b'\x4f\xf4\x80[\x60-\x6f]', window)
        if match:
            mov_offset = start_idx + match.start()
            reg_byte = fw[mov_offset + 3]
            # replace with mov.w r?, #0x800
            fw[mov_offset:mov_offset+4] = bytes([0x4f, 0xf4, 0x00, reg_byte])
            print(f"[patch] 5. cache buffer size successfully expanded to 2048 bytes (0x{mov_offset:x})")
        else:
            print("[warning] could not find cache size instruction near sample rate.")
    else:
        print("[warning] skipping cache patch, as sample rate was not found.")

# ================= core logic =================
def find_injection_offset(fw: bytearray) -> int:
    empty_start = None
    empty_count = 0
    for i in range(len(fw)):
        byte = fw[i]
        if byte == 0xff or byte == 0x00:
            if empty_start is None:
                empty_start = i
            empty_count += 1
            if empty_count >= min_empty_space:
                return empty_start + 24
        else:
            empty_start = None
            empty_count = 0
    raise RuntimeError("could not find enough empty space in firmware")

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
    
    # search for sounds table
    patch_map, tbh_offset = generate_patch_map(fw, base_address)
    if not patch_map:
        print("[error] could not dynamically find prompt blocks in the firmware.")
        return 1
    
    print(f"[info] found sound manager switch at offset 0x{tbh_offset:x}.")
    print(f"[info] dynamically generated patch_map with {len(patch_map)} possible targets.")

    injection_offset = find_injection_offset(fw)
    cur = injection_offset
    print(f"injection offset: 0x{injection_offset:x} (va=0x{base_address + injection_offset:x})")

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
        if end > len(fw):
            print(f"[error] not enough space in firmware")
            return 1

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
            # if it's not movw, then it's mov.w. extract the destination register.
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
        print("\n[warning] no files were patched! check that wav files are in the sounds_src folder.")
    else:
        print(f"\n[info] successfully patched {patched_count} files.")

    print("\n=== sample rate ===")
    sr_offset = patch_prompt_sample_rate_dynamic(fw, tbh_offset)
    
    apply_stereo_patches(fw, sr_offset)

    with open(output_file, "wb") as f:
        f.write(fw)

    print(f"\n[success] saved: {output_file}")
    return 0

# ================= menu system =================
def print_banner():
    print("=" * 60)
    print("  hello! c:")
    print("  qorepatcher script")
    print("=" * 60)
    print()

def print_menu():
    print("\nwhat do you want to do?:")
    print("  1. patch audio prompts")
    print("  2. exit")
    print()

def get_base_address() -> int:
    print("\nwhich firmware do you have?")
    print("  1. without ota boot (+18000 to offset, base 0x3c018000)")
    print("  2. with ota boot (base 0x3c000000)")
    while True:
        try:
            choice = input("choose (1-2): ").strip()
            if choice == "1":
                return 0x3c018000
            elif choice == "2":
                return 0x3c000000
            else:
                print("[error] invalid choice. choose 1 or 2.")
        except (EOFError, KeyboardInterrupt):
            print("\n\n[info] exiting...")
            sys.exit(0)

def main() -> int:
    print_banner()
    while True:
        print_menu()
        try:
            choice = input("select an option (1-2): ").strip()
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
            print("\n[info] exiting...")
            return 0
        else:
            print("[error] invalid option. please select 1 or 2.")

if __name__ == "__main__":
    raise SystemExit(main())