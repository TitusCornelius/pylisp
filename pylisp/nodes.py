#!/usr/bin/env python3
from .insts import *
from .parser import parse

from re import compile, escape
from decimal import Decimal
from textwrap import indent, dedent
from collections import defaultdict, Iterable, ChainMap
from functools import wraps
from itertools import count
from copy import deepcopy
from logging import getLogger
from pprint import pformat
from ast import literal_eval
from enum import Enum, auto
from sys import stdin
logger = getLogger(__name__)

# NOT thread-safe!!
depth = 0
max_depth = 30
max_length = len('Comment')
stats = {'func-calls': defaultdict(int), 'ufunc-calls': defaultdict(int)}
def print_stats():
    if stats['func-calls']:
        print('Function Calls')
        print('--------------')
        width = max(len(str(x)) for x in stats['func-calls'])
        for cls, count in sorted(stats['func-calls'].items()):
            print(f'\t{cls:<{width}} = {count}')
    if stats['ufunc-calls']:
        print('User Defined Function Calls')
        print('---------------------------')
        width = max(len(str(x)) for x in stats['ufunc-calls'])
        for obj, count in sorted(stats['ufunc-calls'].items()):
            print(f'\t{obj:<{width}} = {count}')
def debug(f):
    @wraps(f)
    def inner(self, env):
        global depth
        call_indent = '  ' * depth
        template = f'%-{max_depth}s:%-{max_length}s:env = %r'
        logger.debug(template, f'{call_indent}enter', type(self).__name__, env)
        depth += 1
        stats['func-calls'][type(self).__name__] += 1
        if isinstance(self, UfuncBase):
            for key, val in env.items():
                if val is type(self):
                    stats['ufunc-calls'][key] += 1
        rv = f(self, env)
        depth -= 1
        logger.debug(template, f'{call_indent}leave', type(self).__name__, env)
        return rv
    return inner

class Node:
    def __init__(self, *children):
        self.children = children
    def __repr__(self):
        return f'{type(self).__name__}({", ".join(repr(x) for x in self.children)})'
    def __deepcopy__(self, memo=None):
        return type(self)(*deepcopy(self.children, memo))
    def replace(self, node):
        self.__class__ = node.__class__
        self.children = node.children
    def pformat(self, level=0):
        if not self.children:
            return f'{type(self).__name__}()'
        if len(self.children) == 1:
            child = self.children[0]
            child = child.pformat() if isinstance(child, Node) else repr(child)
            pfx = '\t' * level
            return indent(f'{type(self).__name__}({child})', prefix=pfx)
        sep, pfx = ',\n', '\t' * level
        children = [x.pformat(level=1) if isinstance(x, Node) else pfx + repr(x) for x in self.children]
        msg = f'{type(self).__name__}(\n{sep.join(children)}\n)'
        return indent(msg, prefix=pfx)
    def __call__(self, env):
        raise NotImplementedError()
    def __iter__(self):
        yield Missing(self)
    @classmethod
    def parse(cls, tree):
        NUM_RE = compile(r'(?:\+|-)?\d*\.?\d*')
        if not isinstance(tree, list):
            if NUM_RE.fullmatch(tree):
                return Atom(Decimal(tree))
            if tree.startswith('"') and tree.endswith('"'):
                return Atom(literal_eval(tree))
            if tree == 'nil':
                return Nil
            if tree == 'true':
                return True_
            return Var(tree)
            if tree == 'false':
                return False_
        if all(isinstance(x, list) for x in tree):
            return Suite.parse(tree)
        if tree[0] == 'set':
            return Set.parse(tree)
        if tree[0] == 'setg':
            return Setg.parse(tree)
        if tree[0] == 'setc':
            return Setc.parse(tree)
        if tree[0] == 'ret':
            return Ret.parse(tree)
        if tree[0] == 'lambda':
            return Lambda.parse(tree)
        comparisons = {'==': Eq, '<>': Ne, '<': Lt, '>': Gt, '<=': Le, '>=': Ge,}
        if tree[0] in comparisons:
            return comparisons[tree[0]].parse(tree)
        ops = {'+': {1: Pos, 2: Add},
               '-': {1: Neg, 2: Sub},
               '*': {2: Mul}, '/': {2: Div}, '%': {2: Mod}, '**': {2: Pow},
               'and': {2: And}, 'or': {2: Or}, 'not': {1: Not}, 'xor': {2: Xor}}
        if tree[0] in ops:
            return ops[tree[0]][len(tree[1:])].parse(tree)
        pyfuncs = {'print': Print, 'printf': Printf, 'printfs': Printfs, 'format': Format}
        if tree[0] in pyfuncs:
            return pyfuncs[tree[0]].parse(tree)
        if tree[0] == 'assert':
            return Assert.parse(tree)
        if tree[0] == 'list':
            return List.parse(tree)
        if tree[0] == 'cons':
            return Cons.parse(tree)
        if tree[0] == 'car':
            return Car.parse(tree)
        if tree[0] == 'cdr':
            return Cdr.parse(tree)
        if tree[0] == 'if':
            return IfElse.parse(tree)
        if tree[0] == 'while':
            return While.parse(tree)
        if tree[0] == 'parse':
            return Parse.parse(tree)
        if tree[0] == 'quoted':
            return Atom(Node.parse(tree[1]))
        if tree[0] == 'eval':
            return Eval.parse(tree)
        if tree[0] == 'read':
            return Read.parse(tree)
        if len(tree) == 1:
            if tree[0].startswith('^'):
                tree[0] = tree[0][1:]
                return TailCall.parse(tree)
            return Call.parse(tree)
        if isinstance(tree[0], str):
            if len(tree) > 1:
                if tree[0].startswith('^'):
                    tree[0] = tree[0][1:]
                    return TailCall.parse(tree)
                return Call.parse(tree)
            return Var(tree[0])
        return NotImplemented.parse(tree)

