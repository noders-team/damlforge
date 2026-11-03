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

import enum
import io
import re
from collections import defaultdict
from tokenize import COMMENT, NAME, NEWLINE, OP, STRING, TokenError, TokenInfo, tokenize, untokenize

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from forge.compiler.settings import OptimizationLevel, Settings

# seems a bit early to be importing this but we want it to validate the
from forge.exceptions import PragmaException, SyntaxException, VersionException


def validate_version_pragma(version_str: str, location: tuple[str, int, int]) -> None:
    """
    Validates a version pragma directive against the current compiler version.
    """
    from forge import __version__

    if len(version_str) == 0:
        raise VersionException("Version specification cannot be empty", *location)

    # X.Y.Z or vX.Y.Z => ==X.Y.Z, ==vX.Y.Z
    if re.match("[v0-9]", version_str):
        version_str = "==" + version_str
    # convert npm to pep440
    version_str = re.sub("^\\^", "~=", version_str)

    try:
        spec = SpecifierSet(version_str)
    except InvalidSpecifier:
        raise VersionException(
            f'Version specification "{version_str}" is not a valid PEP440 specifier', *location
        )

    if not spec.contains(__version__, prereleases=True):
        raise VersionException(
            f'Version specification "{version_str}" is not compatible '
            f'with compiler version "{__version__}"',
            *location,
        )


def _parse_pragma(comment_contents, settings, is_interface, code, start):
    pragma = comment_contents.removeprefix("pragma ").strip()

    # location for error messages
    location = code, *start

    if pragma.startswith("version "):
        if settings.compiler_version is not None:
            raise PragmaException("pragma version specified twice!", *location)
        compiler_version = pragma.removeprefix("version ").strip()
        validate_version_pragma(compiler_version, location)
        settings.compiler_version = compiler_version
        return

    # TODO: refactor these to something like Settings.from_pragma
    # note similarity to cli arg parsing.
    if pragma.startswith("optimize "):
        if settings.optimize is not None:
            raise PragmaException("pragma optimize specified twice!", *location)
        try:
            mode = pragma.removeprefix("optimize").strip()
            settings.optimize = OptimizationLevel.from_string(mode)
        except ValueError:
            raise PragmaException(f"Invalid optimization mode `{mode}`", *location)
        return

    if pragma == "enable-decimals":
        if settings.enable_decimals is not None:
            raise PragmaException("pragma enable_decimals specified twice!", *location)
        settings.enable_decimals = True
        return

    if pragma.startswith("nonreentrancy "):
        if is_interface:
            raise PragmaException("pragma nonreentrancy not allowed in interface files!", *location)

        if settings.nonreentrancy_by_default is not None:
            raise PragmaException("pragma nonreentrancy specified twice!", *location)
        pragma = pragma.removeprefix("nonreentrancy").strip()
        if pragma not in ("on", "off"):
            raise PragmaException("invalid pragma reentrancy (expected on/off)", *location)
        settings.nonreentrancy_by_default = pragma == "on"
        return

    raise PragmaException(f"Unknown pragma `{pragma.split()[0]}`", *location)  # pragma: nocover


class ParserState(enum.Enum):
    NOT_RUNNING = enum.auto()
    START_SOON = enum.auto()
    RUNNING = enum.auto()


# a simple state machine which allows us to handle loop variable annotations
# (which are rejected by the python parser due to pep-526, so we scoop up the
# tokens between `:` and `in` and parse them and add them back in later).
class ForParser:
    def __init__(self, code):
        self._code = code
        self.annotations = {}
        self._current_annotation = None

        self._state = ParserState.NOT_RUNNING
        self._current_for_loop = None

    def consume(self, token):
        # state machine: we can start slurping tokens soon
        if token.type == NAME and token.string == "for":
            # note: self._state should be NOT_RUNNING here, but we don't sanity
            # check here as that should be an error the parser will handle.
            self._state = ParserState.START_SOON
            self._current_for_loop = token.start

        if self._state == ParserState.NOT_RUNNING:
            return False

        # state machine: start slurping tokens
        if token.type == OP and token.string == ":":
            self._state = ParserState.RUNNING

            # sanity check -- this should never really happen, but if it does,
            # try to raise an exception which pinpoints the source.
            if self._current_annotation is not None:
                raise SyntaxException(
                    "for loop parse error", self._code, token.start[0], token.start[1]
                )

            self._current_annotation = []
            return True  # do not add ":" to tokens.

        # state machine: end slurping tokens
        if token.type == NAME and token.string == "in":
            self._state = ParserState.NOT_RUNNING
            self.annotations[self._current_for_loop] = self._current_annotation or []
            self._current_annotation = None
            return False

        if self._state != ParserState.RUNNING:
            return False

        # slurp the token
        if self._current_annotation is not None:
            self._current_annotation.append(token)
        return True


