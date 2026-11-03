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

from forge.ast import nodes as vy_ast
from forge.exceptions import DamlForgeException, ExceptionList
from forge.models.error_codes import ErrorCodes
from forge.semantics.primitive_specs import (
    INT_MAX,
    INT_MIN,
    date_policy_error,
    reltime_policy_error,
    time_policy_error,
)

COMPILER_INTRINSIC_IMPORTS = {
    "datetime": frozenset({"date", "datetime", "time", "timedelta", "timezone"})
}

_TIMEDELTA_PARAMETERS = (
    "days",
    "seconds",
    "microseconds",
    "milliseconds",
    "minutes",
    "hours",
    "weeks",
)

_DATE_PARAMETERS = ("year", "month", "day")
_TIME_PARAMETERS = ("hour", "minute", "second")
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class DatetimeIntrinsicAnalyzer:
    """Resolve Python datetime constructs that lower directly to Daml."""

    def __init__(self, module: vy_ast.Module, errors: ExceptionList) -> None:
        self.module = module
        self.errors = errors
        self.date_names: set[str] = set()
        self.datetime_names: set[str] = set()
        self.time_names: set[str] = set()
        self.timedelta_names: set[str] = set()
        self.timezone_names: set[str] = set()
        self.datetime_aliases: set[str] = set()
        self._index_imports()

    def _index_imports(self) -> None:
        for stmt in self.module.body:
            if isinstance(stmt, vy_ast.ImportFrom) and stmt.module == "datetime":
                self.date_names.update(
                    alias.asname or alias.name for alias in stmt.names if alias.name == "date"
                )
                self.datetime_names.update(
                    alias.asname or alias.name for alias in stmt.names if alias.name == "datetime"
                )
                self.time_names.update(
                    alias.asname or alias.name for alias in stmt.names if alias.name == "time"
                )
                self.timedelta_names.update(
                    alias.asname or alias.name for alias in stmt.names if alias.name == "timedelta"
                )
                self.timezone_names.update(
                    alias.asname or alias.name for alias in stmt.names if alias.name == "timezone"
                )
            elif isinstance(stmt, vy_ast.Import):
                self.datetime_aliases.update(
                    alias.asname or alias.name for alias in stmt.names if alias.name == "datetime"
                )

    @staticmethod
    def _local_names(fn: vy_ast.FunctionDef | None) -> set[str]:
        if fn is None:
            return set()
        names = {arg.arg for arg in fn.args.args}
        for node in fn.get_descendants((vy_ast.Assign, vy_ast.AnnAssign, vy_ast.For)):
            target = node.target
            if isinstance(target, vy_ast.Name):
                names.add(target.id)
        return names

    def _is_timedelta(self, node, local_names: set[str]) -> bool:
        if isinstance(node, vy_ast.Name):
            return node.id in self.timedelta_names and node.id not in local_names
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "timedelta"
            and isinstance(node.value, vy_ast.Name)
            and node.value.id in self.datetime_aliases
            and node.value.id not in local_names
        )

    def _is_date(self, node, local_names: set[str]) -> bool:
        if isinstance(node, vy_ast.Name):
            return node.id in self.date_names and node.id not in local_names
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "date"
            and isinstance(node.value, vy_ast.Name)
            and node.value.id in self.datetime_aliases
            and node.value.id not in local_names
        )

    def _is_datetime(self, node, local_names: set[str]) -> bool:
        if isinstance(node, vy_ast.Name):
            return node.id in self.datetime_names and node.id not in local_names
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "datetime"
            and isinstance(node.value, vy_ast.Name)
            and node.value.id in self.datetime_aliases
            and node.value.id not in local_names
        )

    def _is_datetime_now(self, node, local_names: set[str]) -> bool:
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "now"
            and self._is_datetime(node.value, local_names)
        )

    def _is_time(self, node, local_names: set[str]) -> bool:
        if isinstance(node, vy_ast.Name):
            return node.id in self.time_names and node.id not in local_names
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "time"
            and isinstance(node.value, vy_ast.Name)
            and node.value.id in self.datetime_aliases
            and node.value.id not in local_names
        )

    def _is_datetime_combine(self, node, local_names: set[str]) -> bool:
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "combine"
            and self._is_datetime(node.value, local_names)
        )

    def _is_datetime_fromtimestamp(self, node, local_names: set[str]) -> bool:
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "fromtimestamp"
            and self._is_datetime(node.value, local_names)
        )

    def _is_timezone(self, node, local_names: set[str]) -> bool:
        if isinstance(node, vy_ast.Name):
            return node.id in self.timezone_names and node.id not in local_names
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "timezone"
            and isinstance(node.value, vy_ast.Name)
            and node.value.id in self.datetime_aliases
            and node.value.id not in local_names
        )

    def _is_utc(self, node, local_names: set[str]) -> bool:
        return (
            isinstance(node, vy_ast.Attribute)
            and node.attr == "utc"
            and self._is_timezone(node.value, local_names)
        )

    def _normalize_timedelta_arguments(self, call: vy_ast.Call):
        if len(call.args) > len(_TIMEDELTA_PARAMETERS):
            self._invalid_timedelta_call(
                call, f"takes at most {len(_TIMEDELTA_PARAMETERS)} positional arguments"
            )
            return None

        values = dict(zip(_TIMEDELTA_PARAMETERS, call.args))
        for keyword in call.keywords:
            if keyword.arg is None:
                self._invalid_timedelta_call(call, "does not support unpacked keyword arguments")
                return None
            if keyword.arg not in _TIMEDELTA_PARAMETERS:
                self._invalid_timedelta_call(
                    call,
                    f"does not support `{keyword.arg}=`; supported units are "
                    f"{', '.join(_TIMEDELTA_PARAMETERS)}",
                )
                return None
            if keyword.arg in values:
                self._invalid_timedelta_call(call, f"got multiple values for `{keyword.arg}`")
                return None
            values[keyword.arg] = keyword.value

        return tuple((name, values[name]) for name in _TIMEDELTA_PARAMETERS if name in values)

    def _invalid_timedelta_call(self, call: vy_ast.Call, reason: str) -> None:
        self.errors.append(
            DamlForgeException(
                f"`datetime.timedelta(...)` {reason}", call, code=ErrorCodes.DF1225.value
            )
        )

    def _normalize_date_arguments(self, call: vy_ast.Call):
        if len(call.args) > len(_DATE_PARAMETERS):
            self._invalid_date_call(call, "takes exactly 3 arguments")
            return None

        values = dict(zip(_DATE_PARAMETERS, call.args))
        for keyword in call.keywords:
            if keyword.arg is None or keyword.arg not in _DATE_PARAMETERS:
                name = "**" if keyword.arg is None else f"{keyword.arg}="
                self._invalid_date_call(call, f"does not support `{name}`")
                return None
            if keyword.arg in values:
                self._invalid_date_call(call, f"got multiple values for `{keyword.arg}`")
                return None
            values[keyword.arg] = keyword.value

        missing = [name for name in _DATE_PARAMETERS if name not in values]
        if missing:
            self._invalid_date_call(call, f"is missing {', '.join(missing)}")
            return None
        return tuple(values[name] for name in _DATE_PARAMETERS)

    def _invalid_date_call(
        self, call: vy_ast.Call, reason: str, code: str = ErrorCodes.DF1225.value
    ) -> None:
        self.errors.append(DamlForgeException(f"`datetime.date(...)` {reason}", call, code=code))

    def _analyze_date(self, call: vy_ast.Call, local_names: set[str]) -> bool:
        if not self._is_date(call.func, local_names):
            return False
        arguments = self._normalize_date_arguments(call)
        if arguments is None:
            return False

        if all(isinstance(value, vy_ast.Int) for value in arguments):
            year, month, day = (value.value for value in arguments)
            reason = date_policy_error(year, month, day)
            if reason is not None:
                self._invalid_date_call(call, reason, code=ErrorCodes.DF1229.value)
                return False
            call._metadata["date_month_name"] = _MONTH_NAMES[month - 1]
        call._metadata["date_arguments"] = arguments
        return True

    def _analyze_datetime_now(self, call: vy_ast.Call, local_names: set[str]) -> bool:
        if not self._is_datetime_now(call.func, local_names):
            return False
        if call.args or call.keywords:
            self.errors.append(
                DamlForgeException(
                    "`datetime.now()` does not accept arguments in DamlForge",
                    call,
                    code=ErrorCodes.DF1225.value,
                )
            )
            return False
        call._metadata.update(
            daml_builtin="getTime", return_type="Timestamp", is_ledger_effect=True
        )
        return True

    def _normalize_time_arguments(self, call: vy_ast.Call):
        if len(call.args) > len(_TIME_PARAMETERS):
            self._invalid_time_call(call, "takes at most 3 arguments")
            return None

        values = dict(zip(_TIME_PARAMETERS, call.args))
        for keyword in call.keywords:
            if keyword.arg is None or keyword.arg not in _TIME_PARAMETERS:
                name = "**" if keyword.arg is None else f"{keyword.arg}="
                self._invalid_time_call(call, f"does not support `{name}`")
                return None
            if keyword.arg in values:
                self._invalid_time_call(call, f"got multiple values for `{keyword.arg}`")
                return None
            values[keyword.arg] = keyword.value
        return tuple(values.get(name) for name in _TIME_PARAMETERS)

    def _invalid_time_call(
        self, call: vy_ast.Call, reason: str, code: str = ErrorCodes.DF1225.value
    ) -> None:
        self.errors.append(DamlForgeException(f"`datetime.time(...)` {reason}", call, code=code))

    def _analyze_datetime_combine(
        self, call: vy_ast.Call, local_names: set[str], consumed_time_calls: set[int]
    ) -> bool:
        if len(call.args) != 2 or call.keywords:
            self.errors.append(
                DamlForgeException(
                    "`datetime.combine(...)` requires a date and a time",
                    call,
                    code=ErrorCodes.DF1225.value,
                )
            )
            return False

        value, time_call = call.args
        if not (isinstance(time_call, vy_ast.Call) and self._is_time(time_call.func, local_names)):
            self.errors.append(
                DamlForgeException(
                    "`datetime.combine(...)` requires `datetime.time(...)` as its second argument",
                    call,
                    code=ErrorCodes.DF1225.value,
                )
            )
            return False

        consumed_time_calls.add(id(time_call))
        time_arguments = self._normalize_time_arguments(time_call)
        if time_arguments is None:
            return False
        if all(value is None or isinstance(value, vy_ast.Int) for value in time_arguments):
            hour, minute, second = (
                value.value if isinstance(value, vy_ast.Int) else 0 for value in time_arguments
            )
            reason = time_policy_error(hour, minute, second)
            if reason is not None:
                self._invalid_time_call(time_call, reason, code=ErrorCodes.DF1229.value)
                return False
        call._metadata["datetime_combine_arguments"] = (value, *time_arguments)
        return True

    def _analyze_datetime_fromtimestamp(self, call: vy_ast.Call, local_names: set[str]) -> bool:
        if not 1 <= len(call.args) <= 2:
            self.errors.append(
                DamlForgeException(
                    "`datetime.fromtimestamp(...)` requires a timestamp and `timezone.utc`",
                    call,
                    code=ErrorCodes.DF1225.value,
                )
            )
            return False

        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        if any(name not in ("tz",) for name in keywords) or (
            len(call.args) == 2 and "tz" in keywords
        ):
            self.errors.append(
                DamlForgeException(
                    "`datetime.fromtimestamp(...)` supports only the `tz=` keyword",
                    call,
                    code=ErrorCodes.DF1225.value,
                )
            )
            return False

        timezone = call.args[1] if len(call.args) == 2 else keywords.get("tz")
        if not self._is_utc(timezone, local_names):
            self.errors.append(
                DamlForgeException(
                    "`datetime.fromtimestamp(...)` requires `timezone.utc`",
                    call,
                    code=ErrorCodes.DF1225.value,
                )
            )
            return False

        timestamp = call.args[0]
        is_microseconds = (
            isinstance(timestamp, vy_ast.BinOp)
            and isinstance(timestamp.op, vy_ast.Div)
            and isinstance(timestamp.right, vy_ast.Int)
            and timestamp.right.value == 1_000_000
        )
        call._metadata["fromtimestamp_argument"] = timestamp.left if is_microseconds else timestamp
        call._metadata["fromtimestamp_is_microseconds"] = is_microseconds
        return True

    def analyze(self) -> None:
        if (
            not self.date_names
            and not self.datetime_names
            and not self.time_names
            and not self.timedelta_names
            and not self.timezone_names
            and not self.datetime_aliases
        ):
            return

        date_used = False
        datetime_combine_used = False
        datetime_fromtimestamp_used = False
        timedelta_used = False
        consumed_time_calls: set[int] = set()
        calls = list(self.module.get_descendants(vy_ast.Call))
        local_names: dict[int, set[str]] = {}
        for call in calls:
            fn = call.get_ancestor(vy_ast.FunctionDef)
            names = local_names.setdefault(id(fn), self._local_names(fn))
            if self._analyze_datetime_now(call, names):
                continue
            if self._is_datetime_combine(call.func, names):
                if self._analyze_datetime_combine(call, names, consumed_time_calls):
                    datetime_combine_used = True
                continue
            if self._is_datetime_fromtimestamp(call.func, names):
                if self._analyze_datetime_fromtimestamp(call, names):
                    datetime_fromtimestamp_used = True
                continue
            if self._analyze_date(call, names):
                date_used = True
                continue
            if not self._is_timedelta(call.func, names):
                continue
            arguments = self._normalize_timedelta_arguments(call)
            if arguments is None:
                continue
            if all(
                isinstance(value, vy_ast.Int) and INT_MIN <= value.value <= INT_MAX
                for _, value in arguments
            ):
                units = {
                    "weeks": 7 * 24 * 60 * 60 * 1_000_000,
                    "days": 24 * 60 * 60 * 1_000_000,
                    "hours": 60 * 60 * 1_000_000,
                    "minutes": 60 * 1_000_000,
                    "seconds": 1_000_000,
                    "milliseconds": 1_000,
                    "microseconds": 1,
                }
                total = sum(value.value * units[name] for name, value in arguments)
                reason = reltime_policy_error(total)
                if reason is not None:
                    self.errors.append(
                        DamlForgeException(
                            f"`datetime.timedelta(...)` result {reason}",
                            call,
                            code=ErrorCodes.DF1229.value,
                        )
                    )
                    continue
            call._metadata["timedelta_arguments"] = arguments
            timedelta_used = True

        for call in calls:
            fn = call.get_ancestor(vy_ast.FunctionDef)
            names = local_names.setdefault(id(fn), self._local_names(fn))
            if self._is_time(call.func, names) and id(call) not in consumed_time_calls:
                self._invalid_time_call(
                    call, "has no standalone Daml equivalent; use `datetime.combine(...)`"
                )

        for node in self.module.get_descendants((vy_ast.Name, vy_ast.Attribute)):
            if self._is_timedelta(node, set()):
                node._metadata["daml_type_name"] = "RelTime"
                timedelta_used = True
            elif self._is_date(node, set()):
                node._metadata["daml_type_name"] = "Date"
            elif self._is_datetime(node, set()):
                node._metadata["daml_type_name"] = "Timestamp"

        if date_used or datetime_combine_used or datetime_fromtimestamp_used or timedelta_used:
            required = set(self.module._metadata.get("required_daml_modules", ()))
            if date_used or datetime_fromtimestamp_used:
                required.add("DA.Date")
            if datetime_combine_used or datetime_fromtimestamp_used or timedelta_used:
                required.add("DA.Time")
            self.module._metadata["required_daml_modules"] = tuple(sorted(required))

        intrinsic_effects = {
            daml_builtin
            for call in calls
            if call._metadata.get("is_ledger_effect")
            and (daml_builtin := call._metadata.get("daml_builtin")) is not None
        }
        if intrinsic_effects:
            effects = set(self.module._metadata.get("intrinsic_effect_fns", ()))
            effects.update(intrinsic_effects)
            self.module._metadata["intrinsic_effect_fns"] = tuple(sorted(effects))
