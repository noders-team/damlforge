# Copyright 2026 Noders Team
# Author: Artem Bogomaz <artembogomaz@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest

from forge.codegen.emit import generate_daml, render_body, render_expr, render_stmt, render_type
from forge.codegen.loops import LoopAnalysis
from forge.daml_versions import DamlCodegenSettings
from forge.exceptions import CompilerPanic
from forge.ir.nodes import (
    Field,
    IRAssert,
    IRAssign,
    IRAttribute,
    IRBinOp,
    IRBoolOp,
    IRCall,
    IRChoice,
    IRCompare,
    IRConstant,
    IREnum,
    IRExprStmt,
    IRFor,
    IRIf,
    IRIfExp,
    IRImport,
    IRInterface,
    IRInterfaceInstance,
    IRList,
    IRModule,
    IRName,
    IRPass,
    IRReturn,
    IRStruct,
    IRSubscript,
    IRTemplate,
    IRTuple,
    IRUnaryOp,
    TypeRef,
)
from forge.models.decorators import Consuming


@pytest.mark.parametrize(
    "type_ref,expected",
    [
        (TypeRef("Party"), "Party"),
        (TypeRef("Text"), "Text"),
        (TypeRef("int"), "Int"),
        (TypeRef("bool"), "Bool"),
        (TypeRef("None"), "()"),
        (TypeRef("list", (TypeRef("Party"),)), "[Party]"),
        (TypeRef("Optional", (TypeRef("Party"),)), "Optional Party"),
        (TypeRef("ContractId", (TypeRef("Asset"),)), "ContractId Asset"),
        (
            TypeRef("Optional", (TypeRef("ContractId", (TypeRef("Asset"),)),)),
            "Optional (ContractId Asset)",
        ),
        (TypeRef("tuple", (TypeRef("Party"), TypeRef("Text"))), "(Party, Text)"),
        (TypeRef("TextMap", (TypeRef("Text"),)), "TextMap Text"),
        (TypeRef("GenMap", (TypeRef("Text"), TypeRef("int"))), "Map.Map Text Int"),
        (TypeRef("Asset"), "Asset"),
    ],
)
def test_render_type(type_ref, expected):
    assert render_type(type_ref) == expected


@pytest.mark.parametrize(
    "expr,expected",
    [
        (IRName("owner"), "owner"),
        (IRAttribute(IRName("holding"), "amount"), "holding.amount"),
        (IRConstant(True), "True"),
        (IRConstant(None), "None"),
        (IRConstant("hi"), '"hi"'),
        (IRConstant(5), "5"),
        (IRCompare(IRName("a"), "!=", IRName("b")), "a /= b"),
        (IRCompare(IRName("a"), "==", IRName("b")), "a == b"),
        (IRBoolOp("and", (IRName("a"), IRName("b"))), "a && b"),
        (IRBoolOp("or", (IRName("a"), IRName("b"))), "a || b"),
        (IRUnaryOp("not", IRName("a")), "not a"),
        (IRBinOp("+", IRName("a"), IRName("b")), "a + b"),
        (
            IRCall(IRName("_damlforge_floorDiv"), (IRName("a"), IRConstant(-2))),
            "_damlforge_floorDiv a (-2)",
        ),
        (IRBinOp("+", IRConstant("a"), IRName("b")), '"a" <> b'),
        (IRBinOp("+", IRName("a"), IRConstant("b")), 'a <> "b"'),
        (IRBinOp("+", IRList((IRName("a"),)), IRName("b")), "[a] ++ b"),
        (IRBinOp("+", IRName("a"), IRList((IRName("b"),))), "a ++ [b]"),
        (IRSubscript(IRName("entry"), IRConstant(0)), "entry._1"),
        (IRSubscript(IRName("entry"), IRConstant(1)), "entry._2"),
        (IRCall(IRName("create"), (IRName("x"),)), "create x"),
        (
            IRCall(IRName("Asset"), (), (("owner", IRName("o")), ("amount", IRName("a")))),
            "Asset with owner = o, amount = a",
        ),
        (IRTuple((IRName("a"), IRName("b"))), "(a, b)"),
        (IRCall(IRName("Decimal"), (IRConstant(0),)), "(0.0 : Decimal)"),
        (IRCall(IRName("Decimal"), (IRConstant(5),)), "(5.0 : Decimal)"),
        (IRIfExp(IRName("c"), IRName("a"), IRName("b")), "if c then a else b"),
        (
            IRBinOp("+", IRName("x"), IRIfExp(IRName("c"), IRName("a"), IRName("b"))),
            "x + (if c then a else b)",
        ),
        (
            IRCall(IRName("foo"), (IRIfExp(IRName("c"), IRName("a"), IRName("b")),)),
            "foo (if c then a else b)",
        ),
        (
            IRIfExp(IRIfExp(IRName("c1"), IRName("c2"), IRName("c3")), IRName("a"), IRName("b")),
            "if (if c1 then c2 else c3) then a else b",
        ),
        (
            IRIfExp(IRName("c1"), IRIfExp(IRName("c2"), IRName("a"), IRName("b")), IRName("d")),
            "if c1 then (if c2 then a else b) else d",
        ),
    ],
)
def test_render_expr(expr, expected):
    assert render_expr(expr) == expected