class NotImplemented(Node):
    @classmethod
    def parse(cls, tree):
        return cls(tree)

class Comment(Node):
    @debug
    def __call__(self, env):
        return Nil
    def __iter__(self):
        yield Noop()

class Suite(Node):
    @debug
    def __call__(self, env):
        rv = Nil
        for child in self.children:
            rv = child(env)
        return rv
    @classmethod
    def parse(self, tree):
        return Suite(*[Node.parse(element) for element in tree])
    def __iter__(self):
        for child in self.children:
            yield from child

class Set(Node):
    @debug
    def __call__(self, env):
        name, value = self.name(env), self.value(env)
        env[name.value] = value
        return value
    def __init__(self, name, value):
        super().__init__(name, value)
    name  = property(lambda self: self.children[0])
    value = property(lambda self: self.children[1])
    @classmethod
    def parse(cls, tree):
        _, name, value = tree
        return cls(Name(name), Node.parse(value))
    def __iter__(self):
        yield from self.value
        yield PopVar(self.name.value)
        yield PushVar(self.name.value)

class Setg(Node):
    @debug
    def __call__(self, env):
        name, value = self.name(env), self.value(env)
        if isinstance(env, ChainMap):
            env.maps[-1][name.value] = value
        else:
            env[name.value] = value
        return value
    def __init__(self, name, value):
        super().__init__(name, value)
    name  = property(lambda self: self.children[0])
    value = property(lambda self: self.children[1])
    @classmethod
    def parse(cls, tree):
        _, name, value = tree
        return cls(Name(name), Node.parse(value))
    def __iter__(self):
        yield from self.value
        yield PopGlobalVar(self.name.value)
        yield PushGlobalVar(self.name.value)

