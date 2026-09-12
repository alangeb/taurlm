"""Tests for new skills: web_research, code_analysis, system_info."""


class TestCodeAnalysisSkill:
    """Tests for code_analysis skill."""

    def test_count_loc(self, tmp_path):
        """Test counting lines of code."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo():\n    pass\n\n# comment\nx = 1\n")
        from skills.code_analysis import run

        result = run(action="count_loc", file_path=str(test_file))
        assert result["success"]
        assert result["lines"] >= 5  # 5 lines + trailing newline
        assert result["characters"] > 0

    def test_find_functions(self, tmp_path):
        """Test finding function definitions."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo():\n    pass\n\ndef bar(x):\n    return x\n")
        from skills.code_analysis import run

        result = run(action="find_functions", file_path=str(test_file))
        assert result["success"]
        assert "foo" in result["functions"]
        assert "bar" in result["functions"]

    def test_find_classes(self, tmp_path):
        """Test finding class definitions."""
        test_file = tmp_path / "test.py"
        test_file.write_text("class Foo:\n    pass\n\nclass Bar:\n    pass\n")
        from skills.code_analysis import run

        result = run(action="find_classes", file_path=str(test_file))
        assert result["success"]
        assert "Foo" in result["classes"]
        assert "Bar" in result["classes"]

    def test_find_imports(self, tmp_path):
        """Test finding imports."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nfrom pathlib import Path\nimport json\n")
        from skills.code_analysis import run

        result = run(action="find_imports", file_path=str(test_file))
        assert result["success"]
        assert "import os" in result["imports"]
        assert "from pathlib import Path" in result["imports"]

    def test_analyze(self, tmp_path):
        """Test comprehensive file analysis."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\n\ndef foo():\n    pass\n\nclass Bar:\n    pass\n")
        from skills.code_analysis import run

        result = run(action="analyze", file_path=str(test_file))
        assert result["success"]
        assert "loc" in result
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result

    def test_scan_dir(self, tmp_path):
        """Test directory scanning."""
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\nz = 3\n")
        from skills.code_analysis import run

        result = run(action="scan_dir", dir_path=str(tmp_path))
        assert result["success"]
        assert len(result["files"]) >= 2

    def test_missing_file_path(self):
        """Test error on missing file_path."""
        from skills.code_analysis import run

        result = run(action="count_loc")
        assert not result["success"]
        assert "file_path" in result["error"]


class TestSystemInfoSkill:
    """Tests for system_info skill."""

    def test_disk_usage(self):
        """Test disk usage."""
        from skills.system_info import run

        result = run(action="disk_usage", path="/")
        assert result["success"]
        assert "total_gb" in result
        assert "used_gb" in result
        assert "free_gb" in result

    def test_memory(self):
        """Test memory info."""
        from skills.system_info import run

        result = run(action="memory")
        assert result["success"]
        assert "total_mb" in result or "error" in result

    def test_cpu(self):
        """Test CPU info."""
        from skills.system_info import run

        result = run(action="cpu")
        assert result["success"]
        assert "cpu_count" in result

    def test_env(self):
        """Test environment variables."""
        from skills.system_info import run

        result = run(action="env", prefix="PATH")
        assert result["success"]
        assert "count" in result

    def test_run_command(self):
        """Test running shell commands."""
        from skills.system_info import run

        result = run(action="run", cmd="echo hello")
        assert result["success"]
        assert result["returncode"] == 0
        assert "hello" in result["stdout"]

    def test_summary(self):
        """Test system summary."""
        from skills.system_info import run

        result = run(action="summary")
        assert result["success"]
        assert "platform" in result
        assert "python_version" in result

    def test_missing_cmd(self):
        """Test error on missing cmd."""
        from skills.system_info import run

        result = run(action="run")
        assert not result["success"]
        assert "cmd" in result["error"]


class TestWebResearchSkill:
    """Tests for web_research skill."""

    def test_missing_url(self):
        """Test error on missing URL."""
        from skills.web_research import run

        result = run(action="fetch_url")
        assert not result["success"]
        assert "URL" in result["error"]

    def test_missing_query(self):
        """Test error on missing query."""
        from skills.web_research import run

        result = run(action="search")
        assert not result["success"]
        assert "Query" in result["error"]

    def test_unknown_action(self):
        """Test unknown action."""
        from skills.web_research import run

        result = run(action="unknown")
        assert not result["success"]
        assert "Unknown action" in result["error"]
