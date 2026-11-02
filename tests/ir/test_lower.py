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

from textwrap import indent

import pytest

from forge.ast.parse import parse_to_ast
from forge.ir import lower_module
from forge.ir.imports import _walk_ir
from forge.ir.lower import LIST_SET_HELPER
from forge.ir.nodes import (
    Field,
    IRAssert,
    IRAssign,
    IRAttribute,
    IRBinOp,
    IRBoolOp,
    IRCall,
    IRCompare,
    IRConstant,
    IRDict,
    IRExprStmt,
    IRFor,
    IRIf,
    IRIfExp,
    IRImport,
    IRList,
    IRName,
    IRPass,
    IRReturn,
    IRSubscript,
    IRTuple,
    IRUnaryOp,
    TypeRef,
)
from forge.models.decorators import Consuming
from forge.semantics import analyze_module


def _lower(source):
    module = parse_to_ast(source, module_path="test.vy", resolved_path="test.vy")
    return lower_module(module)


_DECORATOR_PREAMBLE = (
    "from forge.models import choice, template, view\n"
    "from forge.builtins import DamlName\n"
    "from forge.builtins.daml_lang import create, exercise, fetch\n"
)


def _analyze_and_lower(source):
    module = parse_to_ast(
        _DECORATOR_PREAMBLE + source, module_path="test.vy", resolved_path="test.vy"
    )
    analyze_module(module)
    return lower_module(module)


def test_struct_lowers_to_fields_with_type_refs():
    ir = _lower("struct Point:\n    x: int128\n    y: Party\n")

    (point,) = ir.structs
    assert point.name == "Point"
    assert point.fields == (Field("x", TypeRef("int128")), Field("y", TypeRef("Party")))


def test_lower_module_prepares_structured_support_imports():
    ir = _analyze_and_lower("struct Ledger:\n" "    balances: GenMap[Party, Decimal]\n")

    assert IRImport("DA.Map", alias="Map", qualified=False) in ir.imports


@pytest.mark.parametrize(
    "choice_body",
    [
        "@choice(controller=issuer, consuming=False)\n"
        "def GiveEach(self, owners: list[Party]) -> list[ContractId[Iou]]:\n"
        "    cids: list[ContractId[Iou]] = []\n"
        "    for o in owners:\n"
        "        cids.append(create(Iou(issuer=self.issuer, owner=o, amount=self.amount)))\n"
        "    return cids\n",
        "@choice(controller=owner, consuming=False)\n"
        "def Total(self, cids: list[ContractId[Iou]]) -> Decimal:\n"
        "    total = Decimal(0)\n"
        "    for c in cids:\n"
        "        total = total + fetch(c).amount\n"
        "    return total\n",
        "@choice(controller=owner, consuming=False)\n"
        "def CountLarger(self, cids: list[ContractId[Iou]]) -> int:\n"
        "    n = 0\n"
        "    for c in cids:\n"
        "        if fetch(c).amount > self.amount:\n"
        "            n = n + 1\n"
        "    return n\n",
    ],
    ids=["create-inside-append", "fetch-inside-expression", "fetch-inside-if-test"],
)
def test_foldla_import_detects_effects_hidden_before_loop_normalization(choice_body):
    ir = _analyze_and_lower(
        "from forge.models import ContractId, Decimal, Party\n"
        "\n"
        "@template\n"
        "class Iou:\n"
        "    issuer: Party\n"
        "    owner: Party\n"
        "    amount: Decimal\n"
        "\n"
        "    signatory: list[Party] = [issuer]\n"
        "    observer: list[Party] = [owner]\n\n" + indent(choice_body, "    ")
    )

    assert IRImport("DA.Action", ("foldlA",)) in ir.imports


def test_enum_lowers_to_values():
    ir = _lower(
        "from enum import Enum, auto\n\n"
        "class Status(Enum):\n"
        "    PENDING = auto()\n"
        "    DONE = auto()\n"
    )

    (status,) = ir.enums
    assert status.name == "Status"
    assert status.values == ("PENDING", "DONE")


