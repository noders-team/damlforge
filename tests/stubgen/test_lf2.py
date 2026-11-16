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

import os
from pathlib import Path

import pytest

pb = pytest.importorskip("dazl._gen.com.digitalasset.daml.lf.archive.daml_lf2_pb2")
lf = pytest.importorskip("dazl.damlast.daml_lf_1")
stubgen = pytest.importorskip("forge.stubgen")
exports = pytest.importorskip("forge.stubgen.exports")
lf2 = pytest.importorskip("forge.stubgen.lf2")
resolve_dpm_path = pytest.importorskip("forge.cli.forge_dar")._resolve_dpm_path

KEYS_PROJECT = Path(__file__).parent / "fixtures" / "keys"


@pytest.fixture(scope="module")
def keys_stub(tmp_path_factory):
    try:
        resolve_dpm_path(None)
    except RuntimeError as exc:
        if os.environ.get("CI"):
            pytest.fail(str(exc))
        pytest.skip("dpm is required to build the stubgen keys fixture")
    out_dir = tmp_path_factory.mktemp("keys_stubs")
    stubgen.generate_stubs(KEYS_PROJECT, out_dir, modules=("Keys",))
    return (out_dir / "Keys.vy").read_text()


def _builtin(name: str):
    return pb.Type(builtin=pb.Type.Builtin(builtin=pb.BuiltinType.Value(name)))


@pytest.mark.parametrize(
    "method,message,check",
    [
        (
            "parse_Type",
            pb.Type(tapp=pb.Type.TApp(lhs=_builtin("LIST"), rhs=_builtin("PARTY"))),
            lambda result: len(result.prim.args) == 1,
        ),
        ("parse_Kind", pb.Kind(interned_kind=0), lambda result: result.star is not None),
        ("parse_Expr", pb.Expr(interned_expr=0), lambda result: result.prim_con is not None),
        ("parse_Update", pb.Update(), lambda result: result is None),
        (
            "parse_ModuleId",
            pb.ModuleId(package_id=pb.SelfOrImportedPackageId(package_import_id=0)),
            lambda result: str(result) == "pkg2:M",
        ),
    ],
    ids=["type-application", "interned-kind", "interned-expr", "unknown-update", "package-import"],
)
def test_parser_reads_lf_2_3_messages(method, message, check):
    parser = lf2.Parser23(lf.PackageRef("pkg"))
    parser.interned_dotted_names.append(("M",))
    parser.package_imports = ["pkg2"]
    parser.kinds = [pb.Kind(star=pb.Unit())]
    parser.exprs = [pb.Expr(builtin_con=pb.BuiltinCon.Value("CON_UNIT"))]

    assert check(getattr(parser, method)(message))


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "module DA.ContractKeys ( DA.lookupNByKey, DA.lookupAllByKey ) where",
            [("name", "lookupNByKey"), ("name", "lookupAllByKey")],
        ),
        (
            "module M (foo, type Bar, Baz(..)) where",
            [("name", "foo"), ("name", "Bar"), ("name", "Baz")],
        ),
        ("module M (module DA.Types, A.B.c) where", [("module", "DA.Types"), ("name", "c")]),
    ],
    ids=["qualified-names", "plain-names", "module-and-nested-qualifier"],
)
def test_export_items_accepts_qualified_names(source, expected):
    assert exports.export_items(source) == expected


@pytest.mark.parametrize(
    "template,expected",
    [
        (
            "Account",
            ["key: tuple[Party, str] = (bank, number)", "maintainer: list[Party] = [key[0]]"],
        ),
        ("Wallet", ["key: Party = owner", "maintainer: list[Party] = [key]"]),
        ("Deposit", ["key: AccountKey = AccountKey(bank=bank, number=number)", "[key.bank]"]),
        ("Note", []),
    ],
)
def test_stubs_render_contract_keys_from_lf_2_3_dar(keys_stub, template, expected):
    block = keys_stub.split(f"class {template}:", 1)[1].split("@template")[0]

    assert all(line in block for line in expected)
    assert ("key:" in block) is bool(expected)