class Setc(Node):
    @debug
    def __call__(self, env):
        name, value = self.name(env), self.value(env)
        if isinstance(env, ChainMap):
            env.maps[1][name.value] = value
        else:
            env[name.value] = value
        return value
    def __init__(self, name, value):
        super().__init__(name, value)
    name  = property(lambda self: self.children[0])
    value = property(lambda self: self.children[1])
    @classmethod
    def parse(cls, tree):
        _, name, value = tree
        return cls(Name(name), Node.parse(value))
    def __iter__(self):
        yield from self.value
        yield PopClosureVar(self.name.value)
        yield PushClosureVar(self.name.value)

class Ret(Node):
    @debug
    def __call__(self, env):
        return self.value(env)
    def __init__(self, value):
        super().__init__(value)
    value = property(lambda self: self.children[0])
    @classmethod
    def parse(cls, tree):
        _, value = tree
        return cls(Node.parse(value))
    def __iter__(self):
        yield from self.value
        yield PopFunc()

class List(Node):
    @debug
    def __call__(self, env):
        values = [val(env) for val in self.values]
        values.reverse()
        rv = Cell(values[0], Nil)
        for v in values[1:]:
            rv = Cell(v, rv)
        return rv
    def __init__(self, car, *cdr):
        super().__init__(car, *cdr)
    values = property(lambda self: self.children)
    @classmethod
    def parse(cls, tree):
        _, *tree = tree
        return cls(*[Node.parse(x) for x in tree])
    @staticmethod
    def build(*vals):
        values = [v for v in vals]
        values.reverse()
        rv = values[0], None
        for v in values[1:]:
            rv = v, rv
        return rv
    def __iter__(self):
        for val in reversed(self.values):
            yield from val
        yield CallPyFunc(self.build, len(self.values))

class Params(Node):
    def __call__(self, env):
        return self
    def __init__(self, *params):
       super().__init__(*params)
    params = property(lambda self: self.children)
    value  = property(lambda self: self.params)
    @classmethod
    def parse(cls, tree):
        return cls(*tree)

class Cons(Node):
    @debug
    def __call__(self, env):
        car, cdr = self.car(env), self.cdr(env)
        return Cell(car, cdr)
    def __init__(self, car, cdr):
        super().__init__(car, cdr)
    car = property(lambda self: self.children[0])
    cdr = property(lambda self: self.children[1])
    @classmethod
    def parse(cls, tree):
        _, car, cdr = tree
        return cls(Node.parse(car), Node.parse(cdr))
    def __iter__(self):
        yield from self.cdr
        yield from self.car
        yield CallPyFunc(lambda car, cdr: (car, cdr), 2)

class Car(Node):
    @debug
    def __call__(self, env):
        return self.cons(env).car
    def __init__(self, cons):
        super().__init__(cons)
    cons = property(lambda self: self.children[0])
    @classmethod
    def parse(cls, tree):
        _, cons = tree
        return cls(Node.parse(cons))
    def __iter__(self):
        yield from self.cons
        yield CallPyFunc(lambda x: x[0], 1)

class Cdr(Node):
    @debug
    def __call__(self, env):
        return self.cons(env).cdr
    def __init__(self, cons):
        super().__init__(cons)
    cons = property(lambda self: self.children[0])
    @classmethod
    def parse(cls, tree):
        _, cons = tree
        return cls(Node.parse(cons))
    def __iter__(self):
        yield from self.cons
        yield CallPyFunc(lambda x: x[1], 1)

class Cell(Node):
    @debug
    def __call__(self, env):
        return self
    def __init__(self, car, cdr):
        super().__init__(car, cdr)
    car   = property(lambda self: self.children[0])
    cdr   = property(lambda self: self.children[1])
    value = property(lambda self: (self.car.value, self.cdr.value))

class ProgramError(Exception):
    pass

def check_assert(cond, msg):
    if not cond:
        raise ProgramError(msg)
    return Nil

