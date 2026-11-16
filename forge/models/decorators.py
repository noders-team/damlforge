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

from forge.builtins import (
    ChoiceMeta,
    Consuming,
    InterfaceMeta,
    checked_fetch,
    choice,
    interface,
    template,
    variant,
    variant_record,
    view,
)

__all__ = (
    "ChoiceMeta",
    "checked_fetch",
    "Consuming",
    "InterfaceMeta",
    "choice",
    "interface",
    "variant",
    "variant_record",
    "view",
    "template",
)


def exception(cls):
    raise NotImplementedError(
        "`exception` is excluded from the DamlForge PoC milestone "
        "(see Semantic constraints and differences from Daml.md); "
        "not usable yet."
    )
