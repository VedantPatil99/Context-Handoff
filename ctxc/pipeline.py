"""Compiler driver: transcript -> Context IR.

    load_transcript  (source reader)
      -> lex                  (lexical analysis: sentence lexemes)
      -> run_front_end        (parsing: fact extraction + scope stack + SSA symbol table)
      -> analyze              (reachability / dead-symbol elimination)
      -> separate             (constant pool)
      -> build_bundle         (IR serialization)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import ir
from .constpool import Partition, separate
from .extractor import FrontEndResult, run_front_end
from .lexer import lex
from .reachability import Reachability, analyze
from .transcript import Turn, load_transcript, render


@dataclass
class CompileResult:
    turns: list[Turn]
    front: FrontEndResult
    reach: Reachability
    partition: Partition
    bundle: dict

    @property
    def header(self) -> str:
        return ir.render_header(self.bundle)


def compile_turns(turns: list[Turn], source: str = "<memory>", *,
                  global_kinds: frozenset[str] = frozenset()) -> CompileResult:
    lexemes = lex(turns)
    fe = run_front_end(lexemes)
    reach = analyze(fe, global_kinds)
    part = separate(fe.symtab, reach)
    bundle = ir.build_bundle(fe, reach, part, source=source, raw_text=render(turns), n_turns=len(turns))
    return CompileResult(turns, fe, reach, part, bundle)


def compile_file(path: str | Path, *, global_kinds: frozenset[str] = frozenset()) -> CompileResult:
    return compile_turns(load_transcript(path), source=str(path), global_kinds=global_kinds)