def test_validated_enum_value_access_lowers_to_bare_name():
    ir = _analyze_and_lower(
        "from enum import Enum, auto\n\n"
        "class Status(Enum):\n"
        "    PENDING = auto()\n"
        "    DONE = auto()\n\n"
        "def f() -> bool:\n"
        "    return Status.PENDING == Status.DONE\n"
    )

    (fn,) = ir.functions
    assert fn.body[0] == IRReturn(IRCompare(IRName("PENDING"), "==", IRName("DONE")))


def test_unvalidated_attribute_access_lowers_to_ir_attribute():
    ir = _lower("def f(x: int128) -> bool:\n" "    return x.PENDING\n")

    (fn,) = ir.functions
    assert fn.body[0] == IRReturn(IRAttribute(IRName("x"), "PENDING"))


def test_list_subscript_assignment_lowers_to_immutable_update():
    ir = _analyze_and_lower(
        "def reset_first(xs: list[int]) -> list[int]:\n" "    xs[0] = 0\n" "    return xs\n"
    )

    (fn,) = ir.functions
    assignment, result = fn.body
    assert assignment == IRAssign(
        IRName("xs"), IRCall(IRName(LIST_SET_HELPER), (IRConstant(0), IRConstant(0), IRName("xs")))
    )
    assert isinstance(result, IRReturn)
    assert isinstance(result.value, IRName)
    assert result.value.id == "xs"


def test_dynamic_list_subscript_assignment_uses_the_same_update_helper():
    ir = _analyze_and_lower(
        "def set_at(xs: list[int], index: int, value: int) -> list[int]:\n"
        "    xs[index] = value\n"
        "    return xs\n"
    )

    (fn,) = ir.functions
    assignment = fn.body[0]
    assert isinstance(assignment, IRAssign)
    assert isinstance(assignment.value, IRCall)
    assert assignment.value.func == IRName(LIST_SET_HELPER)
    assert assignment.value.args[0] == IRName("index")


def test_template_list_field_assignment_lowers_to_record_field_update():
    ir = _analyze_and_lower(
        "from forge.models import Party\n"
        "@template\n"
        "class Box:\n"
        "    owner: Party\n"
        "    items: list[int]\n"
        "    signatory: Party = owner\n\n"
        "    @choice(controller=owner, consuming=True)\n"
        "    def set_first(self, value: int) -> int:\n"
        "        self.items[0] = value\n"
        "        return value\n"
    )

    choice = ir.templates[0].choices[0]
    assignment = choice.body[0]
    assert isinstance(assignment, IRAssign)
    assert isinstance(assignment.target, IRAttribute)
    assert assignment.target.attr == "items"
    assert isinstance(assignment.value, IRCall)
    assert assignment.value.func == IRName(LIST_SET_HELPER)


def test_bare_value_assigned_to_optional_field_auto_wraps_in_some():
    ir = _analyze_and_lower(
        "struct Terms:\n"
        "    label: Optional[Text]\n\n"
        "def f(label: Text) -> Terms:\n"
        "    return Terms(label=label)\n"
    )

    (fn,) = ir.functions
    assert fn.body[0] == IRReturn(
        IRCall(
            IRName("Terms"),
            (),
            (("label", IRCall(IRName("Some"), (IRName("label", TypeRef("Text")),))),),
        )
    )


def test_optional_field_read_with_non_optional_annotation_auto_unwraps():
    ir = _analyze_and_lower(
        "struct Terms:\n"
        "    fee: Decimal\n\n"
        "struct Holder:\n"
        "    terms: Optional[Terms]\n\n"
        "def f(holder: Holder) -> Decimal:\n"
        "    t: Terms = holder.terms\n"
        "    return t.fee\n"
    )

    (fn,) = ir.functions
    assign = fn.body[0]
    assert isinstance(assign, IRAssign)
    assert assign.value == IRCall(
        IRName("from_some"), (IRAttribute(IRName("holder", TypeRef("Holder")), "terms"),)
    )


