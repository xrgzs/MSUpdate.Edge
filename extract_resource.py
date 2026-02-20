#!/usr/bin/env python3
"""
Extract files from Google Omaha Setup resource 102 (type "B").

Pipeline: Resource 102 -> LZMA decompress -> BCJ2 decode -> tar extract

Based on Google Omaha source code:
  - omaha/mi_exe_stub/mi.cc (DecompressBufferToFile)
  - third_party/lzma/files/C/Bcj2.c (Bcj2_Decode)
  - omaha/mi_exe_stub/tar.cc (Tar::ExtractOneFile)

Header format (20 bytes, little-endian uint32 x5):
  original_size  - final tar output size
  stream0_size   - main bytecode stream
  stream1_size   - CALL address stream
  stream2_size   - JMP/JCC address stream
  stream3_size   - range coder bitstream
"""

import struct
import sys
import os
import lzma


def bcj2_decode(buf0: bytes, size0: int,
                buf1: bytes, size1: int,
                buf2: bytes, size2: int,
                buf3: bytes, size3: int,
                out_size: int) -> bytes:
    """
    Exact Python port of Bcj2_Decode from LZMA SDK / Google Omaha.
    See: google/omaha third_party/lzma/files/C/Bcj2.c
    """
    # CProb p[256 + 2], initialized to kBitModelTotal >> 1 = 1024
    p = [1024] * 258

    in_pos = 0
    out_pos = 0
    out_buf = bytearray(out_size)

    prev_byte = 0

    # Range coder init: buffer = buf3
    buf3_pos = 0
    code = 0
    rc_range = 0xFFFFFFFF

    # RC_INIT2: read 5 bytes
    for _ in range(5):
        if buf3_pos >= size3:
            print("[!] RC init: ran out of range coder data")
            return bytes(out_buf[:out_pos])
        code = ((code << 8) | buf3[buf3_pos]) & 0xFFFFFFFF
        buf3_pos += 1

    if out_size == 0:
        return b''

    # Mutable pointers for buf1 and buf2
    buf1_pos = 0
    buf1_remaining = size1
    buf2_pos = 0
    buf2_remaining = size2

    while True:
        # Inner copy loop
        limit = size0 - in_pos
        if out_size - out_pos < limit:
            limit = out_size - out_pos

        while limit != 0:
            b = buf0[in_pos]
            out_buf[out_pos] = b
            out_pos += 1

            # IsJ(prevByte, b): (b & 0xFE) == 0xE8 || (prevByte == 0x0F && (b & 0xF0) == 0x80)
            is_j = ((b & 0xFE) == 0xE8) or (prev_byte == 0x0F and (b & 0xF0) == 0x80)
            if is_j:
                break

            in_pos += 1
            prev_byte = b
            limit -= 1

        if limit == 0 or out_pos == out_size:
            break

        # Re-read the same byte (the one that triggered IsJ) and advance inPos
        b = buf0[in_pos]
        in_pos += 1

        # Select probability model
        if b == 0xE8:
            prob_idx = prev_byte
        elif b == 0xE9:
            prob_idx = 256
        else:
            prob_idx = 257

        # IF_BIT_0: range decode
        ttt = p[prob_idx]
        bound = (rc_range >> 11) * ttt

        if code < bound:
            # UPDATE_0: bit = 0, no address correction
            rc_range = bound
            p[prob_idx] = ttt + ((2048 - ttt) >> 5)
            # NORMALIZE
            if rc_range < 0x01000000:
                if buf3_pos >= size3:
                    print(f"[!] RC normalize error at out_pos=0x{out_pos:X}")
                    return bytes(out_buf[:out_pos])
                rc_range = (rc_range << 8) & 0xFFFFFFFF
                code = ((code << 8) | buf3[buf3_pos]) & 0xFFFFFFFF
                buf3_pos += 1

            prev_byte = b
        else:
            # UPDATE_1: bit = 1, apply address correction
            rc_range = (rc_range - bound) & 0xFFFFFFFF
            code = (code - bound) & 0xFFFFFFFF
            p[prob_idx] = ttt - (ttt >> 5)
            # NORMALIZE
            if rc_range < 0x01000000:
                if buf3_pos >= size3:
                    print(f"[!] RC normalize error at out_pos=0x{out_pos:X}")
                    return bytes(out_buf[:out_pos])
                rc_range = (rc_range << 8) & 0xFFFFFFFF
                code = ((code << 8) | buf3[buf3_pos]) & 0xFFFFFFFF
                buf3_pos += 1

            # Read 4 bytes from appropriate address stream
            if b == 0xE8:
                if buf1_remaining < 4:
                    print(f"[!] CALL stream exhausted at out_pos=0x{out_pos:X}")
                    return bytes(out_buf[:out_pos])
                v = buf1[buf1_pos:buf1_pos + 4]
                buf1_pos += 4
                buf1_remaining -= 4
            else:
                if buf2_remaining < 4:
                    print(f"[!] JMP stream exhausted at out_pos=0x{out_pos:X}")
                    return bytes(out_buf[:out_pos])
                v = buf2[buf2_pos:buf2_pos + 4]
                buf2_pos += 4
                buf2_remaining -= 4

            # dest = big_endian(v) - (outPos + 4)
            dest = (((v[0] << 24) | (v[1] << 16) | (v[2] << 8) | v[3])
                    - (out_pos + 4)) & 0xFFFFFFFF

            out_buf[out_pos] = dest & 0xFF
            out_pos += 1
            if out_pos == out_size:
                break

            out_buf[out_pos] = (dest >> 8) & 0xFF
            out_pos += 1
            if out_pos == out_size:
                break

            out_buf[out_pos] = (dest >> 16) & 0xFF
            out_pos += 1
            if out_pos == out_size:
                break

            prev_byte = (dest >> 24) & 0xFF
            out_buf[out_pos] = prev_byte
            out_pos += 1

    if out_pos != out_size:
        print(f"[!] BCJ2 decode incomplete: {out_pos}/{out_size}")
        print(f"    in_pos={in_pos}/{size0}, buf1_pos={buf1_pos}/{size1}")
        print(f"    buf2_pos={buf2_pos}/{size2}, buf3_pos={buf3_pos}/{size3}")
    else:
        print(f"    [OK] BCJ2 decode complete: {out_size} bytes")

    return bytes(out_buf[:out_pos])


