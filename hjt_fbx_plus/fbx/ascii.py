# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#ascii fbx reader (6.x n 7.x) that spits out typed FbxNode trees
#
#ascii fbx doesnt store prop types at all (thx autodesk), so after each node is read
# ascii_schema gives it the binary type codes the sdk wouldve written. that way the
# tree can be written straight back out as binary, which is how ascii 7.x files get fed
# to blenders python importer (it only eats binary)
#
#big number arrays get parsed in bulk, one regex match + one split, not token by token
#otherwise a 300mb ascii file takes literally forever lol


from __future__ import annotations

import array
import base64
import re

from .elements import FbxArray, FbxError, FbxNode
from . import ascii_schema

_WS = re.compile(rb"[ \t\r]*")
_KEY = re.compile(rb"([A-Za-z_][A-Za-z0-9_|\-.]*)[ \t]*:")
_NUM = (rb"(?:[-+]?(?:\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)(?:#[A-Za-z]+\d*)?"
        rb"|[-+]?(?:inf|nan|INF|NAN)(?![A-Za-z0-9_]))")
#a run of numbers w commas between, the commas can be on the next line too (6.x does that, ew)
_NUMLIST = re.compile(_NUM + rb"(?:[ \t\r\n]*,[ \t\r\n]*" + _NUM + rb")*")
_STRING = re.compile(rb'"([^"]*)"')
_WORD = re.compile(rb"[A-Za-z_][A-Za-z0-9_|\-.]*")
_STAR = re.compile(rb"\*[ \t]*(\d+)")
_FLOATY = re.compile(rb"[.eE#nN]")
_SPLIT = re.compile(rb"[ \t\r\n]*,[ \t\r\n]*")

_SPECIAL_FLOATS = {
    b"1.#INF": float("inf"), b"-1.#INF": float("-inf"), b"+1.#INF": float("inf"),
    b"1.#IND": float("nan"), b"-1.#IND": float("nan"), b"1.#QNAN": float("nan"),
    b"-1.#QNAN": float("nan"), b"inf": float("inf"), b"-inf": float("-inf"), b"nan": float("nan"),
}


class Word(bytes):
    #bare unquoted token like Y N T F
    __slots__ = ()


def _to_float(tok):
    try:
        return float(tok)
    except ValueError:
        #windows printf gives us gems like 1.#INF n -1.#IND00, have fun
        t = tok.strip()
        base = re.match(rb"([-+]?\d+\.)#([A-Za-z]+)", t)
        if base:
            key = base.group(1) + b"#" + base.group(2)[:4].upper().rstrip(b"0123456789")
            for k, v in _SPECIAL_FLOATS.items():
                if key.upper().startswith(k.upper()):
                    return v
            return float("nan")
        raise FbxError("invalid number %r" % tok)


def _parse_number_span(span):
    #b"1, 2.5,\n 3" -> array.array of d or q
    parts = _SPLIT.split(span.strip())
    if _FLOATY.search(span):
        try:
            return array.array("d", map(float, parts))
        except ValueError:
            return array.array("d", map(_to_float, parts))
    try:
        return array.array("q", map(int, parts))
    except OverflowError:  # pragma: no cover  who puts 2^70 in an fbx
        return array.array("d", map(float, parts))


def _unescape(raw):
    if b"&" in raw:
        raw = raw.replace(b"&quot;", b'"').replace(b"&lf;", b"\n").replace(b"&cr;", b"\r")
    return raw


class AsciiFbx:
    __slots__ = ("root", "version", "node_count")

    def __init__(self, root, version, node_count):
        self.root = root
        self.version = version
        self.node_count = node_count

    def close(self):
        pass


def is_ascii_fbx(prefix):
    #guesswork: ascii fbx starts w ; comments or FBXHeaderExtension
    head = bytes(prefix[:4096]).lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b";") and b"FBX" in head[:200]:
        return True
    return head.startswith(b"FBXHeaderExtension") or b"\nFBXHeaderExtension:" in head


def detect_version(data):
    m = re.search(rb"FBXVersion:[ \t]*(\d+)", data[:65536])
    if m:
        return int(m.group(1))
    m = re.search(rb";\s*FBX\s+(\d+)\.(\d+)\.(\d+)", data[:4096])
    if m:
        return int(m.group(1)) * 1000 + int(m.group(2)) * 100 + int(m.group(3))
    return 0


def read_ascii(path=None, data=None, typed=True, cancel=None):
    #parse ascii fbx from path or data bytes
    # typed = give it binary type codes (needed if u wanna write it as binary)
    # cancel = optional threading.Event we peek at every few thousand nodes
    if data is None:
        with open(path, "rb") as fh:
            data = fh.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    version = detect_version(data)
    parser = _AsciiParser(data, version, typed, cancel)
    root = parser.parse()
    return AsciiFbx(root, version, parser.count)