def test_optional_typed_return_value_auto_unwraps_to_match_bare_return_type():
    ir = _analyze_and_lower(
        "def f(value: Optional[list[int]]) -> list[int]:\n"
        "    if value == None:\n"
        "        return []\n"
        "    return value\n"
    )

    (fn,) = ir.functions
    final_return = fn.body[-1]
    assert final_return == IRReturn(
        IRCall(
            IRName("from_some"),
            (IRName("value", TypeRef("Optional", (TypeRef("list", (TypeRef("Int"),)),))),),
        )
    )


def test_none_assigned_to_optional_field_is_not_wrapped():
    ir = _analyze_and_lower(
        "struct Terms:\n"
        "    label: Optional[Text]\n\n"
        "def f() -> Terms:\n"
        "    return Terms(label=None)\n"
    )

    (fn,) = ir.functions
    assert fn.body[0] == IRReturn(IRCall(IRName("Terms"), (), (("label", IRConstant(None)),)))


def test_manual_some_call_in_vy_source_is_rejected():
    with pytest.raises(Exception, match="DF1219"):
        _analyze_and_lower(
            "struct Terms:\n"
            "    label: Optional[Text]\n\n"
            "def f(label: Text) -> Terms:\n"
            "    return Terms(label=Some(label))\n"
        )


def test_manual_from_some_call_in_vy_source_is_rejected():
    with pytest.raises(Exception, match="DF1219"):
        _analyze_and_lower("def f(x: Optional[Decimal]) -> Decimal:\n" "    return from_some(x)\n")


def test_nested_attribute_chain_auto_unwraps():
    ir = _analyze_and_lower(
        "struct Rewards:\n"
        "    amount: Decimal\n\n"
        "struct Terms:\n"
        "    rewards: Optional[Rewards]\n\n"
        "@template\nclass Tracker:\n"
        "    owner: Party\n"
        "    terms: Terms\n"
        "    signatory: list[Party] = [owner]\n"
        "    @choice(controller=owner, consuming=True)\n"
        "    def get_rewards(self) -> Decimal:\n"
        "        rewards: Rewards = self.terms.rewards\n"
        "        return rewards.amount\n"
    )

    (tracker,) = ir.templates
    (choice,) = tracker.choices
    assign = choice.body[0]
    assert isinstance(assign, IRAssign)
    assert assign.value == IRCall(
        IRName("from_some"), (IRAttribute(IRAttribute(IRName("self"), "terms"), "rewards"),)
    )


def test_for_loop_target_type_does_not_leak_into_outer_scope():
    ir = _analyze_and_lower(
        "struct Terms:\n"
        "    label: Optional[Text]\n\n"
        "def f(label: Text, xs: list[Text]) -> Terms:\n"
        "    ys: list[Text] = []\n"
        "    for label: Text in xs:\n"
        "        ys.append(label)\n"
        "    return Terms(label=label)\n"
    )

    (fn,) = ir.functions
    final_return = fn.body[-1]
    assert final_return == IRReturn(
        IRCall(
            IRName("Terms"),
            (),
            (("label", IRCall(IRName("Some"), (IRName("label", TypeRef("Text")),))),),
        )
    )


def test_variable_with_same_type_in_both_if_branches_is_known_after_the_if():
    ir = _analyze_and_lower(
        "struct Terms:\n"
        "    label: Optional[Text]\n\n"
        "def f(cond: bool, a: Text, b: Text) -> Terms:\n"
        "    if cond:\n"
        "        label = a\n"
        "    else:\n"
        "        label = b\n"
        "    return Terms(label=label)\n"
    )

    (fn,) = ir.functions
    final_return = fn.body[-1]
    assert final_return == IRReturn(
        IRCall(
            IRName("Terms"),
            (),
            (("label", IRCall(IRName("Some"), (IRName("label", TypeRef("Text")),))),),
        )
    )


