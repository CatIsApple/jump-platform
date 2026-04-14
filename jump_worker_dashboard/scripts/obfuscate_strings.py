"""Pre-build string obfuscation for Nuitka.

Transforms Python source files before Nuitka compilation:
1. Strips all docstrings
2. XOR-encodes string constants (> 6 chars, non-import, non-dunder)
3. Injects a tiny runtime decoder

Usage:
    python obfuscate_strings.py <directory> [<directory2> ...]
"""
from __future__ import annotations

import ast
import os
import random
import sys
from pathlib import Path

# ── XOR key (randomized per build) ──
XOR_KEY = random.randint(0x10, 0xFE)

# ── Decoder function injected into each module ──
DECODER_FUNC = f"""
def _xd(d):
    return bytes(b^{XOR_KEY} for b in d).decode()
"""

# Strings to NEVER encode (would break imports/runtime)
SKIP_PATTERNS = {
    "__name__", "__main__", "__init__", "__file__", "__doc__",
    "__all__", "__version__", "__future__", "__spec__", "__loader__",
    "__package__", "__path__", "__builtins__", "__cached__",
    "utf-8", "utf8", "ascii", "latin-1", "rb", "wb", "r", "w", "a",
    "rb+", "wb+", "r+", "w+", "a+",
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
    "True", "False", "None",
    "return", "args", "kwargs", "self", "cls",
}


def xor_encode(s: str) -> bytes:
    """Encode string with XOR key."""
    return bytes(b ^ XOR_KEY for b in s.encode("utf-8", errors="surrogatepass"))


def should_encode(s: str) -> bool:
    """Determine if a string should be XOR-encoded."""
    if len(s) <= 6:
        return False
    if s in SKIP_PATTERNS:
        return False
    # Skip dunder names
    if s.startswith("__") and s.endswith("__"):
        return False
    # Skip single-word identifiers (likely variable/module names for imports)
    if s.isidentifier() and "." not in s and "_" not in s:
        return False
    # Skip format specs
    if s.startswith("{") and s.endswith("}"):
        return False
    # Skip short common patterns
    if s.startswith("%.") or s.startswith("%s") or s.startswith("%d"):
        return False
    return True


class DocstringStripper(ast.NodeTransformer):
    """Remove all docstrings from AST."""

    def _strip_docstring(self, node):
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
        return node

    def visit_Module(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

    def visit_AsyncFunctionDef(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)


class StringEncoder(ast.NodeTransformer):
    """Replace string constants with XOR-decoded calls."""

    def __init__(self):
        self.encoded_count = 0
        self._in_import = False
        self._in_decorator = False
        self._in_annotation = False
        self._in_fstring = False

    def visit_Import(self, node):
        return node  # Don't touch imports

    def visit_ImportFrom(self, node):
        return node  # Don't touch imports

    def visit_FunctionDef(self, node):
        # Don't encode decorator arguments
        old = self._in_annotation
        # Process annotations carefully
        self._in_annotation = True
        if node.returns:
            node.returns = self.visit(node.returns)
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.annotation:
                arg.annotation = self.visit(arg.annotation)
        self._in_annotation = old

        # Process decorators
        self._in_decorator = True
        node.decorator_list = [self.visit(d) for d in node.decorator_list]
        self._in_decorator = False

        # Process body
        node.body = [self.visit(stmt) for stmt in node.body]
        return node

    def visit_AsyncFunctionDef(self, node):
        return self.visit_FunctionDef(node)

    def visit_JoinedStr(self, node):
        # Never transform strings inside f-strings (ast.unparse can't handle Call in JoinedStr)
        return node

    def visit_Constant(self, node):
        if self._in_annotation or self._in_decorator or self._in_fstring:
            return node
        if not isinstance(node.value, str):
            return node
        if not should_encode(node.value):
            return node

        try:
            encoded = xor_encode(node.value)
        except (UnicodeEncodeError, UnicodeDecodeError):
            return node
        self.encoded_count += 1

        # _xd(b'\x..')
        return ast.Call(
            func=ast.Name(id="_xd", ctx=ast.Load()),
            args=[ast.Constant(value=encoded)],
            keywords=[],
        )


def process_file(filepath: Path, strip_only: bool = False) -> tuple[int, int]:
    """Process a single Python file. Returns (docstrings_removed, strings_encoded)."""
    try:
        source = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return 0, 0

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return 0, 0

    # Count original docstrings
    original_docstrings = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (
                hasattr(node, "body")
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                original_docstrings += 1

    # Step 1: Strip docstrings
    tree = DocstringStripper().visit(tree)

    # Step 2: Encode strings
    encoder = StringEncoder()
    if not strip_only:
        tree = encoder.visit(tree)

    ast.fix_missing_locations(tree)

    # Generate modified source
    try:
        new_source = ast.unparse(tree)
    except Exception:
        return 0, 0

    # Inject decoder if any strings were encoded
    # Must go AFTER any `from __future__` imports to avoid SyntaxError
    if encoder.encoded_count > 0:
        lines = new_source.split("\n")
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("from __future__"):
                insert_idx = i + 1
        lines.insert(insert_idx, DECODER_FUNC)
        new_source = "\n".join(lines)

    filepath.write_text(new_source, encoding="utf-8")
    return original_docstrings, encoder.encoded_count


def process_directory(dirpath: Path) -> None:
    """Process all .py files in directory recursively."""
    total_docs = 0
    total_encoded = 0
    total_files = 0

    skip_dirs = {"__pycache__", ".venv", ".venv-build", "venv", ".git", "node_modules", "scripts"}
    for pyfile in sorted(dirpath.rglob("*.py")):
        if any(part in skip_dirs for part in pyfile.parts):
            continue
        docs, encoded = process_file(pyfile)
        if docs > 0 or encoded > 0:
            total_files += 1
            total_docs += docs
            total_encoded += encoded
            print(f"  {pyfile.relative_to(dirpath)}: -{docs} docstrings, +{encoded} encoded")

    print(f"\nTotal: {total_files} files, {total_docs} docstrings removed, {total_encoded} strings encoded")
    print(f"XOR key: 0x{XOR_KEY:02X}")


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory> [<directory2> ...]")
        sys.exit(1)

    for dirpath in sys.argv[1:]:
        p = Path(dirpath)
        if not p.is_dir():
            print(f"Error: {dirpath} is not a directory")
            sys.exit(1)
        print(f"\nProcessing: {p}")
        process_directory(p)


if __name__ == "__main__":
    main()
