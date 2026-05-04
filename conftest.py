"""Root conftest — replaces SessionLocal with a pure in-memory fake.
No database of any kind. ORM objects live in a plain Python dict."""

import operator as _op

from sqlalchemy.sql import elements, operators

import app.db.database as _db

_shared_store = {}
_id_counters = {}


def _eval_expr(obj, expr):
    """Evaluate a SQLAlchemy column expression against an ORM instance."""
    if isinstance(expr, elements.BooleanClauseList):
        if expr.operator is _op.or_:
            return any(_eval_expr(obj, c) for c in expr.clauses)
        return all(_eval_expr(obj, c) for c in expr.clauses)

    if isinstance(expr, elements.BinaryExpression):
        col_name = expr.left.key if hasattr(expr.left, "key") else str(expr.left)
        val = getattr(obj, col_name, None)

        if expr.operator is operators.in_op:
            return val in expr.right.value
        if expr.operator is operators.not_in_op:
            return val not in expr.right.value

        if isinstance(expr.right, elements.Null):
            right_val = None
        elif hasattr(expr.right, "value"):
            right_val = expr.right.value
        else:
            right_val = expr.right

        op_func = {
            _op.eq: _op.eq,
            _op.ne: _op.ne,
            _op.lt: _op.lt,
            _op.le: _op.le,
            _op.gt: _op.gt,
            _op.ge: _op.ge,
            operators.is_: _op.is_,
            operators.is_not: _op.is_not,
        }.get(expr.operator)
        if op_func:
            return op_func(val, right_val)

    return True


class _FakeQuery:
    def __init__(self, model, store):
        self._model = model
        self._store = store
        self._filters = {}

    def _matching(self):
        table = self._model.__tablename__
        objs = [v for (t, _), v in self._store.items() if t == table]
        for attr, val in self._filters.items():
            objs = [o for o in objs if getattr(o, attr, None) == val]
        for expr in getattr(self, "_exprs", []):
            objs = [o for o in objs if _eval_expr(o, expr)]
        return objs

    def filter_by(self, **kw):
        q = _FakeQuery(self._model, self._store)
        q._filters = {**self._filters, **kw}
        return q

    def filter(self, *args):
        q = _FakeQuery(self._model, self._store)
        q._filters = dict(self._filters)
        q._exprs = list(getattr(self, "_exprs", []))
        q._exprs.extend(args)
        return q

    def all(self):
        return [_resolve(o, self._store) for o in self._matching()]

    def first(self):
        m = self._matching()
        return _resolve(m[0], self._store) if m else None

    def count(self):
        return len(self._matching())

    def delete(self):
        for obj in self._matching():
            pk = getattr(obj, "id", None) or getattr(obj, "key", None)
            self._store.pop((self._model.__tablename__, pk), None)

    def order_by(self, *a):
        return self


def _resolve(obj, store):
    """Set relationship attributes from FK values in the store."""
    try:
        mapper = obj.__class__.__mapper__
    except Exception:
        return obj
    for rel in mapper.relationships:
        related_class = rel.mapper.class_
        related_table = related_class.__tablename__
        if rel.direction.name == "MANYTOONE":
            for local_col, remote_col in rel.local_remote_pairs:
                fk = getattr(obj, local_col.key, None)
                if fk is not None:
                    related = store.get((related_table, fk))
                    if related is not None:
                        object.__setattr__(obj, rel.key, related)
        elif rel.direction.name == "ONETOMANY":
            pk = getattr(obj, "id", None)
            if pk is not None:
                fk_col = list(rel.remote_side)[0]
                matches = [
                    v
                    for (t, _), v in store.items()
                    if t == related_table and getattr(v, fk_col.key, None) == pk
                ]
                object.__setattr__(obj, rel.key, matches)
    return obj


class FakeSession:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def _pk(self, obj):
        return getattr(obj, "id", None) or getattr(obj, "key", None)

    def _assign_id(self, obj):
        if hasattr(obj, "id") and obj.id is None:
            tbl = obj.__class__.__tablename__
            _id_counters.setdefault(tbl, 0)
            _id_counters[tbl] += 1
            obj.id = _id_counters[tbl]

    def add(self, obj):
        self._assign_id(obj)
        self._store[(obj.__class__.__tablename__, self._pk(obj))] = obj

    def flush(self):
        for obj in list(self._store.values()):
            self._assign_id(obj)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def refresh(self, obj):
        _resolve(obj, self._store)

    def expunge(self, obj):
        pass

    def get(self, model, pk):
        obj = self._store.get((model.__tablename__, pk))
        return _resolve(obj, self._store) if obj else None

    def delete(self, obj):
        self._store.pop((obj.__class__.__tablename__, self._pk(obj)), None)

    def execute(self, stmt):
        from unittest.mock import MagicMock

        return MagicMock()

    def query(self, model):
        return _FakeQuery(model, self._store)


def _session_factory():
    return FakeSession(_shared_store)


def pytest_configure(config):
    marker_expr = config.getoption("-m", default="")
    if "integration" in marker_expr and "not integration" not in marker_expr:
        return
    _db.SessionLocal = _session_factory
    config._using_fake_session = True


def pytest_runtest_teardown(item, nextitem):
    if getattr(item.config, "_using_fake_session", False):
        _shared_store.clear()
        _id_counters.clear()