class Assert(Node):
    @debug
    def __call__(self, env):
        if not self.cond(env).value:
            raise ProgramError(self.msg(env).value)
        return Nil
    def __init__(self, cond, msg):
        super().__init__(cond, msg)
    cond = property(lambda self: self.children[0])
    msg  = property(lambda self: self.children[1])
    @classmethod
    def parse(cls, tree):
        _, cond, msg = tree
        return cls(Node.parse(cond), Node.parse(msg))
    def __iter__(self):
        yield from self.msg
        yield from self.cond
        yield CallPyFunc(check_assert, 2)

def create_binop(name, op):
    # XXX
    code = dedent(f'''
    def create_op(op):
        class {name}(Node):
            @debug
            def __call__(self, env):
                left, right = self.left(env), self.right(env)
                left, right = left.value, right.value   # unwrap
                return Atom(op(left, right)) # wrap
            def __init__(self, left, right):
                super().__init__(left, right)
            left  = property(lambda self: self.children[0])
            right = property(lambda self: self.children[1])
            @classmethod
            def parse(cls, tree):
                _, left, right = tree
                return cls(Node.parse(left), Node.parse(right))
            def __iter__(self):
                yield from self.right
                yield from self.left
                yield CallPyFunc(op, 2)
        return {name}
    ''')
    ns = {}
    exec(code, globals(), ns)
    return ns['create_op'](op)

def create_unop(name, op):
    # XXX
    code = dedent(f'''
    def create_op(op):
        class {name}(Node):
            @debug
            def __call__(self, env):
                arg = self.arg(env)
                arg = arg.value   # unwrap
                return Atom(op(arg)) # wrap
            def __init__(self, arg):
                super().__init__(arg)
            arg  = property(lambda self: self.children[0])
            @classmethod
            def parse(cls, tree):
                _, arg = tree
                return cls(Node.parse(arg))
            def __iter__(self):
                yield from self.arg
                yield CallPyFunc(op, 1)
        return {name}
    ''')
    ns = {}
    exec(code, globals(), ns)
    return ns['create_op'](op)

from operator import pos, neg
Pos = create_unop('Pos', pos)
Neg = create_unop('Neg', neg)

UNOPS = Pos, Neg

from operator import eq, ne, lt, gt, le, ge
Eq = create_binop('Eq', eq)
Ne = create_binop('Ne', ne)
Lt = create_binop('Lt', lt)
Gt = create_binop('Gt', gt)
Le = create_binop('Le', le)
Ge = create_binop('Ge', ge)

from operator import add, sub, mul, truediv, mod, pow
Add = create_binop('Add', add)
Sub = create_binop('Sub', sub)
Mul = create_binop('Mul', mul)
Div = create_binop('Div', truediv)
Mod = create_binop('Mod', mod)
Pow = create_binop('Pow', pow)

from operator import and_, or_, not_, xor, is_
And = create_binop('And', and_)
Or  = create_binop('Or',  or_)
Not = create_binop('Not', not_)
Xor = create_binop('Xor', xor)
Is  = create_binop('Is',  is_)

BINOPS = Eq, Ne, Lt, Gt, Le, Ge, Add, Sub, Mul, Div, Mod, Pow, And, Or, Not, Xor, Is

def create_pyfunc(name, func):
    code = dedent(f'''
    def create_func(func):
        class {name}(Node):
            @debug
            def __call__(self, env):
                args = [arg(env) for arg in self.args]
                args = [arg.value for arg in args] # unwrap
                rv = func(*args)
                return Atom(rv) # wrap
            def __init__(self, *args):
                super().__init__(*args)
            args = property(lambda self: self.children)
            @classmethod
            def parse(cls, tree):
                _, *args = tree
                return cls(*[Node.parse(x) for x in args])
            def __iter__(self):
                for arg in reversed(self.args):
                    yield from arg
                yield CallPyFunc(func, len(self.args))
        return {name}
    ''')
    ns = {}
    exec(code, globals(), ns)
    return ns['create_func'](func)

Print   = create_pyfunc('Print',   lambda *args: print(*args, flush=True))
Format  = create_pyfunc('Format',  format)
Printf  = create_pyfunc('Printf',  lambda fmt, *args:      print(fmt.format(*args), end='', flush=True))
Printfs = create_pyfunc('Printfs', lambda fmt, sep, *args: print(fmt.format(*args), sep=sep, end='', flush=True))

