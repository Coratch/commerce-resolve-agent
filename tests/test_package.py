"""验证 CommerceResolve Python 包的基本可导入性。"""

import ast
from pathlib import Path


def test_package_imports() -> None:
    """验证测试环境能够导入应用包并读取模块说明。"""

    import commerce_resolve

    assert commerce_resolve.__doc__


def test_all_product_functions_have_chinese_docstrings() -> None:
    """验证所有产品函数和方法都具有至少包含一个中文字符的 docstring。"""

    source_root = Path(__file__).parent.parent / "src"
    violations: list[str] = []
    for source_file in source_root.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            docstring = ast.get_docstring(node)
            if docstring is None or not any(
                "\u4e00" <= character <= "\u9fff" for character in docstring
            ):
                violations.append(f"{source_file}:{node.lineno}:{node.name}")

    assert violations == []
