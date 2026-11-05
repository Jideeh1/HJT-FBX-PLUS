# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#aes for encrypted 7z (aes-256 cbc) n winzip aes zips (aes ctr, little endian counter bc of course)
#
#backend order:
#  pycryptodomex / pycryptodome / cryptography if installed (fast, c)
#  numpy vectorized aes (always there in blender). cbc DEcryption n ctr r both parallel
#   per block so we can do thousands of blocks per numpy op, like 30-60 mb/s, not bad for python lol
#  plain python as the last resort, slow af but works

from __future__ import annotations

import hashlib
import struct

try:
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None


#ok aes tables. computed at import from gf(2^8) math instead of pasting 4kb of hex
def _xtime(a):
    a <<= 1
    return (a ^ 0x11B) & 0xFF if a & 0x100 else a


def _gmul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _build_tables():
    sbox = [0] * 256
    inv = [0] * 256
    #multiplicative inverse via brute force, 65k mults once at import, whatever
    ginv = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if _gmul(a, b) == 1:
                ginv[a] = b
                break
    for x in range(256):
        b = ginv[x]
        s = b
        for i in range(1, 5):
            s ^= ((b << i) | (b >> (8 - i))) & 0xFF
        s ^= 0x63
        sbox[x] = s
        inv[s] = x
    te0 = [0] * 256
    td0 = [0] * 256
    for x in range(256):
        s = sbox[x]
        te0[x] = (_gmul(s, 2) << 24) | (s << 16) | (s << 8) | _gmul(s, 3)
        si = inv[x]
        td0[x] = (_gmul(si, 14) << 24) | (_gmul(si, 9) << 16) | (_gmul(si, 13) << 8) | _gmul(si, 11)

    def ror(t, n):
        return [((v >> n) | (v << (32 - n))) & 0xFFFFFFFF for v in t]

    return sbox, inv, (te0, ror(te0, 8), ror(te0, 16), ror(te0, 24)), (td0, ror(td0, 8), ror(td0, 16), ror(td0, 24))