class HexStringParser:
    def __init__(self):
        self.locations = []
        self._tokens = []
        self._state = ParserState.NOT_RUNNING

    def consume(self, token, result):
        # prepare to check if the next token is a STRING
        if self._state == ParserState.NOT_RUNNING:
            if token.type == NAME and token.string == "x":
                self._tokens.append(token)
                self._state = ParserState.RUNNING
                return True

            return False

        assert self._state == ParserState.RUNNING, "unreachable"

        self._state = ParserState.NOT_RUNNING

        if token.type != STRING:
            # flush the tokens we have accumulated and move on
            result.extend(self._tokens)
            self._tokens = []
            return False

        # mark hex string in locations for later processing
        self.locations.append(token.start)

        # discard the `x` token and apply sanity checks -
        # we should only be discarding one token.
        assert len(self._tokens) == 1
        assert (x_tok := self._tokens[0]).type == NAME and x_tok.string == "x"
        self._tokens = []  # discard tokens

        result.append(token)
        return True


# compound statements that are replaced with `class`
VYPER_CLASS_TYPES = {"error": "ErrorDef", "interface": "InterfaceDef", "struct": "StructDef"}

# simple statements that are replaced with `yield`
CUSTOM_STATEMENT_TYPES = {"log": "Log"}

# class declarations that can be expressed using a bare decorator
DECORATED_CLASS_TYPES = {"template": "TemplateDef", "variant": "VariantDef"}


