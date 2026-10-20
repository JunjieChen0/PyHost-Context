"""合成测试替身：run_matrix.run_one 的签名占位。

测试会 monkeypatch 该属性；包内自检不会真正执行测试轮。
"""


def run_one(*args, **kwargs):  # pragma: no cover - 始终被替换
    raise RuntimeError("stub_run_one_must_be_patched")