SBOX, INV_SBOX, TE, TD = _build_tables()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def expand_key(key):
    #standard key schedule -> (encrypt round keys, decrypt round keys, rounds)
    nk = len(key) // 4
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16, 24 or 32 bytes")
    nr = nk + 6
    w = list(struct.unpack(">%dI" % nk, key))
    for i in range(nk, 4 * (nr + 1)):
        t = w[i - 1]
        if i % nk == 0:
            t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
            t = (SBOX[t >> 24] << 24) | (SBOX[(t >> 16) & 0xFF] << 16) | (SBOX[(t >> 8) & 0xFF] << 8) | SBOX[t & 0xFF]
            t ^= _RCON[i // nk - 1] << 24
        elif nk > 6 and i % nk == 4:
            t = (SBOX[t >> 24] << 24) | (SBOX[(t >> 16) & 0xFF] << 16) | (SBOX[(t >> 8) & 0xFF] << 8) | SBOX[t & 0xFF]
        w.append(w[i - nk] ^ t)
    #decrypt schedule = reversed rounds w InvMixColumns on the middle ones (equivalent inverse cipher)
    dk = []
    for r in range(nr, -1, -1):
        rk = w[4 * r:4 * r + 4]
        if 0 < r < nr:
            rk = [TD[0][SBOX[v >> 24]] ^ TD[1][SBOX[(v >> 16) & 0xFF]] ^ TD[2][SBOX[(v >> 8) & 0xFF]] ^ TD[3][SBOX[v & 0xFF]] for v in rk]
        dk.extend(rk)
    return w, dk, nr


#pure python block ops (slow path)
def _enc_block(rk, nr, s0, s1, s2, s3):
    te0, te1, te2, te3 = TE
    s0 ^= rk[0]; s1 ^= rk[1]; s2 ^= rk[2]; s3 ^= rk[3]  # noqa: E702
    k = 4
    for _ in range(nr - 1):
        t0 = te0[s0 >> 24] ^ te1[(s1 >> 16) & 255] ^ te2[(s2 >> 8) & 255] ^ te3[s3 & 255] ^ rk[k]
        t1 = te0[s1 >> 24] ^ te1[(s2 >> 16) & 255] ^ te2[(s3 >> 8) & 255] ^ te3[s0 & 255] ^ rk[k + 1]
        t2 = te0[s2 >> 24] ^ te1[(s3 >> 16) & 255] ^ te2[(s0 >> 8) & 255] ^ te3[s1 & 255] ^ rk[k + 2]
        t3 = te0[s3 >> 24] ^ te1[(s0 >> 16) & 255] ^ te2[(s1 >> 8) & 255] ^ te3[s2 & 255] ^ rk[k + 3]
        s0, s1, s2, s3 = t0, t1, t2, t3
        k += 4
    S = SBOX
    return ((S[s0 >> 24] << 24 | S[(s1 >> 16) & 255] << 16 | S[(s2 >> 8) & 255] << 8 | S[s3 & 255]) ^ rk[k],
            (S[s1 >> 24] << 24 | S[(s2 >> 16) & 255] << 16 | S[(s3 >> 8) & 255] << 8 | S[s0 & 255]) ^ rk[k + 1],
            (S[s2 >> 24] << 24 | S[(s3 >> 16) & 255] << 16 | S[(s0 >> 8) & 255] << 8 | S[s1 & 255]) ^ rk[k + 2],
            (S[s3 >> 24] << 24 | S[(s0 >> 16) & 255] << 16 | S[(s1 >> 8) & 255] << 8 | S[s2 & 255]) ^ rk[k + 3])


def _dec_block(dk, nr, s0, s1, s2, s3):
    td0, td1, td2, td3 = TD
    s0 ^= dk[0]; s1 ^= dk[1]; s2 ^= dk[2]; s3 ^= dk[3]  # noqa: E702
    k = 4
    for _ in range(nr - 1):
        t0 = td0[s0 >> 24] ^ td1[(s3 >> 16) & 255] ^ td2[(s2 >> 8) & 255] ^ td3[s1 & 255] ^ dk[k]
        t1 = td0[s1 >> 24] ^ td1[(s0 >> 16) & 255] ^ td2[(s3 >> 8) & 255] ^ td3[s2 & 255] ^ dk[k + 1]
        t2 = td0[s2 >> 24] ^ td1[(s1 >> 16) & 255] ^ td2[(s0 >> 8) & 255] ^ td3[s3 & 255] ^ dk[k + 2]
        t3 = td0[s3 >> 24] ^ td1[(s2 >> 16) & 255] ^ td2[(s1 >> 8) & 255] ^ td3[s0 & 255] ^ dk[k + 3]
        s0, s1, s2, s3 = t0, t1, t2, t3
        k += 4
    S = INV_SBOX
    return ((S[s0 >> 24] << 24 | S[(s3 >> 16) & 255] << 16 | S[(s2 >> 8) & 255] << 8 | S[s1 & 255]) ^ dk[k],
            (S[s1 >> 24] << 24 | S[(s0 >> 16) & 255] << 16 | S[(s3 >> 8) & 255] << 8 | S[s2 & 255]) ^ dk[k + 1],
            (S[s2 >> 24] << 24 | S[(s1 >> 16) & 255] << 16 | S[(s0 >> 8) & 255] << 8 | S[s3 & 255]) ^ dk[k + 2],
            (S[s3 >> 24] << 24 | S[(s2 >> 16) & 255] << 16 | S[(s1 >> 8) & 255] << 8 | S[s0 & 255]) ^ dk[k + 3])


if _np is not None:
    _NTE = [_np.array(t, dtype=_np.uint32) for t in TE]
    _NTD = [_np.array(t, dtype=_np.uint32) for t in TD]
    _NS = _np.array(SBOX, dtype=_np.uint32)
    _NIS = _np.array(INV_SBOX, dtype=_np.uint32)


def _np_ecb(data, rk, nr, decrypt):
    #numpy version, whole buffer at once. s0..s3 r big endian column words of every block
    w = _np.frombuffer(data, dtype=">u4").astype(_np.uint32).reshape(-1, 4)
    s0, s1, s2, s3 = w[:, 0], w[:, 1], w[:, 2], w[:, 3]
    t = _NTD if decrypt else _NTE
    b0, b1, b2, b3 = t
    s0 = s0 ^ _np.uint32(rk[0]); s1 = s1 ^ _np.uint32(rk[1]); s2 = s2 ^ _np.uint32(rk[2]); s3 = s3 ^ _np.uint32(rk[3])  # noqa: E702
    k = 4
    m = _np.uint32(255)
    for _ in range(nr - 1):
        if decrypt:
            t0 = b0[s0 >> 24] ^ b1[(s3 >> 16) & m] ^ b2[(s2 >> 8) & m] ^ b3[s1 & m] ^ _np.uint32(rk[k])
            t1 = b0[s1 >> 24] ^ b1[(s0 >> 16) & m] ^ b2[(s3 >> 8) & m] ^ b3[s2 & m] ^ _np.uint32(rk[k + 1])
            t2 = b0[s2 >> 24] ^ b1[(s1 >> 16) & m] ^ b2[(s0 >> 8) & m] ^ b3[s3 & m] ^ _np.uint32(rk[k + 2])
            t3 = b0[s3 >> 24] ^ b1[(s2 >> 16) & m] ^ b2[(s1 >> 8) & m] ^ b3[s0 & m] ^ _np.uint32(rk[k + 3])
        else:
            t0 = b0[s0 >> 24] ^ b1[(s1 >> 16) & m] ^ b2[(s2 >> 8) & m] ^ b3[s3 & m] ^ _np.uint32(rk[k])
            t1 = b0[s1 >> 24] ^ b1[(s2 >> 16) & m] ^ b2[(s3 >> 8) & m] ^ b3[s0 & m] ^ _np.uint32(rk[k + 1])
            t2 = b0[s2 >> 24] ^ b1[(s3 >> 16) & m] ^ b2[(s0 >> 8) & m] ^ b3[s1 & m] ^ _np.uint32(rk[k + 2])
            t3 = b0[s3 >> 24] ^ b1[(s0 >> 16) & m] ^ b2[(s1 >> 8) & m] ^ b3[s2 & m] ^ _np.uint32(rk[k + 3])
        s0, s1, s2, s3 = t0, t1, t2, t3
        k += 4
    S = _NIS if decrypt else _NS
    if decrypt:
        o0 = (S[s0 >> 24] << 24) | (S[(s3 >> 16) & m] << 16) | (S[(s2 >> 8) & m] << 8) | S[s1 & m]
        o1 = (S[s1 >> 24] << 24) | (S[(s0 >> 16) & m] << 16) | (S[(s3 >> 8) & m] << 8) | S[s2 & m]
        o2 = (S[s2 >> 24] << 24) | (S[(s1 >> 16) & m] << 16) | (S[(s0 >> 8) & m] << 8) | S[s3 & m]
        o3 = (S[s3 >> 24] << 24) | (S[(s2 >> 16) & m] << 16) | (S[(s1 >> 8) & m] << 8) | S[s0 & m]
    else:
        o0 = (S[s0 >> 24] << 24) | (S[(s1 >> 16) & m] << 16) | (S[(s2 >> 8) & m] << 8) | S[s3 & m]
        o1 = (S[s1 >> 24] << 24) | (S[(s2 >> 16) & m] << 16) | (S[(s3 >> 8) & m] << 8) | S[s0 & m]
        o2 = (S[s2 >> 24] << 24) | (S[(s3 >> 16) & m] << 16) | (S[(s0 >> 8) & m] << 8) | S[s1 & m]
        o3 = (S[s3 >> 24] << 24) | (S[(s0 >> 16) & m] << 16) | (S[(s1 >> 8) & m] << 8) | S[s2 & m]
    out = _np.empty((len(s0), 4), dtype=">u4")
    out[:, 0] = o0 ^ _np.uint32(rk[k])
    out[:, 1] = o1 ^ _np.uint32(rk[k + 1])
    out[:, 2] = o2 ^ _np.uint32(rk[k + 2])
    out[:, 3] = o3 ^ _np.uint32(rk[k + 3])
    return out.tobytes()


def _py_ecb(data, rk, nr, decrypt):
    fn = _dec_block if decrypt else _enc_block
    out = bytearray(len(data))
    unpack = struct.unpack_from
    pack = struct.pack_into
    for off in range(0, len(data), 16):
        pack(">4I", out, off, *fn(rk, nr, *unpack(">4I", data, off)))
    return bytes(out)


def _lib_backend():
    #catch BaseException on purpose: a broken/mismatched compiled lib (pyo3 PanicException etc) is NOT an
    # Exception subclass n would take the whole import down w it. we just fall back to numpy aes then
    for mod in ("Cryptodome.Cipher.AES", "Crypto.Cipher.AES"):
        try:
            m = __import__(mod, fromlist=["AES"])
            if hasattr(m, "MODE_ECB"):
                return ("pycryptodome", m)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001
            continue
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401
        return ("cryptography", None)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001
        return (None, None)


_LIB = None


def backend_name():
    global _LIB
    if _LIB is None:
        _LIB = _lib_backend()
    if _LIB[0]:
        return _LIB[0]
    return "numpy" if _np is not None else "python"


class AesEcb:
    #raw block cipher, picks the fastest thing available
    def __init__(self, key, force=None):
        global _LIB
        if _LIB is None:
            _LIB = _lib_backend()
        self.key = bytes(key)
        self.kind = force or (_LIB[0] if _LIB[0] else ("numpy" if _np is not None else "python"))
        if self.kind == "pycryptodome":
            m = _LIB[1]
            self._enc = m.new(self.key, m.MODE_ECB)
        elif self.kind == "cryptography":
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            self._cipher = Cipher(algorithms.AES(self.key), modes.ECB())
        else:
            self._rk, self._dk, self._nr = expand_key(self.key)

    def encrypt(self, data):
        if self.kind == "pycryptodome":
            return self._enc.encrypt(data)
        if self.kind == "cryptography":
            e = self._cipher.encryptor()
            return e.update(data) + e.finalize()
        if self.kind == "numpy":
            return _np_ecb(data, self._rk, self._nr, False)
        return _py_ecb(data, self._rk, self._nr, False)

    def decrypt(self, data):
        if self.kind == "pycryptodome":
            return self._enc.decrypt(data)
        if self.kind == "cryptography":
            d = self._cipher.decryptor()
            return d.update(data) + d.finalize()
        if self.kind == "numpy":
            return _np_ecb(data, self._dk, self._nr, True)
        return _py_ecb(data, self._dk, self._nr, True)


class CbcDecryptor:
    #streaming cbc decrypt, feed it chunks (any size), it keeps the leftover bytes n the iv
    def __init__(self, key, iv, force=None):
        self._ecb = AesEcb(key, force)
        self._iv = bytes(iv)
        self._pending = b""

    def decrypt(self, data):
        buf = self._pending + data
        n = len(buf) - (len(buf) % 16)
        self._pending = buf[n:]
        if n == 0:
            return b""
        block = buf[:n]
        plain = self._ecb.decrypt(block)
        prev = self._iv + block[:-16]
        self._iv = block[-16:]
        return _xor(plain, prev)


def _xor(a, b):
    if _np is not None and len(a) >= 64:
        return (_np.frombuffer(a, dtype=_np.uint8) ^ _np.frombuffer(b, dtype=_np.uint8)).tobytes()
    n = len(a)
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(n, "little")


class WinZipCtr:
    #winzip ae-1/ae-2 ctr mode: counter is a 16 byte LITTLE endian int starting at 1
    # (yes its different from every other ctr mode on earth)
    def __init__(self, key, force=None):
        self._ecb = AesEcb(key, force)
        self._counter = 1
        self._keystream = b""

    def _blocks(self, nblocks):
        start = self._counter
        self._counter += nblocks
        if _np is not None and nblocks > 4:
            lo = _np.arange(start, start + nblocks, dtype=_np.uint64)
            arr = _np.zeros((nblocks, 2), dtype="<u8")
            arr[:, 0] = lo
            raw = arr.tobytes()
        else:
            raw = b"".join((start + i).to_bytes(16, "little") for i in range(nblocks))
        return self._ecb.encrypt(raw)

    def process(self, data):
        need = len(data) - len(self._keystream)
        if need > 0:
            self._keystream += self._blocks((need + 15) // 16)
        ks = self._keystream[:len(data)]
        self._keystream = self._keystream[len(data):]
        return _xor(data, ks)


_KEY_CACHE = {}


def sevenzip_key(password, salt, cycles_power):
    #7z aes key = sha256 over (salt + utf16le password + 8 byte counter) repeated 2^cycles times
    # 2^19 rounds by default. building the buffer w numpy instead of a python loop = way faster
    pw = password.encode("utf-16-le") if isinstance(password, str) else password
    ck = (pw, bytes(salt), cycles_power)
    hit = _KEY_CACHE.get(ck)
    if hit is not None:
        return hit
    if cycles_power == 0x3F:
        key = (bytes(salt) + pw + b"\0" * 32)[:32]
    else:
        h = hashlib.sha256()
        prefix = bytes(salt) + pw
        rounds = 1 << cycles_power
        batch = 8192
        plen = len(prefix)
        for start in range(0, rounds, batch):
            n = min(batch, rounds - start)
            if _np is not None:
                buf = _np.empty((n, plen + 8), dtype=_np.uint8)
                if plen:
                    buf[:, :plen] = _np.frombuffer(prefix, dtype=_np.uint8)
                buf[:, plen:] = _np.arange(start, start + n, dtype="<u8").view(_np.uint8).reshape(n, 8)
                h.update(buf.tobytes())
            else:
                h.update(b"".join(prefix + struct.pack("<Q", i) for i in range(start, start + n)))
        key = h.digest()
    if len(_KEY_CACHE) > 64:
        _KEY_CACHE.clear()
    _KEY_CACHE[ck] = key
    return key