def test_render_expr_nested_generic_type_parenthesized():
    t = TypeRef("list", (TypeRef("tuple", (TypeRef("Party"), TypeRef("int"))),))
    assert render_type(t) == "[(Party, Int)]"


@pytest.mark.parametrize(
    "expr, expected",
    [
        (IRBinOp("**", IRName("x"), IRName("y")), "x ^ y"),
        (IRUnaryOp("-", IRBinOp("**", IRName("x"), IRConstant(2))), "-(x ^ 2)"),
        (IRBinOp("*", IRBinOp("**", IRName("x"), IRName("y")), IRConstant(3)), "(x ^ y) * 3"),
    ],
)
def test_render_power_uses_caret(expr, expected):
    assert render_expr(expr) == expected


@pytest.mark.parametrize(
    "expr, expected",
    [
        (IRBinOp("%", IRConstant(-7), IRConstant(-2)), "(-7) % (-2)"),
        (IRBinOp("*", IRName("x"), IRConstant(-1.5)), "x * (-1.5)"),
        (IRCompare(IRConstant(-1), "not in", IRList((IRConstant(-1),))), "(-1) `notElem` [-1]"),
        (IRBinOp("**", IRConstant(-1), IRConstant(2)), "(-1) ^ 2"),
        (IRBinOp("+", IRName("x"), IRConstant(1)), "x + 1"),
    ],
)
def test_render_negative_constant_infix_operand_is_parenthesized(expr, expected):
    assert render_expr(expr) == expected


@pytest.mark.parametrize(
    "expr, expected",
    [
        (IRUnaryOp("-", IRAttribute(IRName("r"), "f")), "-(r.f)"),
        (
            IRUnaryOp("-", IRAttribute(IRCall(IRName("Rec"), (), (("f", IRConstant(-5)),)), "f")),
            "-((Rec with f = -5).f)",
        ),
        (IRUnaryOp("-", IRConstant(-5)), "-(-5)"),
        (IRUnaryOp("-", IRName("x")), "-x"),
    ],
)
def test_render_unary_operand_is_parenthesized(expr, expected):
    assert render_expr(expr) == expected


@pytest.mark.parametrize(
    "arg, expected",
    [
        (IRSubscript(IRName("xs"), IRName("i"), is_list=True), "length ((xs) !! (i))"),
        (IRSubscript(IRName("xs"), IRConstant(0), is_list=True), "length ((xs) !! (0))"),
        (IRSubscript(IRName("t"), IRConstant(0)), "length (t._1)"),
    ],
)
def test_render_subscript_call_arg_is_parenthesized(arg, expected):
    assert render_expr(IRCall(IRName("len"), (arg,))) == expected


def test_render_qualified_import_with_alias():
    source, _ = generate_daml(IRModule(imports=(IRImport("DA.Text", alias="Text"),)))

    assert "import qualified DA.Text as Text\n" in source


@pytest.mark.parametrize(
    "names,expected",
    [
        (("!!",), "import DA.List ((!!))\n"),
        (("sort", "!!"), "import DA.List (sort, (!!))\n"),
        (("sortOn", "_helper"), "import DA.List (sortOn, _helper)\n"),
    ],
)
def test_render_import_wraps_operators(names, expected):
    source, _ = generate_daml(IRModule(imports=(IRImport("DA.List", names),)))

    assert expected in source