class _AsciiParser:
    def __init__(self, data, version, typed, cancel):
        self.data = data
        self.n = len(data)
        self.version = version
        self.typed = typed
        self.cancel = cancel
        self.count = 0

    def error(self, pos, msg):
        line = self.data.count(b"\n", 0, pos) + 1
        raise FbxError("ASCII FBX parse error at line %d: %s" % (line, msg))

    def skip_blank(self, pos):
        #skip spaces newlines n ; comments
        data = self.data
        n = self.n
        while pos < n:
            c = data[pos]
            if c in (32, 9, 13, 10):
                pos += 1
            elif c == 59:  #; comment
                nl = data.find(b"\n", pos)
                pos = n if nl < 0 else nl + 1
            else:
                break
        return pos

    def parse(self):
        data = self.data
        n = self.n
        root = FbxNode(b"")
        stack = [root]
        names = []  #names of the open parents, root not included
        pos = 0
        check = 0
        while True:
            pos = self.skip_blank(pos)
            if pos >= n:
                break
            c = data[pos]
            if c == 125:  #}
                if len(stack) == 1:
                    self.error(pos, "unbalanced '}'")
                node = stack.pop()
                names.pop()
                pos += 1
                continue
            m = _KEY.match(data, pos)
            if m is None:
                self.error(pos, "expected a node name, found %r" % data[pos:pos + 20])
            name = m.group(1)
            pos = m.end()
            node = FbxNode(name)
            self.count += 1
            check += 1
            if check >= 4096:
                check = 0
                if self.cancel is not None and self.cancel.is_set():
                    raise FbxError("cancelled")
            stack[-1].children.append(node)
            pos, opens = self.read_values(node, pos)
            if self.typed:
                ascii_schema.assign_types(node, names, self.version)
            if opens:
                stack.append(node)
                names.append(name)
        if len(stack) != 1:
            #file ended w a { still open, its truncated or broken so just say so
            raise FbxError("unexpected end of ASCII FBX (unclosed '{')")
        return root

    def _continues(self, pos):
        #after a newline, does the value list keep going? (next line starts w a comma)
        data = self.data
        n = self.n
        while pos < n and data[pos] in (32, 9, 13, 10):
            pos += 1
        return pos < n and data[pos] == 44

    def read_values(self, node, pos):
        #reads the values after "Name:" -> (pos, opens_block)
        # this lil state machine is the most annoying part of ascii fbx ngl
        data = self.data
        n = self.n
        props = node.props
        last_comma = False
        while pos < n:
            pos = _WS.match(data, pos).end()
            if pos >= n:
                break
            c = data[pos]
            if c == 10:  #newline
                if last_comma or self._continues(pos + 1):
                    pos += 1
                    continue
                return pos + 1, False
            if c == 59:  #comment till end of line
                nl = data.find(b"\n", pos)
                pos = n if nl < 0 else nl
                continue
            if c == 44:  #comma. a leading one means empty first value, Content: , does this
                last_comma = True
                pos += 1
                continue
            if c == 123:  # {
                return pos + 1, True
            if c == 125:  #} closes the parent, not our problem
                return pos, False
            if props and not last_comma:
                #token w no comma before it = new node starts here
                return pos, False
            last_comma = False
            if c == 34:  #string
                m = _STRING.match(data, pos)
                if m is None:
                    self.error(pos, "unterminated string")
                props.append(_unescape(m.group(1)))
                pos = m.end()
                continue
            if c == 42:  #*N array
                pos = self.read_star_array(node, pos)
                continue
            m = _NUMLIST.match(data, pos)
            if m is not None and m.end() > pos:
                span = m.group(0)
                if b"," in span:
                    if self.version and self.version < 7000 and node.name in ascii_schema.ARRAY_NODES_6X:
                        props.append(_AsciiNumbers(_parse_number_span(span)))
                    else:
                        #7.x arrays always use *N {a: ...}, so a plain comma list is just separate scalars
                        # (C: "OO",123,456 is 3 props not an array, learned that the hard way)
                        for tok in _SPLIT.split(span.strip()):
                            props.append(_to_float(tok) if _FLOATY.search(tok) else int(tok))
                else:
                    tok = span.strip()
                    props.append(_to_float(tok) if _FLOATY.search(tok) else int(tok))
                pos = m.end()
                continue
            if _KEY.match(data, pos) is not None:
                return pos, False
            m = _WORD.match(data, pos)
            if m is not None:
                props.append(Word(m.group(0)))
                pos = m.end()
                continue
            self.error(pos, "unexpected character %r" % data[pos:pos + 1])
        return pos, False

    def read_star_array(self, node, pos):
        # *N { a: v,v,v } the 7.x array syntax
        data = self.data
        m = _STAR.match(data, pos)
        if m is None:
            self.error(pos, "bad array length")
        count = int(m.group(1))
        pos = self.skip_blank(m.end())
        if pos >= self.n or data[pos] != 123:
            self.error(pos, "expected '{' after array length")
        close = data.find(b"}", pos)
        if close < 0:
            self.error(pos, "unterminated array")
        body = data[pos + 1:close]
        colon = body.find(b":")
        content = body[colon + 1:] if colon >= 0 else body
        content = re.sub(rb";[^\n]*", b"", content) if b";" in content else content
        content = content.strip()
        if count == 0 or not content:
            vals = array.array("q")
        else:
            vals = _parse_number_span(content)
        if len(vals) != count:
            #count lies sometimes, trust the actual data
            count = len(vals)
        node.props.append(_AsciiNumbers(vals, star=True))
        return close + 1


class _AsciiNumbers:
    #temp holder for a number list before the schema decides what type it is

    __slots__ = ("vals", "star")

    def __init__(self, vals, star=False):
        self.vals = vals
        self.star = star


def finalize_untyped(node):
    #if typed=False was used, turn the temp holders into arrays anyway
    for n in node.walk():
        for i, v in enumerate(n.props):
            if isinstance(v, _AsciiNumbers):
                tc = "d" if v.vals.typecode == "d" else "l"
                n.props[i] = FbxArray.from_values(tc, v.vals)


def decode_content_strings(parts):
    #base64 chunks of an ascii Content prop -> real bytes
    joined = b"".join(p for p in parts if isinstance(p, bytes))
    joined = re.sub(rb"\s+", b"", joined)
    if not joined:
        return b""
    try:
        return base64.b64decode(joined, validate=False)
    except Exception as exc:  #binascii.Error
        raise FbxError("invalid embedded content: %s" % exc) from exc