class PreParser:
    # Compilation settings based on the directives in the source code
    settings: Settings

    # A mapping of offsets to new class names
    keyword_translations: dict[tuple[int, int], str]

    # Map from offsets in the original vyper source code to offsets
    # in the new ("reformatted", i.e. python-compatible) source code
    adjustments: dict[tuple[int, int], int]

    # A mapping of line/column offsets of `For` nodes to the annotation of the for loop target
    for_loop_annotations: dict[tuple[int, int], list[TokenInfo]]
    # A list of line/column offsets of hex string literals
    hex_string_locations: list[tuple[int, int]]
    # Reformatted python source string.
    reformatted_code: str

    def __init__(self, is_interface):
        self._is_interface = is_interface

    def parse(self, code: str):
        """
        Re-formats a vyper source string into a python source string and performs
        some validation.  More specifically,

        * Translates Vyper-style container keywords into Python-compatible syntax
        * Validates "@version" pragma against current compiler version
        * Prevents use of python semi-colon statement separator
        * Extracts type annotation of for loop iterators into a separate dictionary

        Stores a mapping of detected interface and struct names to their
        respective vyper class types ("interface" or "struct"), and a mapping of line numbers
        of for loops to the type annotation of their iterators.

        Parameters
        ----------
        code : str
            The source code to be re-formatted.
        """
        try:
            self._parse(code)
        except IndentationError as e:
            offset = e.offset - 1 if e.offset is not None else None
            raise SyntaxException(str(e), code, e.lineno, offset) from e
        except TokenError as e:
            raise SyntaxException(e.args[0], code, e.args[1][0], e.args[1][1]) from e

    def _is_enum_class(self, token_list: list[TokenInfo], class_index: int) -> bool:
        """Return whether ``class Name(Enum):`` starts at ``class_index``."""

        tokens = token_list[class_index + 1 :]
        if len(tokens) < 5:
            return False
        if tokens[0].type != NAME or tokens[1].string != "(":
            return False

        if tokens[2].type == NAME and tokens[2].string == "Enum":
            return tokens[3].string == ")" and tokens[4].string == ":"

        return (
            len(tokens) >= 7
            and tokens[2].type == NAME
            and tokens[3].string == "."
            and tokens[4].type == NAME
            and tokens[4].string == "Enum"
            and tokens[5].string == ")"
            and tokens[6].string == ":"
        )

    def _get_bare_decorated_class_type(
        self, token_list: list[TokenInfo], class_index: int
    ) -> str | None:
        if class_index < 3:
            return None
        newline_tok, name_tok, at_tok = (
            token_list[class_index - 1],
            token_list[class_index - 2],
            token_list[class_index - 3],
        )
        if not (
            newline_tok.type == NEWLINE
            and name_tok.type == NAME
            and at_tok.type == OP
            and at_tok.string == "@"
            and at_tok.start[1] == 0
        ):
            return None

        return DECORATED_CLASS_TYPES.get(name_tok.string)

    def _parse(self, code: str):
        adjustments: dict = {}
        result: list[TokenInfo] = []
        keyword_translations: dict[tuple[int, int], str] = {}
        settings = Settings()
        for_parser = ForParser(code)
        hex_string_parser = HexStringParser()

        _col_adjustments: dict[int, int] = defaultdict(lambda: 0)

        code_bytes = code.encode("utf-8")
        token_list = list(tokenize(io.BytesIO(code_bytes).readline))

        for i, token in enumerate(token_list):
            toks = [token]

            typ = token.type
            string = token.string
            start = token.start
            end = token.end
            line = token.line

            # handle adjustments
            lineno, col = token.start
            adj = _col_adjustments[lineno]
            newstart = lineno, col - adj
            adjustments[lineno, col - adj] = adj

            # TODO: addapt to daml versioning
            if typ == COMMENT:
                contents = string[1:].strip()
                if contents.startswith("@version"):
                    if settings.compiler_version is not None:
                        raise PragmaException("compiler version specified twice!", code, *start)
                    compiler_version = contents.removeprefix("@version ").strip()
                    validate_version_pragma(compiler_version, (code, *start))
                    settings.compiler_version = compiler_version

                if contents.startswith("pragma "):
                    _parse_pragma(contents, settings, self._is_interface, code, start)

            if typ == NAME and string == "class":
                # validating if its bare template or variant and replacing with the correct class
                # type, i.e. TemplateDef or VariantDef
                decorated_class_type = self._get_bare_decorated_class_type(token_list, i)
                if decorated_class_type is not None:
                    keyword_translations[newstart] = decorated_class_type
                elif self._is_enum_class(token_list, i):
                    keyword_translations[newstart] = "EnumDef"
                else:
                    raise SyntaxException(
                        f"The `{string}` keyword is not allowed. ", code, start[0], start[1]
                    )

            elif typ == NAME and string in ("class", "yield"):
                raise SyntaxException(
                    f"The `{string}` keyword is not allowed. ", code, start[0], start[1]
                )

            if typ == NAME:
                # see if it's a keyword we need to replace
                new_keyword = None
                if string in VYPER_CLASS_TYPES and start[1] == 0:
                    new_keyword = "class"
                    vyper_type = VYPER_CLASS_TYPES[string]
                elif string in CUSTOM_STATEMENT_TYPES:
                    new_keyword = "yield"
                    vyper_type = CUSTOM_STATEMENT_TYPES[string]

                if new_keyword is not None:
                    keyword_translations[newstart] = vyper_type

                    adjustment = len(string) - len(new_keyword)
                    # adjustments for following tokens
                    lineno, col = start
                    _col_adjustments[lineno] += adjustment

                    # a bit cursed technique to get untokenize to put
                    # the new tokens in the right place so that
                    # `keyword_translations` will work correctly.
                    # (recommend comparing the result of parse with the
                    # source code side by side to visualize the whitespace)
                    toks = [TokenInfo(NAME, new_keyword, start, end, line)]

            if (typ, string) == (OP, ";"):
                raise SyntaxException("Semi-colon statements not allowed", code, start[0], start[1])

            if not for_parser.consume(token) and not hex_string_parser.consume(token, result):
                result.extend(toks)

        for_loop_annotations = {}
        for k, v in for_parser.annotations.items():
            for_loop_annotations[k] = v.copy()

        self.adjustments = adjustments
        self.settings = settings
        self.keyword_translations = keyword_translations
        self.for_loop_annotations = for_loop_annotations
        self.hex_string_locations = hex_string_parser.locations
        try:
            self.reformatted_code = untokenize(result).decode("utf-8")  # type: ignore[union-attr]
        except (IndexError, ValueError) as e:
            syntax_line, syntax_col = for_parser._current_for_loop or (1, 0)
            raise SyntaxException("Invalid for-loop syntax", code, syntax_line, syntax_col) from e
