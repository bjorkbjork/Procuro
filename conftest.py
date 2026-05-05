"""Root conftest — replaces SessionLocal with a pure in-memory fake.
No database of any kind. ORM objects live in a plain Python dict."""

import operator as _op
from collections import defaultdict

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
            if val is None and op_func not in (_op.eq, _op.ne, _op.is_, _op.is_not):
                return False
            return op_func(val, right_val)

    return True


# ---------------------------------------------------------------------------
# Expression introspection helpers for aggregate queries
# ---------------------------------------------------------------------------


def _col_key(expr):
    """Walk an expression tree to find the innermost column attribute name."""
    if hasattr(expr, "key") and hasattr(expr, "class_"):
        return expr.key
    for attr in ("element", "clauses"):
        inner = getattr(expr, attr, None)
        if inner is None:
            continue
        if hasattr(inner, "__iter__"):
            for item in inner:
                k = _col_key(item)
                if k:
                    return k
        else:
            k = _col_key(inner)
            if k:
                return k
    return None


def _find_model(expr):
    """Walk an expression tree to find the ORM model class."""
    if isinstance(expr, type) and hasattr(expr, "__tablename__"):
        return expr
    if hasattr(expr, "class_"):
        return expr.class_
    # AnnotatedColumn / Column — resolve model via table mapper
    table = getattr(expr, "table", None)
    if table is not None and hasattr(table, "name"):
        from sqlalchemy.orm import class_mapper
        from sqlalchemy.orm.exc import UnmappedClassError

        for sub in type.__subclasses__(type):
            pass
        # Walk Base subclasses to find the model for this table
        from app.db.database import Base

        for mapper in Base.registry.mappers:
            if mapper.local_table.name == table.name:
                return mapper.class_
    for attr in ("element", "clauses"):
        inner = getattr(expr, attr, None)
        if inner is None:
            continue
        if hasattr(inner, "__iter__"):
            for item in inner:
                m = _find_model(item)
                if m:
                    return m
        else:
            m = _find_model(inner)
            if m:
                return m
    return None


def _eval_agg(expr, objs):
    """Evaluate an aggregate function (count/avg/max) over a list of ORM objects."""
    # Unwrap Label
    if hasattr(expr, "element") and not hasattr(expr, "name"):
        return _eval_agg(expr.element, objs)

    name = getattr(expr, "name", "").lower()
    clauses = list(getattr(expr, "clauses", ()))

    if name == "count":
        if not clauses:
            return len(objs)
        inner = clauses[0]
        if getattr(inner, "name", "").lower() == "distinct":
            col = _col_key(inner)
            if col:
                return len(
                    {getattr(o, col) for o in objs if getattr(o, col, None) is not None}
                )
        return len(objs)

    if name == "avg":
        if clauses:
            col = _col_key(clauses[0])
            if col:
                vals = [
                    float(getattr(o, col))
                    for o in objs
                    if getattr(o, col, None) is not None
                ]
                return sum(vals) / len(vals) if vals else None
        return None

    if name == "max":
        if clauses:
            col = _col_key(clauses[0])
            if col:
                vals = [
                    getattr(o, col) for o in objs if getattr(o, col, None) is not None
                ]
                return max(vals) if vals else None
        return None

    return len(objs)


def _eval_select_item(expr, objs):
    """Evaluate one SELECT-list expression against a group of objects."""
    if hasattr(expr, "key") and hasattr(expr, "class_"):
        return getattr(objs[0], expr.key) if objs else None
    return _eval_agg(expr, objs)


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

    def limit(self, n):
        q = _FakeQuery(self._model, self._store)
        q._filters = dict(self._filters)
        q._exprs = list(getattr(self, "_exprs", []))
        q._limit = n
        return q


class _FakeAggregateQuery:
    """Handles queries with aggregate functions and column expressions."""

    def __init__(self, select_args, store):
        self._select_args = select_args
        self._store = store
        self._exprs = []
        self._group_by_cols = []
        self._limit_n = None
        self._model = None
        for arg in select_args:
            m = _find_model(arg)
            if m:
                self._model = m
                break

    def _copy(self):
        q = _FakeAggregateQuery(self._select_args, self._store)
        q._exprs = list(self._exprs)
        q._group_by_cols = list(self._group_by_cols)
        q._limit_n = self._limit_n
        q._model = self._model
        return q

    def _matching(self):
        if not self._model:
            return []
        table = self._model.__tablename__
        objs = [v for (t, _), v in self._store.items() if t == table]
        for expr in self._exprs:
            objs = [o for o in objs if _eval_expr(o, expr)]
        return objs

    def filter(self, *args):
        q = self._copy()
        q._exprs.extend(args)
        return q

    def filter_by(self, **kw):
        q = self._copy()
        for attr, val in kw.items():
            q._exprs.append(
                elements.BinaryExpression(
                    getattr(self._model, attr),
                    elements.BindParameter(None, val),
                    _op.eq,
                )
            )
        return q

    def group_by(self, *cols):
        q = self._copy()
        q._group_by_cols = list(cols)
        return q

    def order_by(self, *a):
        return self

    def limit(self, n):
        q = self._copy()
        q._limit_n = n
        return q

    def scalar(self):
        objs = self._matching()
        return _eval_agg(self._select_args[0], objs)

    def all(self):
        objs = self._matching()
        if not self._group_by_cols:
            return objs

        groups = defaultdict(list)
        for obj in objs:
            key = tuple(
                _col_key(c) and getattr(obj, _col_key(c)) for c in self._group_by_cols
            )
            groups[key].append(obj)

        result = []
        for group_objs in groups.values():
            row = tuple(
                _eval_select_item(expr, group_objs) for expr in self._select_args
            )
            result.append(row)

        if self._limit_n is not None:
            result = result[: self._limit_n]
        return result


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

    def query(self, *args):
        if (
            len(args) == 1
            and isinstance(args[0], type)
            and hasattr(args[0], "__tablename__")
        ):
            return _FakeQuery(args[0], self._store)
        return _FakeAggregateQuery(args, self._store)


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