@pytest.mark.parametrize(
    "stmt,expected_lines",
    [
        (IRAssert(IRName("cond"), None), ["assert (cond)", "pure ()"]),
        (IRAssert(IRName("cond"), IRConstant("bad")), ['assertMsg "bad" (cond)', "pure ()"]),
        (IRAssign(IRName("x"), IRName("y")), ["let x = y", "pure ()"]),
        (
            IRAssign(IRName("p_INT_1"), IRBinOp("*", IRName("p_INT_1"), IRConstant(-2147483648))),
            ["let p_INT_1 = p_INT_1 * (-2147483648)", "pure ()"],
        ),
        (IRReturn(IRName("x")), ["pure (x)"]),
        (IRReturn(None), ["pure ()"]),
        (IRPass(), ["pure ()"]),
    ],
)
def test_render_stmt_simple(stmt, expected_lines):
    # a lone non-terminal statement still needs "what happens next" - here, the
    # implicit fall-off-the-end `pure ()` a real Daml `do`-block requires as its
    # final expression. `return` and `pass` (last two cases) are both terminal -
    # `error "..."` unifies with any type only as a do-block's OWN final
    # statement, so `pass` must stop the sequence exactly like `return` does,
    # never followed by a continuation that would make ITS type the real one.
    assert render_stmt(stmt, indent=0) == expected_lines


@pytest.mark.parametrize(
    "stmts,expected_lines",
    [
        (
            [IRAssert(IRName("cond"), IRConstant("neg")), IRReturn(IRName("x"))],
            ["if cond", "  then", "    x", '  else error "neg"'],
        ),
        (
            [IRAssert(IRName("cond"), None), IRReturn(IRName("x"))],
            ["if cond", "  then", "    x", '  else error "Assertion failed"'],
        ),
        (
            [
                IRFor("i", TypeRef("Int"), IRName("xs"), (IRAssert(IRName("i"), IRConstant("e")),)),
                IRReturn(IRName("x")),
            ],
            ["if all (\\i -> i) xs", "  then", "    x", '  else error "e"'],
        ),
    ],
)
def test_render_body_pure_assert_guards_rest(stmts, expected_lines):
    assert render_body(stmts, indent=0, in_update_block=False) == expected_lines


def test_render_stmt_if_else():
    stmt = IRIf(IRName("cond"), (IRReturn(IRConstant(True)),), (IRReturn(IRConstant(False)),))
    lines = render_stmt(stmt, indent=0)
    assert lines == ["if cond", "  then do", "    pure (True)", "  else do", "    pure (False)"]


def test_render_stmt_for_with_no_accumulators_and_no_effects_is_dropped():
    # a pure loop with no accumulator, no `return`, and no ledger effect has
    # no observable outcome at all - real damlc has no use for it, so it's
    # elided entirely rather than hoisting a dead top-level function.
    stmt = IRFor("item", TypeRef("Party"), IRName("xs"), (IRPass(),))
    hoisted = []
    lines = render_stmt(stmt, indent=0)
    joined = "\n".join(lines)
    assert "_damlforge_loop" not in joined
    assert hoisted == []


def test_render_stmt_for_with_single_assert_uses_all():
    # unlike a `pass`-only loop, a loop whose only content is `assert` has a
    # real, observable effect (it can abort the transaction) - it must never
    # be silently elided just because it has no accumulator.
    stmt = IRFor(
        "item",
        TypeRef("Party"),
        IRName("xs"),
        (IRAssert(IRConstant(True), IRConstant("must hold")),),
    )
    hoisted = []
    lines = render_body([stmt], indent=0, hoisted=hoisted)
    joined = "\n".join(lines)
    assert 'assertMsg "must hold" (all (\\item -> True) xs)' in joined
    assert "foldlA" not in joined
    assert hoisted == []


def test_early_return_loop_binds_effect_before_wrapping_return_in_some():
    stmt = IRFor(
        "k",
        TypeRef("int"),
        IRCall(IRName("range"), (IRConstant(0), IRConstant(2))),
        (IRReturn(IRCall(IRName("create"), (IRName("p"),))),),
    )
    hoisted = []
    lines = render_body(
        [stmt],
        indent=0,
        in_update_block=True,
        known={"p"},
        hoisted=hoisted,
        known_effect_fns=frozenset({"create"}),
        return_type=TypeRef("ContractId", (TypeRef("Payout"),)),
    )

    helper = "\n".join(hoisted)
    assert "<- create p" in helper
    assert "Some _damlforge_effect" in helper
    assert "Some (create p)" not in helper
    assert 'pure (error "Function did not return a value")' in "\n".join(lines)