def test_list_append_of_optional_value_auto_unwraps():
    ir = _analyze_and_lower(
        "def maybe_text(x: Optional[Text]) -> Optional[Text]:\n"
        "    return x\n\n"
        "def f(x: Optional[Text]) -> list[Text]:\n"
        "    out: list[Text] = []\n"
        "    value = maybe_text(x)\n"
        "    out.append(value)\n"
        "    return out\n"
    )

    _maybe_text, fn = ir.functions
    append_stmt = fn.body[2]
    assert append_stmt == IRExprStmt(
        IRCall(
            IRAttribute(IRName("out", TypeRef("list", (TypeRef("Text"),))), "append"),
            (
                IRCall(
                    IRName("from_some"), (IRName("value", TypeRef("Optional", (TypeRef("Text"),))),)
                ),
            ),
        )
    )


def test_reassignment_of_optional_local_auto_wraps():
    ir = _analyze_and_lower(
        "def f(cond: bool, a: Text) -> Optional[Text]:\n"
        "    result: Optional[Text] = None\n"
        "    if cond:\n"
        "        result = a\n"
        "    return result\n"
    )

    (fn,) = ir.functions
    if_stmt = fn.body[1]
    assert isinstance(if_stmt, IRIf)
    assert if_stmt.body[0] == IRAssign(
        IRName("result"), IRCall(IRName("Some"), (IRName("a", TypeRef("Text")),))
    )


def test_fetch_infers_contract_payload_type_from_contract_id_argument():
    ir = _analyze_and_lower(
        "struct Terms:\n"
        "    label: Text\n\n"
        "@template\nclass Tracker:\n"
        "    owner: Party\n"
        "    terms: Optional[Terms]\n"
        "    signatory: list[Party] = [owner]\n"
        "    @choice(controller=owner, consuming=True)\n"
        "    def read_via_fetch(self, cid: ContractId['Tracker']) -> Terms:\n"
        "        data = fetch(cid)\n"
        "        terms: Terms = data.terms\n"
        "        return terms\n"
    )

    (tracker,) = ir.templates
    (choice,) = tracker.choices
    terms_assign = choice.body[1]
    assert isinstance(terms_assign, IRAssign)
    assert terms_assign.value == IRCall(
        IRName("from_some"), (IRAttribute(IRName("data", TypeRef("Tracker")), "terms"),)
    )


def test_create_infers_bare_contract_id_result_type():
    ir = _analyze_and_lower(
        "@template\nclass Tracker:\n"
        "    owner: Party\n"
        "    signatory: list[Party] = [owner]\n\n"
        "struct Holder:\n"
        "    tracker_cid: Optional[ContractId[Tracker]]\n\n"
        ""
        "def build(owner: Party) -> Holder:\n"
        "    new_cid = create(Tracker(owner=owner))\n"
        "    return Holder(tracker_cid=new_cid)\n"
    )

    (fn,) = ir.functions
    final_return = fn.body[-1]
    assert final_return == IRReturn(
        IRCall(
            IRName("Holder"),
            (),
            (
                (
                    "tracker_cid",
                    IRCall(
                        IRName("Some"),
                        (IRName("new_cid", TypeRef("ContractId", (TypeRef("Tracker"),))),),
                    ),
                ),
            ),
        )
    )


def test_ledger_effect_is_inferred_transitively_for_user_functions():
    ir = _analyze_and_lower(
        "from forge.builtins.daml_lang import create\n"
        "from forge.models import ContractId\n\n"
        "@template\n"
        "class Tracker:\n"
        "    owner: Party\n"
        "    signatory: Party = owner\n\n"
        "def inner(owner: Party) -> ContractId[Tracker]:\n"
        "    return create(Tracker(owner=owner))\n\n"
        "def outer(owner: Party) -> ContractId[Tracker]:\n"
        "    return inner(owner)\n"
    )

    assert {fn.name for fn in ir.functions if fn.effectful} == {"inner", "outer"}


def test_generic_type_ref_lowers_nested_subscripts():
    ir = _lower("struct Box:\n    items: Optional[list[Party]]\n")

    (box,) = ir.structs
    assert box.fields[0].type == TypeRef("Optional", (TypeRef("list", (TypeRef("Party"),)),))


def test_none_return_annotation_lowers_to_none_type_ref():
    ir = _lower("def f(x: int128) -> None:\n    pass\n")

    (fn,) = ir.functions
    assert fn.return_type == TypeRef("None")


