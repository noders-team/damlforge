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

from typing import Optional, TypeVar

from forge.models.types import ContractId, Party, Text

from . import DamlName

_T1 = TypeVar("_T1")
_T2 = TypeVar("_T2")
_T3 = TypeVar("_T3")

# TODO: Derive template type-application parameters from builtin signatures instead of this list.
TEMPLATE_ARGUMENT_FUNCTIONS = frozenset({"exercise_by_key", "visible_by_key"})


@DamlName("fetch", is_ledger_effect=True)
def fetch(cid: ContractId[_T1]) -> _T1:
    raise NotImplementedError(
        "fetch is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("exercise", is_ledger_effect=True)
def exercise(cid: ContractId[_T1], choice_args: _T2):
    raise NotImplementedError(
        "exercise is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("create", is_ledger_effect=True)
def create(payload: _T1) -> ContractId[_T1]:
    raise NotImplementedError(
        "create is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("archive", is_ledger_effect=True)
def archive(cid: ContractId[_T1]) -> None:
    raise NotImplementedError(
        "archive is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("lookupByKey", is_ledger_effect=True)
def lookup_by_key(key: _T2) -> Optional[ContractId[_T1]]:
    raise NotImplementedError(
        "lookup_by_key is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("fetchByKey", is_ledger_effect=True)
def fetch_by_key(key: _T2) -> tuple[ContractId[_T1], _T1]:
    raise NotImplementedError(
        "fetch_by_key is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("exerciseByKey", is_ledger_effect=True)
def exercise_by_key(template: type[_T1], key: _T2, choice_args: _T3):
    raise NotImplementedError(
        "exercise_by_key is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("visibleByKey", is_ledger_effect=True)
def visible_by_key(template: type[_T1], key: _T2) -> bool:
    raise NotImplementedError(
        "visible_by_key is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("lookupNByKey", module="DA.ContractKeys", is_ledger_effect=True)
def lookup_n_by_key(limit: int, key: _T2) -> list[tuple[ContractId[_T1], _T1]]:
    raise NotImplementedError(
        "lookup_n_by_key is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("lookupAllByKey", module="DA.ContractKeys", is_ledger_effect=True)
def lookup_all_by_key(key: _T2) -> list[tuple[ContractId[_T1], _T1]]:
    raise NotImplementedError(
        "lookup_all_by_key is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("fromInterfaceContractId")
def from_interface_contract_id(interface_cid: ContractId[_T1]) -> ContractId[_T2]:
    raise NotImplementedError(
        "from_interface_contract_id is a real Daml-language builtin - only "
        "meaningful inside compiled .vy source, not callable directly from Python"
    )


@DamlName("toInterfaceContractId")
def to_interface_contract_id(template_cid: ContractId[_T1]) -> ContractId[_T2]:
    raise NotImplementedError(
        "to_interface_contract_id is a real Daml-language builtin - only "
        "meaningful inside compiled .vy source, not callable directly from Python"
    )


@DamlName("toInterface")
def to_interface(value: _T1) -> _T2:  # type: ignore[type-var]
    raise NotImplementedError(
        "to_interface is a real Daml-language builtin - only meaningful "
        "inside compiled .vy source, not callable directly from Python"
    )


@DamlName("view")
def view(interface_value: _T1) -> _T2:  # type: ignore[type-var]
    raise NotImplementedError(
        "view is a real Daml-language builtin - only meaningful inside "
        "compiled .vy source, not callable directly from Python"
    )


@DamlName("coerceInterfaceContractId")
def coerce_interface_contract_id(interface_cid: ContractId[_T1]) -> ContractId[_T2]:
    raise NotImplementedError(
        "coerce_interface_contract_id is a real Daml-language builtin - only "
        "meaningful inside compiled .vy source, not callable directly from Python"
    )


@DamlName("stakeholder")
def stakeholder(value: _T1) -> list[Party]:
    raise NotImplementedError


@DamlName("reverse")
def reverse(values: list[_T1]) -> list[_T1]:
    raise NotImplementedError


@DamlName("error")
def error(message: Text) -> _T1:  # type: ignore[type-var]
    raise NotImplementedError