def test_assertion_binds_nested_effect_before_accessing_result():
    stmt = IRAssert(
        IRCompare(
            IRAttribute(IRCall(IRName("fetch"), (IRName("cid"),)), "issuer"), "==", IRName("party")
        ),
        IRConstant("issuer"),
    )
    hoisted = []

    lines = render_body(
        [stmt],
        indent=0,
        in_update_block=True,
        known={"cid", "party"},
        hoisted=hoisted,
        known_effect_fns=frozenset({"fetch"}),
    )

    assert "_damlforge_effect1 <- fetch cid" in "\n".join(lines)
    assert 'assertMsg "issuer" (_damlforge_effect1.issuer == party)' in "\n".join(lines)
    assert "fetch cid" not in "\n".join(lines[1:])


def test_render_stmt_for_with_multiple_asserts_uses_action_fold():
    stmt = IRFor(
        "item",
        TypeRef("Party"),
        IRName("xs"),
        (IRAssert(IRConstant(True)), IRAssert(IRConstant(False))),
    )
    joined = "\n".join(render_body([stmt], indent=0))

    assert "foldlA" in joined


def test_render_body_for_with_accumulator_uses_fold_and_fresh_binding():
    # `total` must already be bound (here, as if it were a choice/function
    # parameter) for a plain reassignment inside the loop to count as a real
    # loop-carried accumulator rather than a fresh per-iteration local.
    loop = IRFor(
        "item",
        TypeRef("Party"),
        IRName("xs"),
        (IRExprStmt(IRCall(IRAttribute(IRName("total"), "append"), (IRName("item"),))),),
    )
    hoisted = []
    lines = render_body(
        [loop, IRReturn(IRName("total"))], indent=0, known={"total"}, hoisted=hoisted
    )
    joined = "\n".join(lines)

    assert "foldl" in joined
    assert "\\total item ->" in joined
    assert hoisted == []

    # the post-loop rebind must mint a fresh SSA name for `total` rather than
    # reusing the original - real damlc rejects any local `let X = f(X)` as
    # illegal recursion, even when `X` was bound by a function parameter
    assert "let _damlforge_total2 =" in joined
    assert "pure (_damlforge_total2)" in joined


def test_if_without_branch_carried_values_does_not_create_continuation():
    stmt = IRIf(IRName("cond"), (IRAssert(IRConstant(True)),), (IRAssert(IRConstant(False)),))
    lines = render_body([stmt, IRReturn(IRConstant(True))], indent=0)
    joined = "\n".join(lines)

    assert "_damlforge_cont" not in joined
    assert joined.endswith("pure (True)")


def test_generate_daml_struct():
    module = IRModule(structs=(IRStruct("Point", (Field("x", TypeRef("Party")),)),))
    source, _ = generate_daml(module)
    assert "data Point = Point" in source
    assert "x : Party" in source
    assert "deriving (Eq, Show)" in source
    assert "Serializable" not in source


def test_generate_daml_struct_derives_serializable_for_daml_35_explicit_mode():
    module = IRModule(structs=(IRStruct("Point", (Field("x", TypeRef("Party")),)),))
    settings = DamlCodegenSettings("3.5.0", ("--explicit-serializable=yes",))
    source, _ = generate_daml(module, settings=settings)
    assert "deriving (Eq, Show, Serializable)" in source


def test_generate_daml_struct_with_no_derive_field_type_skips_deriving():
    module = IRModule(
        structs=(
            IRStruct(
                "Wrapper",
                (
                    Field(
                        "value",
                        TypeRef(
                            "list", (TypeRef("FAR.FeaturedAppRight_CreateActivityMarkerResult"),)
                        ),
                    ),
                ),
            ),
        )
    )
    source, _ = generate_daml(module)
    assert "data Wrapper = Wrapper" in source
    assert "value : [FAR.FeaturedAppRight_CreateActivityMarkerResult]" in source
    assert "deriving (Eq, Show)" not in source


def test_generate_daml_enum():
    module = IRModule(enums=(IREnum("Status", ("PENDING", "DONE")),))
    source, _ = generate_daml(module)
    assert "data Status = PENDING | DONE" in source


def test_generate_daml_template_with_choice_and_source_map():
    choice = IRChoice(
        name="transfer",
        controller=IRName("owner"),
        consuming=Consuming.CONSUMING,
        observer=None,
        args=(Field("new_owner", TypeRef("Party")),),
        return_type=TypeRef("ContractId", (TypeRef("Asset"),)),
        body=(IRReturn(IRName("new_owner")),),
        src=None,
    )
    template = IRTemplate(
        name="Asset",
        fields=(Field("owner", TypeRef("Party")),),
        signatory=IRName("owner"),
        observer=None,
        ensure=None,
        init=None,
        choices=(choice,),
    )
    module = IRModule(templates=(template,))
    source, source_map = generate_daml(module)

    assert "template Asset" in source
    assert "signatory owner" in source
    assert "choice Transfer : ContractId Asset" in source
    assert "controller owner" in source
    assert "do" in source
    assert "pure (new_owner)" in source
    # no src was set on this hand-built fixture, so no mapping entries expected
    assert source_map["mappings"] == []