def extract_tar(data: bytes, output_dir: str, offset: int = 0) -> int:
    """
    Extract files from tar (ustar) data.
    Based on omaha/mi_exe_stub/tar.cc Tar::ExtractOneFile.
    """
    pos = offset
    file_count = 0

    while pos + 512 <= len(data):
        header = data[pos:pos + 512]

        # kUstarDone = {0,0,0,0,0}: check magic field for end marker
        if header[257:262] == b'\x00\x00\x00\x00\x00':
            print(f"  [*] End-of-archive at offset 0x{pos:X}")
            break

        # Check ustar magic
        if header[257:262] != b'ustar':
            if header[257:265] == b'ustar  \x00':
                pass  # GNU tar variant
            else:
                print(f"  [!] Bad magic at offset 0x{pos:X}: {header[257:265]!r}")
                break

        # Filename (offset 0, 100 bytes)
        name = header[0:100].split(b'\x00', 1)[0].decode('utf-8', errors='replace').strip()

        # Prefix (offset 345, 155 bytes)
        prefix = header[345:500].split(b'\x00', 1)[0].decode('utf-8', errors='replace').strip()
        if prefix:
            name = prefix + '/' + name

        if not name:
            print(f"  [*] Empty filename at offset 0x{pos:X}, stopping.")
            break

        # File size (octal string at offset 0x7C, 12 bytes)
        size_raw = header[0x7C:0x7C + 12].rstrip(b'\x00').rstrip(b' ').decode('ascii').strip()
        file_size = int(size_raw, 8) if size_raw else 0

        # Type flag (offset 156)
        typeflag = header[156:157]

        data_start = pos + 512
        # Padding to 512-byte boundary: (512 - file_size & 0x1ff) from tar.cc
        padded_size = file_size + ((-file_size) & 0x1FF) if file_size > 0 else 0

        # Sanitize filename
        name = name.lstrip('/')
        name = name.replace('..', '_')

        if typeflag == b'5' or name.endswith('/'):
            dir_path = os.path.join(output_dir, name)
            os.makedirs(dir_path, exist_ok=True)
            print(f"  [DIR]  {name}")
        else:
            file_path = os.path.join(output_dir, name)
            file_dir = os.path.dirname(file_path)
            if file_dir:
                os.makedirs(file_dir, exist_ok=True)

            file_data = data[data_start:data_start + file_size]
            with open(file_path, 'wb') as f:
                f.write(file_data)
            file_count += 1
            print(f"  [FILE] {name} ({file_size:,} bytes)")

        pos = data_start + padded_size

    print(f"\n[*] Total: {file_count} file(s) extracted to '{output_dir}'")
    return file_count


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <resource_102_file> [output_dir]")
        print()
        print("  Extracts the embedded tar archive from a Google Omaha Setup")
        print("  resource file (type 'B', ID 102).")
        print()
        print("  Pipeline: LZMA -> BCJ2 -> tar")
        sys.exit(1)

    input_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else 'extracted'

    with open(input_file, 'rb') as f:
        raw_data = f.read()

    print(f"[*] Read {len(raw_data):,} bytes from '{input_file}'")

    # === Step 1: LZMA decompress ===
    print(f"\n[*] Step 1: LZMA decompression...")
    try:
        lzma_data = lzma.decompress(raw_data, format=lzma.FORMAT_ALONE)
        print(f"    {len(raw_data):,} -> {len(lzma_data):,} bytes")
    except lzma.LZMAError as e:
        print(f"[!] LZMA decompression failed: {e}")
        sys.exit(1)

    # === Step 2: Parse BCJ2 header (20 bytes) ===
    # From mi.cc DecompressBufferToFile:
    #   uint32 original_size, stream0_size, stream1_size, stream2_size, stream3_size
    if len(lzma_data) < 20:
        print("[!] Data too small for BCJ2 header")
        sys.exit(1)

    original_size = struct.unpack_from('<I', lzma_data, 0)[0]
    stream0_size = struct.unpack_from('<I', lzma_data, 4)[0]
    stream1_size = struct.unpack_from('<I', lzma_data, 8)[0]
    stream2_size = struct.unpack_from('<I', lzma_data, 12)[0]
    stream3_size = struct.unpack_from('<I', lzma_data, 16)[0]

    print(f"\n[*] Step 2: BCJ2 header:")
    print(f"    Original size:   {original_size:,} (0x{original_size:X})")
    print(f"    Stream 0 (main): {stream0_size:,} (0x{stream0_size:X})")
    print(f"    Stream 1 (CALL): {stream1_size:,} (0x{stream1_size:X})")
    print(f"    Stream 2 (JMP):  {stream2_size:,} (0x{stream2_size:X})")
    print(f"    Stream 3 (RC):   {stream3_size:,} (0x{stream3_size:X})")

    expected_data = stream0_size + stream1_size + stream2_size + stream3_size
    actual_data = len(lzma_data) - 20
    print(f"    Expected data:   {expected_data:,}")
    print(f"    Available data:  {actual_data:,}")

    if expected_data != actual_data:
        print(f"[!] Warning: size mismatch ({expected_data} != {actual_data})")

    # Extract streams (p starts at offset 20)
    p = 20
    buf0 = lzma_data[p:p + stream0_size]; p += stream0_size
    buf1 = lzma_data[p:p + stream1_size]; p += stream1_size
    buf2 = lzma_data[p:p + stream2_size]; p += stream2_size
    buf3 = lzma_data[p:p + stream3_size]

    # === Step 3: BCJ2 decode ===
    print(f"\n[*] Step 3: BCJ2 decode...")
    tar_data = bcj2_decode(
        buf0, stream0_size,
        buf1, stream1_size,
        buf2, stream2_size,
        buf3, stream3_size,
        original_size
    )

    # Verify
    if len(tar_data) >= 262 and tar_data[257:262] == b'ustar':
        print(f"    [OK] Valid tar archive detected!")
    else:
        print(f"    [!] Warning: no tar magic at expected position")
        idx = tar_data.find(b'ustar')
        if idx >= 0:
            print(f"    Found 'ustar' at offset {idx}")

    # Save raw tar for inspection
    os.makedirs(output_dir, exist_ok=True)
    tar_path = os.path.join(output_dir, '_payload.tar')
    with open(tar_path, 'wb') as f:
        f.write(tar_data)
    print(f"    Saved raw tar: '{tar_path}'")

    # === Step 4: Extract tar ===
    print(f"\n[*] Step 4: Extracting tar archive...")
    extract_tar(tar_data, output_dir)
    os.remove(tar_path)
    print(f"    Temporary tar file removed.")


if __name__ == '__main__':
    main()