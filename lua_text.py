"""Tokenizer/parser for the restricted Lua text inside JA3 savegames.

Validated against real saves (all 10 in the user's save folder parse, and
re-emit character-identical). Handles the shapes the game actually writes:

  return { game = PlaceObj('GameClass', { ... }), gvars = { ... } }
  PlaceObj('X', { 'Key', value, 'Key2', value2, })     -- positional prop list
  PlaceUnitData('Fox', { ... })
  PlaceInventoryItem('AK47', { 'Condition', 66, ... })
  PlaceCharacterEffect('Deadeye', { 'CampaignTimeAdded', 986842200, })
  T{ _language = "English", "Frank Jaeger" }           -- call-with-table sugar
  T(586375682135, "YELLOW JACKETS")
  point(6938, 888)

Every node records its exact source span, so edits are applied as minimal
text patches and anything untouched survives byte-for-byte.
"""
import re

_PARTS = [
    ('ws',   r'\s+'),
    ('cmt',  r'--\[\[.*?\]\]|--[^\n]*'),
    ('name', r'[A-Za-z_][A-Za-z_0-9]*'),
    ('num',  r'-?(?:0x[0-9a-fA-F]+|\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+|\d+)(?:[eE][-+]?\d+)?'),
    ('str',  r'"(?:[^"\\]|\\.)*"' + '|' + r"'(?:[^'\\]|\\.)*'"),
    ('punc', r'[{}\[\](),=;.]'),
]
TOKEN = re.compile('|'.join('(?P<%s>%s)' % p for p in _PARTS), re.S)

# Constructors whose first string argument names the thing being placed.
PLACERS = ('PlaceObj', 'PlaceUnitData', 'PlaceInventoryItem', 'PlaceCharacterEffect')


class LuaSyntaxError(ValueError):
    pass


class Tok:
    __slots__ = ('kind', 'val', 'i', 'j')

    def __init__(s, kind, val, i, j):
        s.kind, s.val, s.i, s.j = kind, val, i, j

    def __repr__(s):
        return '%s:%r@%d' % (s.kind, s.val, s.i)


def tokenize(text):
    out, pos, n = [], 0, len(text)
    while pos < n:
        m = TOKEN.match(text, pos)
        if not m:
            raise LuaSyntaxError('bad token at %d: %r' % (pos, text[pos:pos + 40]))
        pos = m.end()
        if m.lastgroup not in ('ws', 'cmt'):
            out.append(Tok(m.lastgroup, m.group(), m.start(), m.end()))
    return out


_ESC = {'n': '\n', 't': '\t', 'r': '\r', 'a': '\a', 'b': '\b', 'f': '\f', 'v': '\v'}


def unquote(s):
    return re.sub(r'\\(.)', lambda m: _ESC.get(m.group(1), m.group(1)), s[1:-1])


def quote(s):
    return '"%s"' % s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


class Node:
    """A parsed value. start/end are source offsets covering exactly this value.

    kind: 'table' | 'call' | 'num' | 'str' | 'bool' | 'nil' | 'name'
    """
    __slots__ = ('kind', 'value', 'start', 'end', 'items', 'fields', 'fname', 'args')

    def __init__(s, kind, start, end, value=None):
        s.kind, s.start, s.end, s.value = kind, start, end, value
        s.items = []    # table: positional Nodes, in order
        s.fields = {}   # table: key -> (key_start, key_end, Node)
        s.fname = None  # call: function name
        s.args = []     # call: argument Nodes

    # -- table access ---------------------------------------------------
    def get(s, key):
        """Named field of a table (k = v or ["k"] = v)."""
        f = s.fields.get(key)
        return f[2] if f else None

    def keys(s):
        return list(s.fields)

    # -- PlaceObj-style property lists -----------------------------------
    def props(s):
        """{'Key': Node} for a {'Key', value, ...} property table, merged
        with any named fields present in the same table."""
        out = {}
        it = s.items
        for i in range(0, len(it) - 1, 2):
            if it[i].kind == 'str':
                out[it[i].value] = it[i + 1]
        for k, v in s.fields.items():
            out[k] = v[2]
        return out

    def prop_key_node(s, key):
        """The 'Key' string node itself in a property list (for insertions)."""
        it = s.items
        for i in range(0, len(it) - 1, 2):
            if it[i].kind == 'str' and it[i].value == key:
                return it[i]
        return None

    # -- call access -----------------------------------------------------
    def table(s):
        """The payload table of a PlaceObj-ish call, or the node itself."""
        if s.kind == 'table':
            return s
        if s.kind == 'call':
            for a in reversed(s.args):
                if a.kind == 'table':
                    return a
        return None

    def placed_name(s):
        """'Fox' out of PlaceUnitData('Fox', {...})."""
        if s.kind == 'call' and s.args and s.args[0].kind == 'str':
            return s.args[0].value
        return None

    def field(s, key):
        """Field lookup that transparently unwraps PlaceObj-ish calls and
        accepts both `k = v` and positional `'k', v` property styles."""
        t = s.table()
        if t is None:
            return None
        v = t.get(key)
        if v is not None:
            return v
        return t.props().get(key)

    def __repr__(s):
        return '<%s %s>' % (s.kind, s.fname if s.kind == 'call' else s.value)


