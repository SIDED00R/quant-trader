"""마이그레이션 러너 검증 (스텁 conn/client — DB 없음).

핵심 계약: ① 파일명 규약(NNNN_*.sql)·순번 중복 검출 ② 미적용분만 버전 순 적용 + 이력 기록
③ 이미 적용분 skip ④ 실제 baseline 파일이 규약을 만족.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from common import migrations


class _FakePgConn:
    """psycopg conn 스텁: execute 기록 + SELECT는 done 행 반환, transaction()은 no-op 컨텍스트."""
    def __init__(self, done_versions=()):
        self._done = [(v,) for v in done_versions]
        self.executed = []          # (sql, params)

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        res = MagicMock()
        res.fetchall.return_value = self._done
        return res

    def transaction(self):
        class _Tx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return _Tx()


class _FakeChClient:
    """clickhouse_connect client 스텁: command/insert 기록 + query는 done 행 반환."""
    def __init__(self, done_versions=()):
        self._done = [(v,) for v in done_versions]
        self.commands = []
        self.inserts = []           # (table, data, column_names)

    def command(self, sql):
        self.commands.append(sql)

    def query(self, sql):
        res = MagicMock()
        res.result_rows = self._done
        return res

    def insert(self, table, data, column_names):
        self.inserts.append((table, data, column_names))


def _write(dirpath: Path, sub: str, name: str, body: str) -> None:
    d = dirpath / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


class TestStatements(unittest.TestCase):
    def test_split_strips_and_drops_empty(self):
        self.assertEqual(migrations._statements("A; B ;;\n C ;"), ["A", "B", "C"])

    def test_empty_string(self):
        self.assertEqual(migrations._statements("  \n ; ; "), [])


class TestMigrationFiles(unittest.TestCase):
    def test_sorted_by_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "postgres", "0002_add.sql", "SELECT 2;")
            _write(root, "postgres", "0001_baseline.sql", "SELECT 1;")
            with patch.object(migrations, "MIGRATIONS_DIR", root):
                got = migrations._migration_files("postgres")
        self.assertEqual([v for v, _ in got], [1, 2])
        self.assertEqual([p.name for _, p in got], ["0001_baseline.sql", "0002_add.sql"])

    def test_bad_filename_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "postgres", "baseline.sql", "SELECT 1;")   # 순번 없음
            with patch.object(migrations, "MIGRATIONS_DIR", root):
                with self.assertRaises(ValueError):
                    migrations._migration_files("postgres")

    def test_duplicate_version_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "postgres", "0001_a.sql", "SELECT 1;")
            _write(root, "postgres", "0001_b.sql", "SELECT 2;")
            with patch.object(migrations, "MIGRATIONS_DIR", root):
                with self.assertRaises(ValueError):
                    migrations._migration_files("postgres")


class TestApplyPostgres(unittest.TestCase):
    def test_applies_unapplied_in_order_and_records(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "postgres", "0001_baseline.sql", "CREATE TABLE t (id int);\nINSERT INTO t VALUES (1);")
            conn = _FakePgConn(done_versions=())
            with patch.object(migrations, "MIGRATIONS_DIR", root):
                applied = migrations.apply_postgres(conn)
        self.assertEqual(applied, ["0001_baseline.sql"])
        sqls = [s for s, _ in conn.executed]
        self.assertIn("CREATE TABLE t (id int)", sqls)
        self.assertIn("INSERT INTO t VALUES (1)", sqls)
        # 이력 기록: 파일명·버전 파라미터
        rec = [(s, p) for s, p in conn.executed if "INSERT INTO schema_migrations" in s]
        self.assertEqual(rec[0][1], (1, "0001_baseline.sql"))

    def test_skips_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "postgres", "0001_baseline.sql", "CREATE TABLE t (id int);")
            conn = _FakePgConn(done_versions=(1,))
            with patch.object(migrations, "MIGRATIONS_DIR", root):
                applied = migrations.apply_postgres(conn)
        self.assertEqual(applied, [])
        sqls = [s for s, _ in conn.executed]
        self.assertNotIn("CREATE TABLE t (id int)", sqls)                 # 파일 문장 미실행
        self.assertFalse(any("INSERT INTO schema_migrations" in s for s in sqls))


class TestApplyClickhouse(unittest.TestCase):
    def test_applies_and_records(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "clickhouse", "0001_baseline.sql", "CREATE TABLE a (x Int64) ENGINE=Memory;")
            client = _FakeChClient(done_versions=())
            with patch.object(migrations, "MIGRATIONS_DIR", root):
                applied = migrations.apply_clickhouse(client)
        self.assertEqual(applied, ["0001_baseline.sql"])
        self.assertIn("CREATE TABLE a (x Int64) ENGINE=Memory", client.commands)
        self.assertEqual(client.inserts, [("schema_migrations", [[1, "0001_baseline.sql"]],
                                           ["version", "filename"])])

    def test_skips_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "clickhouse", "0001_baseline.sql", "CREATE TABLE a (x Int64) ENGINE=Memory;")
            client = _FakeChClient(done_versions=(1,))
            with patch.object(migrations, "MIGRATIONS_DIR", root):
                applied = migrations.apply_clickhouse(client)
        self.assertEqual(applied, [])
        self.assertNotIn("CREATE TABLE a (x Int64) ENGINE=Memory", client.commands)
        self.assertEqual(client.inserts, [])


class TestRealBaselines(unittest.TestCase):
    """실제 db/migrations/ 파일이 규약을 만족하고 파싱되는지(전 문장 세미콜론 종결)."""
    def test_baselines_parse(self):
        for sub in ("postgres", "clickhouse"):
            files = migrations._migration_files(sub)
            self.assertEqual(files[0][0], 1, f"{sub} 첫 마이그레이션은 0001")
            for _, path in files:
                stmts = migrations._statements(path.read_text(encoding="utf-8"))
                self.assertTrue(stmts, f"{path.name}에 문장이 없음")


if __name__ == "__main__":
    unittest.main()