def test_generate_daml_choice_observer_precedes_controller():
    choice = IRChoice(
        name="transfer",
        controller=IRName("owner"),
        consuming=Consuming.CONSUMING,
        observer=IRName("issuer"),
        args=(Field("new_owner", TypeRef("Party")),),
        return_type=TypeRef("ContractId", (TypeRef("Asset"),)),
        body=(IRReturn(IRName("new_owner")),),
        src=None,
    )
    template = IRTemplate(
        name="Asset",
        fields=(Field("owner", TypeRef("Party")),),
        signatory=IRName("owner"),
        observer=None,
        ensure=None,
        init=None,
        choices=(choice,),
    )
    module = IRModule(templates=(template,))
    source, _ = generate_daml(module)

    observer_index = source.index("observer issuer")
    controller_index = source.index("controller owner")
    assert observer_index < controller_index


def test_generate_daml_interface_with_requires():
    choice = IRChoice(
        name="get_amount",
        controller=IRName("actors"),
        consuming=Consuming.NON_CONSUMING,
        observer=None,
        args=(),
        return_type=TypeRef("Decimal"),
        body=(IRPass(),),
    )
    interface = IRInterface(
        name="Holding",
        view=TypeRef("HoldingView"),
        requires=("Base",),
        keywords=(),
        choices=(choice,),
    )
    module = IRModule(interfaces=(interface,))
    source, _ = generate_daml(module)

    assert "interface Holding requires Base where" in source
    assert "viewtype HoldingView" in source
    assert "nonconsuming choice Get_amount : Decimal" in source


@pytest.mark.parametrize(
    "choice_name,impl_name",
    [
        ("Holding_Transfer", "holding_TransferImpl"),
        ("transfer", "transferImpl"),
        ("Accept", "acceptImpl"),
    ],
)
def test_interface_instance_uses_interface_impl_name(choice_name, impl_name):
    choice = IRChoice(
        name=choice_name,
        controller=IRName("owner"),
        consuming=Consuming.CONSUMING,
        observer=None,
        args=(),
        return_type=None,
        body=(IRPass(),),
    )
    interface = IRInterface("Holding", TypeRef("HoldingView"), (), (), (choice,))
    instance = IRInterfaceInstance("Holding", None, (choice,))
    fields = (Field("owner", TypeRef("Party")),)
    template = IRTemplate("Cash", fields, IRName("owner"), None, None, None, (), (instance,))
    source, _ = generate_daml(IRModule(interfaces=(interface,), templates=(template,)))

    assert source.count(f"{impl_name} ") == 3
    assert f"{choice_name}Impl" not in source.replace(impl_name, "")


@pytest.mark.parametrize(
    "stmts, expected",
    [
        ([IRAssign(IRName("x"), IRConstant(1)), IRReturn(IRName("x"))], set()),
        ([IRReturn(IRName("x"))], {"x"}),
        ([IRAssign(IRName("x"), IRName("x")), IRReturn(IRName("x"))], {"x"}),
        (
            [IRIf(IRName("c"), (IRAssign(IRName("y"), IRConstant(1)),)), IRReturn(IRName("y"))],
            {"c", "y"},
        ),
        (
            [
                IRAssign(IRName("y"), IRConstant(1)),
                IRIf(IRName("c"), (IRReturn(IRName("y")),), (IRReturn(IRName("z")),)),
            ],
            {"c", "z"},
        ),
        ([IRAssign(IRAttribute(IRName("r"), "f"), IRName("v"))], {"r", "v"}),
        (
            [
                IRAssign(IRName("m"), IRConstant(0)),
                IRAssign(IRSubscript(IRName("m"), IRName("k")), IRConstant(1)),
            ],
            {"k"},
        ),
    ],
)
def test_collect_free_names_skips_names_bound_earlier(stmts, expected):
    assert LoopAnalysis.collect_free_names(stmts) == expected


@pytest.mark.parametrize(
    "stmt, panics", [(IRPass(), False), (IRName("x"), True), (IRConstant(1), True)]
)
def test_collect_free_names_rejects_non_statements(stmt, panics):
    if panics:
        with pytest.raises(CompilerPanic):
            LoopAnalysis.collect_free_names([stmt])
    else:
        assert LoopAnalysis.collect_free_names([stmt]) == set()