def test_tuple_type_ref_lowers_multiple_args():
    ir = _lower("struct Box:\n    pair: tuple[Party, int128]\n")

    (box,) = ir.structs
    assert box.fields[0].type == TypeRef("tuple", (TypeRef("Party"), TypeRef("int128")))


def test_forward_ref_string_type_lowers_to_type_ref():
    ir = _lower(
        "@template\nclass Asset:\n"
        "    owner: Party\n"
        "    signatory: list[Party] = [owner]\n"
        "    @choice(controller=owner, consuming=True)\n"
        "    def transfer(self, new_owner: Party) -> ContractId['Asset']:\n"
        "        pass\n"
    )

    (asset,) = ir.templates
    assert asset.choices[0].return_type == TypeRef("ContractId", (TypeRef("Asset"),))


def test_contract_id_choice_attribute_call_lowers_to_choice_argument():
    ir = _analyze_and_lower(
        "@template\n"
        "class Asset:\n"
        "    owner: Party\n"
        "    signatory: Party = owner\n"
        "    @choice(controller=owner, consuming=True)\n"
        "    def transfer(self, new_owner: Party) -> ContractId[Asset]:\n"
        "        return create(Asset(owner=new_owner))\n"
        "@template\n"
        "class Holder:\n"
        "    owner: Party\n"
        "    signatory: Party = owner\n"
        "    @choice(controller=owner, consuming=True)\n"
        "    def move(self, asset_cid: ContractId[Asset], new_owner: Party) -> ContractId[Asset]:\n"
        "        return exercise(asset_cid, asset_cid.transfer(new_owner=new_owner))\n"
    )

    move_body = ir.templates[1].choices[0].body
    exercise_call = move_body[0].value
    choice_arg = exercise_call.args[1]
    assert isinstance(choice_arg, IRCall)
    assert choice_arg.func == IRName("Transfer")
    assert choice_arg.keywords[0][0] == "new_owner"


def test_template_lowers_signatory_and_fields():
    ir = _lower("@template\nclass Asset:\n    owner: Party\n    signatory: list[Party] = [owner]\n")

    (asset,) = ir.templates
    assert asset.name == "Asset"
    assert asset.fields == (Field("owner", TypeRef("Party")),)
    assert asset.signatory == IRList((IRName("owner"),))


def test_template_without_signatory_lowers_to_none():
    ir = _lower(
        "@template\nclass Asset:\n"
        "    owner: Party\n"
        "    @choice(controller=owner, consuming=True)\n"
        "    def transfer(self, new_owner: Party) -> Party:\n"
        "        pass\n"
    )

    (asset,) = ir.templates
    assert asset.signatory is None


def test_template_lowers_ensure_and_excludes_it_from_fields():
    ir = _lower(
        "@template\nclass Asset:\n"
        "    owner: Party\n"
        "    amount: Decimal\n"
        "    signatory: list[Party] = [owner]\n"
        "    ensure: bool = amount > Decimal(0)\n"
    )

    (asset,) = ir.templates
    assert asset.ensure == IRCompare(
        IRName("amount"), ">", IRCall(IRName("Decimal"), (IRConstant(0),))
    )
    assert asset.fields == (Field("owner", TypeRef("Party")), Field("amount", TypeRef("Decimal")))


def test_template_without_ensure_lowers_to_none():
    ir = _lower("@template\nclass Asset:\n    owner: Party\n    signatory: list[Party] = [owner]\n")

    (asset,) = ir.templates
    assert asset.ensure is None


def test_choice_lowers_controller_consuming_and_observer():
    ir = _lower(
        "@template\nclass Asset:\n"
        "    owner: Party\n"
        "    issuer: Party\n"
        "    signatory: list[Party] = [owner]\n"
        "    @choice(controller=owner, consuming=True, observer=issuer)\n"
        "    def transfer(self, new_owner: Party) -> Party:\n"
        "        return new_owner\n"
    )

    (asset,) = ir.templates
    (transfer,) = asset.choices
    assert transfer.name == "transfer"
    assert transfer.controller == IRName("owner")
    assert transfer.consuming is Consuming.CONSUMING
    assert transfer.observer == IRName("issuer")
    assert transfer.args == (Field("new_owner", TypeRef("Party")),)
    assert transfer.return_type == TypeRef("Party")
    assert transfer.body == (IRReturn(IRName("new_owner")),)