class Name(Node):
    @debug
    def __call__(self, env):
        return self
    def __init__(self, name):
        super().__init__(name)
    name  = property(lambda self: self.children[0])
    value = name
    @classmethod
    def parse(cls, tree):
        name = tree[0] if isinstance(tree, list) else tree
        return Name(name)

class Var(Node):
    @debug
    def __call__(self, env):
        try:
            return env[self.name](env)
        except KeyError as e:
            raise ProgramError(f'unknown name {self.name!r}') from e
    def __init__(self, name):
        super().__init__(name)
    name = property(lambda self: self.children[0])
    def __iter__(self):
        yield PushVar(self.name)

class Atom(Node):
    @debug
    def __call__(self, env):
        return self
    def __init__(self, value):
        super().__init__(value)
    value = property(lambda self: self.children[0])
    def __iter__(self):
        yield PushImm(self.value)

class Nil(Node):
    def __call__(self, env):
        return self
    value = property(lambda self: None)
    def __iter__(self):
        yield PushImm(self.value)
Nil = Nil()

class True_(Node):
    def __call__(self, env):
        return self
    value = property(lambda self: True)
    def __iter__(self):
        yield PushImm(self.value)
True_ = True_()

class False_(Node):
    def __call__(self, env):
        return self
    value = property(lambda self: False)
    def __iter__(self):
        yield PushImm(self.value)
False_ = False_()

class While(Node):
    @debug
    def __call__(self, env):
        rv = Nil
        while self.cond(env).value:
            rv = self.body(env)
        return rv
    def __init__(self, cond, body):
        super().__init__(cond, body)
    cond = property(lambda self: self.children[0])
    body = property(lambda self: self.children[1])
    @classmethod
    def parse(cls, tree):
        _, cond, *body = tree
        if len(body) > 1:
            return cls(Node.parse(cond), Suite.parse(body))
        return cls(Node.parse(cond), Node.parse(*body))
    def __iter__(self):
        start, end = f'loop-start-{hex(id(self))}', f'loop-end-{hex(id(self))}'
        yield Label(start)
        yield from self.cond
        yield JumpIfFalse(end)
        yield from self.body
        yield JumpAlways(start)
        yield Label(end)

class IfElse(Node):
    @debug
    def __call__(self, env):
        rv = Nil
        if self.cond(env).value:
            rv = self.ifbody(env)
        else:
            if self.elsebody:
                rv = self.elsebody(env)
        return rv
    def __init__(self, cond, ifbody, elsebody=None):
        super().__init__(cond, ifbody, elsebody)
    cond     = property(lambda self: self.children[0])
    ifbody   = property(lambda self: self.children[1])
    elsebody = property(lambda self: self.children[2])
    @classmethod
    def parse(cls, tree):
        _, cond, ifbody, *elsebody = tree
        if elsebody:
            return cls(Node.parse(cond), Node.parse(ifbody), Node.parse(*elsebody))
        return cls(Node.parse(cond), Node.parse(ifbody))
    def __iter__(self):
        ifbody, elsebody, end = f'ifbody-{hex(id(self))}', f'elsebody-{hex(id(self))}', f'end-{hex(id(self))}'
        yield from self.cond
        if self.elsebody:
            yield JumpIfFalse(elsebody)
        else:
            yield JumpIfFalse(end)
        yield from self.ifbody
        if self.elsebody:
            yield JumpAlways(end)
            yield Label(elsebody)
            yield from self.elsebody
        yield Label(end)