class Parser:
    def __init__(s, text):
        s.text = text
        s.toks = tokenize(text)
        s.p = 0

    def peek(s, k=0):
        return s.toks[s.p + k] if s.p + k < len(s.toks) else None

    def take(s, val=None):
        if s.p >= len(s.toks):
            raise LuaSyntaxError('unexpected end of input')
        t = s.toks[s.p]
        s.p += 1
        if val is not None and t.val != val:
            raise LuaSyntaxError('expected %r got %r' % (val, t))
        return t

    def parse_chunk(s):
        t = s.peek()
        if t and t.kind == 'name' and t.val == 'return':
            s.take()
        v = s.parse_value()
        nxt = s.peek()
        if nxt is not None and nxt.val != ';':
            raise LuaSyntaxError('trailing content at %d: %r' % (nxt.i, nxt.val))
        return v

    def parse_value(s):
        t = s.peek()
        if t is None:
            raise LuaSyntaxError('unexpected end of input')
        if t.val == '{':
            return s.parse_table()
        if t.kind == 'num':
            s.take()
            v = float(t.val) if ('.' in t.val or 'e' in t.val.lower()) else int(t.val, 0)
            return Node('num', t.i, t.j, v)
        if t.kind == 'str':
            s.take()
            return Node('str', t.i, t.j, unquote(t.val))
        if t.kind == 'name':
            if t.val in ('true', 'false'):
                s.take()
                return Node('bool', t.i, t.j, t.val == 'true')
            if t.val == 'nil':
                s.take()
                return Node('nil', t.i, t.j, None)
            nxt = s.peek(1)
            if nxt and nxt.val in ('(', '{'):
                return s.parse_call()
            s.take()
            return Node('name', t.i, t.j, t.val)
        raise LuaSyntaxError('unexpected token %r at %d' % (t.val, t.i))

    def parse_call(s):
        nt = s.take()
        node = Node('call', nt.i, 0)
        node.fname = nt.val
        if s.peek().val == '{':                       # T{...} sugar
            node.args.append(s.parse_table())
            node.end = node.args[0].end
            return node
        s.take('(')
        while s.peek().val != ')':
            node.args.append(s.parse_value())
            if s.peek().val == ',':
                s.take()
        node.end = s.take(')').j
        return node

    def parse_table(s):
        open_t = s.take('{')
        node = Node('table', open_t.i, 0)
        while s.peek().val != '}':
            t = s.peek()
            key = ks = ke = None
            if t.val == '[':                                              # ["k"] = v
                s.take('[')
                kt = s.take()
                s.take(']')
                s.take('=')
                key = unquote(kt.val) if kt.kind == 'str' else kt.val
                ks, ke = kt.i, kt.j
            elif t.kind == 'name' and s.peek(1) and s.peek(1).val == '=':  # k = v
                kt = s.take()
                s.take('=')
                key, ks, ke = kt.val, kt.i, kt.j
            v = s.parse_value()
            if key is None:
                node.items.append(v)
            else:
                node.fields[key] = (ks, ke, v)
            if s.peek().val in (',', ';'):
                s.take()
        node.end = s.take('}').j
        return node


def parse(text):
    return Parser(text).parse_chunk()


def walk(node):
    """Depth-first over every Node in the tree."""
    yield node
    if node.kind == 'table':
        for it in node.items:
            for x in walk(it):
                yield x
        for _ks, _ke, v in node.fields.values():
            for x in walk(v):
                yield x
    elif node.kind == 'call':
        for a in node.args:
            for x in walk(a):
                yield x


class Patcher:
    """Collects (start, end, replacement) edits against the ORIGINAL text and
    applies them in one pass, so offsets never shift under each other."""

    def __init__(s, text):
        s.text = text
        s.edits = []

    def replace(s, node, new_src):
        s.edits.append((node.start, node.end, new_src))

    def replace_span(s, start, end, new_src):
        s.edits.append((start, end, new_src))

    def insert(s, at, new_src):
        s.edits.append((at, at, new_src))

    def set_span(s, start, end, new_src):
        """Replace this span, revising any edit already queued for it.

        For a value that is written more than once in a batch - a stack topped
        up by two separate adds - where replace_span would queue a second edit
        over the first and apply() would refuse them as overlapping. The last
        caller wins, which is what a running total means.
        """
        for i, (a, b, _) in enumerate(s.edits):
            if a == start and b == end:
                s.edits[i] = (start, end, new_src)
                return
        s.edits.append((start, end, new_src))

    def delete_span(s, start, end):
        s.edits.append((start, end, ''))

    def __len__(s):
        return len(s.edits)

    def apply(s):
        out, prev = [], 0
        for a, b, new in sorted(s.edits, key=lambda e: (e[0], e[1])):
            if a < prev:
                raise ValueError('overlapping edits near offset %d' % a)
            out.append(s.text[prev:a])
            out.append(new)
            prev = b
        out.append(s.text[prev:])
        return ''.join(out)
