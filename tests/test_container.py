"""Tests for the container management module."""

import textwrap

from mcp_plotly.container import _wrap_code


class TestWrapCode:
    def test_wrap_code_both_formats(self):
        code = "fig = px.bar(x=[1,2,3], y=[4,5,6])"
        result = _wrap_code(code, "both")
        assert "import plotly.graph_objects as go" in result
        assert "import plotly.express as px" in result
        assert code in result
        assert "fig.write_image" in result
        assert "fig.write_html" in result
        assert "plot.png" in result
        assert "plot.html" in result

    def test_wrap_code_png_only(self):
        code = "fig = go.Figure()"
        result = _wrap_code(code, "png")
        assert "fig.write_image" in result
        assert "fig.write_html" not in result

    def test_wrap_code_html_only(self):
        code = "fig = go.Figure()"
        result = _wrap_code(code, "html")
        assert "fig.write_image" not in result
        assert "fig.write_html" in result

    def test_wrap_code_preserves_user_code(self):
        code = textwrap.dedent("""\
            import random
            data = [random.randint(1, 10) for _ in range(5)]
            fig = px.bar(x=list(range(5)), y=data)
        """)
        result = _wrap_code(code, "both")
        assert "import random" in result
        assert "data = [random.randint" in result
