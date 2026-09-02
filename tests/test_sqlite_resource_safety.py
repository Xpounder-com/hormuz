from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests._sqlite import managed_sqlite_connection


class SQLiteResourceSafetyTests(unittest.TestCase):
    def test_managed_connection_commits_and_closes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "managed.sqlite3"
            with managed_sqlite_connection(path) as connection:
                connection.execute("CREATE TABLE example (value TEXT NOT NULL)")
                connection.execute("INSERT INTO example VALUES ('committed')")
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                connection.execute("SELECT 1")
            with managed_sqlite_connection(path) as reader:
                self.assertEqual(reader.execute("SELECT value FROM example").fetchone(), ("committed",))

    def test_tests_do_not_treat_the_transaction_context_as_connection_custody(self):
        unsafe: list[str] = []
        for path in sorted(Path(__file__).parent.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.With, ast.AsyncWith)):
                    continue
                for item in node.items:
                    call = item.context_expr
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "sqlite3"
                        and call.func.attr == "connect"
                    ):
                        unsafe.append(f"{path.name}:{node.lineno}")
        self.assertEqual(unsafe, [])


if __name__ == "__main__":
    unittest.main()
