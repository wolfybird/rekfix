"""The players' root browse menu (export.pdb table 17), and hiding categories in it.

Each category is one fixed 8-byte row: category id, label id, a flags word and its position in
the menu (1, 2, 3...; 0 = not shown). When rekordbox hides categories (Preferences > Category)
it sets their position to 0 and adds 0x100 to their flags, and numbers the rest 1..n again.
hide() does exactly that in place; rows keep their size, so nothing else in the file moves.
"""
from .pdb import Pdb

HIDDEN_FLAG = 0x100


def items(pdb):
    """[(position or 0, category id, name, MenuItem)] in menu order, hidden ones last."""
    names = pdb.columns()
    out = [(m.sort, m.category, names.get(m.category, f"category {m.category}"), m) for m in pdb.menu()]
    return sorted(out, key=lambda x: (x[0] == 0, x[0], x[2]))


def resolve(pdb, wanted):
    """Category names (any case) or ids -> category ids; unknown names raise ValueError."""
    names = {v.upper(): k for k, v in pdb.columns().items()}
    ids = set()
    for w in wanted:
        w = w.strip()
        if w.isdigit():
            ids.add(int(w))
        elif w.upper() in names:
            ids.add(names[w.upper()])
        else:
            raise ValueError(f"no category called {w!r}; there are: {', '.join(sorted(names))}")
    return ids


def hide(pdb: Pdb, categories):
    """Hide the given category ids from the root menu. Returns the names hidden."""
    names = pdb.columns()
    menu = pdb.menu()
    done = []
    for m in menu:
        if m.category in categories and m.visible:
            pdb.set_u16(m.row.pos + 4, m.flags | HIDDEN_FLAG)
            pdb.set_u16(m.row.pos + 6, 0)
            done.append(names.get(m.category, str(m.category)))
    if done:
        _renumber(pdb)
    return done


def show(pdb: Pdb, categories):
    """Bring hidden categories back, at the end of the menu."""
    names = pdb.columns()
    menu = pdb.menu()
    last = max((m.sort for m in menu), default=0)
    done = []
    for m in menu:
        if m.category in categories and not m.visible:
            last += 1
            pdb.set_u16(m.row.pos + 4, m.flags & ~HIDDEN_FLAG)
            pdb.set_u16(m.row.pos + 6, last)
            done.append(names.get(m.category, str(m.category)))
    if done:
        _renumber(pdb)
    return done


def _renumber(pdb):
    shown = sorted((m for m in pdb.menu() if m.visible), key=lambda m: m.sort)
    for n, m in enumerate(shown, 1):
        if m.sort != n:
            pdb.set_u16(m.row.pos + 6, n)