def test_choice_consuming_string_variant_lowers():
    ir = _lower(
        "interface Holding(view=View):\n"
        "    @choice(controller=actors, consuming='nonconsuming')\n"
        "    def accept(self, actors: list[Party]) -> bool:\n"
        "        pass\n"
    )

    (holding,) = ir.interfaces
    assert holding.choices[0].consuming is Consuming.NON_CONSUMING


def test_interface_lowers_view_and_requires():
    ir = _lower(
        "interface Base:\n    pass\n" "interface Holding(Base, view=HoldingView):\n    pass\n"
    )

    holding = ir.interfaces[1]
    assert holding.name == "Holding"
    assert holding.view == TypeRef("HoldingView")
    assert holding.requires == ("Base",)


def test_template_lowers_interface_instances_and_choices():
    ir = _lower(
        "struct HoldingView:\n    owner: Party\n\n"
        "interface Holding(view=HoldingView):\n"
        "    @choice(controller=actors, consuming=True)\n"
        "    def do_archive(self, actors: list[Party]) -> bool:\n"
        "        pass\n\n"
        "@template\nclass Asset(Holding):\n"
        "    owner: Party\n\n"
        "    signatory: list[Party] = [owner]\n\n"
        "    @view\n"
        "    def get_view(self) -> HoldingView:\n"
        "        return HoldingView(owner=self.owner)\n\n"
        "    @choice(controller=actors, consuming=True)\n"
        "    def do_archive(self, actors: list[Party]) -> bool:\n"
        "        return True\n"
    )

    (asset,) = [t for t in ir.templates if t.name == "Asset"]
    assert asset.choices == ()

    (instance,) = asset.interface_instances
    assert instance.interface == "Holding"
    assert instance.view.name == "get_view"
    assert instance.choices[0].name == "do_archive"
    assert instance.choices[0].controller is not None


def test_init_lowers_as_dedicated_function():
    ir = _lower(
        "@template\nclass Asset:\n"
        "    owner: Party\n"
        "    signatory: list[Party] = [owner]\n"
        "    def __init__(self, owner: Party):\n"
        "        assert owner != None, 'owner required'\n"
        "        self.owner = owner\n"
    )

    (asset,) = ir.templates
    assert asset.init is not None
    assert asset.init.name == "__init__"
    assert asset.init.args == (Field("owner", TypeRef("Party")),)
    assert isinstance(asset.init.body[0], IRAssert)


def test_len_import_is_collected_from_template_init():
    ir = _analyze_and_lower(
        "@template\n"
        "class Asset:\n"
        "    owner: Party\n"
        "    label: Text\n"
        "    signatory: Party = owner\n"
        "    def __init__(self, label: Text):\n"
        "        assert len(label) > 0\n"
    )

    (asset,) = ir.templates
    assert asset.init is not None
    assert asset.init.body[0].test.left.func == IRName("Text.length")
    assert IRImport("DA.Text", alias="Text") in ir.imports


def test_module_level_function_lowers_with_inferred_effect():
    ir = _lower("" "def helper(x: int128) -> int128:\n" "    return x\n")

    (fn,) = ir.functions
    assert fn.name == "helper"
    assert fn.decorators == ()
    assert fn.effectful is False
    assert fn.args == (Field("x", TypeRef("int128")),)
    assert fn.return_type == TypeRef("int128")