class Call(Node):
    @debug
    def __call__(self, env):
        args = [arg(env) for arg in self.args]
        try:
            node = env[self.name.value](*args)
        except KeyError as e:
            raise ProgramError(f'unknown name {self.name.value!r}') from e
        return node(env)
    def __init__(self, name, *args):
        super().__init__(name, *args)
    name = property(lambda self: self.children[0])
    args = property(lambda self: self.children[1:])
    @classmethod
    def parse(cls, tree):
        name, *args = tree
        return cls(Name(name), *[Node.parse(x) for x in args])
    def __iter__(self):
        for arg in reversed(self.args):
            yield from arg
        yield PushFunc(self.name.value)

class TailCall(Call):
    def __iter__(self):
        for arg in reversed(self.args):
            yield from arg
        yield PushTailFunc(self.name.value)

class UfuncBase(Node):
    # XXX
    pass

class Scoping(Enum):
    DYNAMIC = auto()
    LEXICAL = auto()

__scoping__ = Scoping.LEXICAL

class Lambda(Node):
    @debug
    def __call__(self, env):
        params, body = self.params, self.body
        if isinstance(env, ChainMap):
            closures = env.maps[:-1]
        else:
            closures = []
        class Ufunc(UfuncBase):
            @debug
            def __call__(self, env):
                args = dict(zip(params.value, self.args))
                if isinstance(env, ChainMap) and __scoping__ is Scoping.LEXICAL:
                    local_env = ChainMap(args, *closures, env.maps[-1])
                else:
                    local_env = ChainMap(args, *closures, env)
                return body(local_env)
            def __init__(self, *args):
                super().__init__(*args)
            args  = property(lambda self: self.children)
            value = property(lambda self: self)
        return Ufunc
    def __init__(self, params, body):
        super().__init__(params, body)
    params = property(lambda self: self.children[0])
    body   = property(lambda self: self.children[1])
    @classmethod
    def parse(cls, tree):
        _, params, body = tree
        return cls(Params.parse(params), Node.parse(body))
    def __iter__(self):
        yield CreateFunc(self.params.value, self.body)

class Read(Node):
    @debug
    def __call__(self, env):
        if '--stdin' in env:
            return env['--stdin'](env)
        line = next(stdin)
        return Atom(line)
    def __init__(self):
        super().__init__()
    @classmethod
    def parse(cls, tree):
        return cls()
    def __iter__(self):
        yield ReadInput()

class Parse(Node):
    @debug
    def __call__(self, env):
        expr = self.expr
        code = expr(env).value
        node = parse(code)
        return Atom(node)
    def __init__(self, expr):
        super().__init__(expr)
    expr = property(lambda self: self.children[0])
    @classmethod
    def parse(cls, tree):
        _, expr = tree
        return cls(Node.parse(expr))
    def __iter__(self):
        yield from self.expr
        yield CallPyFunc(parse, 1)

class Eval(Node):
    @debug
    def __call__(self, env):
        expr = self.expr
        node = expr(env).value
        return node(env)
    def __init__(self, expr):
        super().__init__(expr)
    expr = property(lambda self: self.children[0])
    @classmethod
    def parse(cls, tree):
        _, expr = tree
        return cls(Node.parse(expr))
    def __iter__(self):
        yield from self.expr
        yield Evaluate()

__all__ = [
    'Node', 'NotImplemented', 'Comment', 'Suite', 'Set', 'Setg', 'Setc',
    'Ret', 'List', 'Params', 'Cons', 'Car', 'Cdr', 'Cell', 'ProgramError',
    'Assert', 'Pos', 'Neg', 'Eq', 'Ne', 'Lt', 'Gt', 'Le', 'Ge', 'Add', 'Sub',
    'Mul', 'Div', 'Mod', 'Pow', 'And', 'Or', 'Not', 'Xor', 'Is',
    'Print', 'Format', 'Printf', 'Printfs', 'Name',
    'Var', 'Atom', 'Nil', 'True_', 'False_', 'While', 'IfElse', 'Call',
    'TailCall', 'UfuncBase', 'Scoping', 'Lambda', 'Read', 'Parse', 'Eval',

    'BINOPS', 'UNOPS',

    'create_pyfunc',
]