@pytest.mark.parametrize(
    "stmt_source,expected",
    [
        ("assert x, 'msg'", IRAssert(IRName("x"), IRConstant("msg"))),
        ("assert x", IRAssert(IRName("x"), None)),
        ("y = x", IRAssign(IRName("y"), IRName("x"))),
        ("y: int128 = x", IRAssign(IRName("y", TypeRef("int128")), IRName("x"), TypeRef("int128"))),
        ("x += y", IRAssign(IRName("x"), IRBinOp("+", IRName("x"), IRName("y")))),
        ("x -= y", IRAssign(IRName("x"), IRBinOp("-", IRName("x"), IRName("y")))),
        ("x *= y", IRAssign(IRName("x"), IRBinOp("*", IRName("x"), IRName("y")))),
        ("x /= y", IRAssign(IRName("x"), IRBinOp("/", IRName("x"), IRName("y")))),
        (
            "x //= y",
            IRAssign(
                IRName("x"), IRCall(IRName("_damlforge_floorDiv"), (IRName("x"), IRName("y")))
            ),
        ),
        ("x %= y", IRAssign(IRName("x"), IRBinOp("%", IRName("x"), IRName("y")))),
        ("x **= y", IRAssign(IRName("x"), IRBinOp("**", IRName("x"), IRName("y")))),
        ("pass", IRPass()),
        ("return x", IRReturn(IRName("x"))),
        ("return", IRReturn(None)),
    ],
)
def test_statement_lowering(stmt_source, expected):
    source = f"def f(x: int128) -> int128:\n    {stmt_source}\n"
    ir = _lower(source)
    (fn,) = ir.functions
    assert fn.body[0] == expected


def test_len_uses_text_length_only_for_text_arguments_in_every_expression_context():
    ir = _analyze_and_lower(
        "def f(text: Text, xs: list[int]) -> int:\n"
        "    value: int = len(text)\n"
        "    assert len(text) > 0\n"
        "    for item: int in xs:\n"
        "        assert len(text) > item\n"
        "    return len(xs)\n"
    )

    (fn,) = ir.functions
    text_length = IRCall(IRName("Text.length"), (IRName("text", TypeRef("Text")),))
    builtin_length = IRCall(IRName("len"), (IRName("xs", TypeRef("list", (TypeRef("Int"),))),))
    assert fn.body[0] == IRAssign(IRName("value", TypeRef("int")), text_length, TypeRef("int"))
    assert fn.body[1] == IRAssert(IRCompare(text_length, ">", IRConstant(0)))
    assert fn.body[2].body == (
        IRAssert(IRCompare(text_length, ">", IRName("item", TypeRef("Int")))),
    )
    assert fn.body[3] == IRReturn(builtin_length)
    assert IRImport("DA.Text", alias="Text") in ir.imports


def test_len_uses_map_size_for_map_arguments():
    ir = _analyze_and_lower(
        "def f(balances: GenMap[Party, int], names: TextMap[int]) -> int:\n"
        "    return len(balances) + len(names)\n"
    )

    (fn,) = ir.functions
    assert fn.body[0] == IRReturn(
        IRBinOp(
            "+",
            IRCall(
                IRName("Map.size"),
                (IRName("balances", TypeRef("GenMap", (TypeRef("Party"), TypeRef("Int")))),),
            ),
            IRCall(
                IRName("TextMap.size"), (IRName("names", TypeRef("TextMap", (TypeRef("Int"),))),)
            ),
        )
    )
    assert IRImport("DA.Map", alias="Map", qualified=False) in ir.imports
    assert IRImport("DA.TextMap", alias="TextMap", qualified=False) in ir.imports


def test_if_statement_lowers_body_and_orelse():
    ir = _lower(
        "def f(x: int128) -> int128:\n"
        "    if x > 0:\n"
        "        return x\n"
        "    else:\n"
        "        return 0\n"
    )
    (fn,) = ir.functions
    stmt = fn.body[0]
    assert isinstance(stmt, IRIf)
    assert stmt.test == IRCompare(IRName("x"), ">", IRConstant(0))
    assert stmt.body == (IRReturn(IRName("x")),)
    assert stmt.orelse == (IRReturn(IRConstant(0)),)


def test_for_statement_lowers_target_type_and_body():
    ir = _lower(
        "def f(xs: list[int128]) -> int128:\n"
        "    for item: int128 in xs:\n"
        "        return item\n"
        "    return 0\n"
    )
    (fn,) = ir.functions
    stmt = fn.body[0]
    assert isinstance(stmt, IRFor)
    assert stmt.target == "item"
    assert stmt.target_type == TypeRef("int128")


def test_for_statement_without_target_type_lowers_with_inferred_type_placeholder():
    ir = _lower(
        "def f(xs: list[int128]) -> int128:\n"
        "    for item in xs:\n"
        "        return item\n"
        "    return 0\n"
    )

    (fn,) = ir.functions
    stmt = fn.body[0]
    assert isinstance(stmt, IRFor)
    assert stmt.target == "item"
    assert stmt.target_type is None
    assert stmt.iter == IRName("xs")
    assert stmt.body == (IRReturn(IRName("item")),)


@pytest.mark.parametrize(
    "expr_source,expected",
    [
        ("x", IRName("x")),
        ("x.y", IRAttribute(IRName("x"), "y")),
        ("x[0]", IRSubscript(IRName("x"), IRConstant(0))),
        ("f(x, y=1)", IRCall(IRName("f"), (IRName("x"),), (("y", IRConstant(1)),))),
        ("True", IRConstant(True)),
        ("None", IRConstant(None)),
        ("42", IRConstant(42)),
        ("[x, y]", IRList((IRName("x"), IRName("y")))),
        ("(x, y)", IRTuple((IRName("x"), IRName("y")))),
        ("{}", IRDict((), ())),
        ("x + y", IRBinOp("+", IRName("x"), IRName("y"))),
        ("x // y", IRCall(IRName("_damlforge_floorDiv"), (IRName("x"), IRName("y")))),
        ("x and y", IRBoolOp("and", (IRName("x"), IRName("y")))),
        ("not x", IRUnaryOp("not", IRName("x"))),
        ("x == y", IRCompare(IRName("x"), "==", IRName("y"))),
        ("x != y", IRCompare(IRName("x"), "!=", IRName("y"))),
        ("x in y", IRCompare(IRName("x"), "in", IRName("y"))),
        ("x if cond else y", IRIfExp(IRName("cond"), IRName("x"), IRName("y"))),
    ],
)
def test_expression_lowering(expr_source, expected):
    source = f"def f(x: int128, y: int128) -> int128:\n    return {expr_source}\n"
    ir = _lower(source)
    (fn,) = ir.functions
    assert fn.body[0] == IRReturn(expected)


def test_conditional_expression_lowering_fuzzer_case():
    source = "def func_0() -> int:\n    return (-2147483648 if False else -2147483648)\n"
    ir = _analyze_and_lower(source)
    (fn,) = ir.functions
    assert fn.body[0] == IRReturn(
        IRIfExp(IRConstant(False), IRConstant(-2147483648), IRConstant(-2147483648))
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            "def f() -> int:\n    return len([{}])\n",
            IRImport("DA.TextMap", alias="TextMap", qualified=False),
        ),
        (
            f"def f() -> int:\n    return len([{{'a': {1}}}])\n",
            IRImport("DA.TextMap", alias="TextMap", qualified=False),
        ),
        (
            f"def f(p: Party) -> int:\n    m: GenMap[Party, int] = {{p: {1}}}\n    return 0\n",
            IRImport("DA.Map", alias="Map", qualified=False),
        ),
    ],
)
def test_map_literal_requires_its_map_module(source, expected):
    assert expected in _lower(source).imports


@pytest.mark.parametrize(
    "source, is_list",
    [
        ("def f(xs: list[list[int]]) -> int:\n    return len(xs[0])\n", True),
        ("def f() -> int:\n    return [-2147483648][0]\n", True),
        ("def f(xs: list[int], i: int) -> int:\n    return xs[i]\n", True),
        ("def f(t: tuple[int, int]) -> int:\n    return t[0]\n", False),
    ],
)
def test_subscript_uses_inferred_base_type(source, is_list):
    ir = _analyze_and_lower(source)
    (fn,) = ir.functions
    (subscript,) = [node for node in _walk_ir(fn.body) if isinstance(node, IRSubscript)]

    assert subscript.is_list is is_list
    assert (IRImport("DA.List", ("!!",)) in ir.imports) is is_list
