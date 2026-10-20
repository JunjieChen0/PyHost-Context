"""PyHost-Context 准入阶段：合成 pytest 套件 fixture（仅在临时目录中生成，不含任何候选项目）。

覆盖：正常通过、call 失败、显式 skip、fixture setup 失败（error / call not_run）、
xfail、非严格 XPASS、严格 XPASS；xfail 变体（``condition=False`` / ``run=False``）；
同一测试 call+teardown 同时失败；真实语义 时间/时区/expected/actual 值与对象 repr 地址、
运行目录路径；以及用于 gate 的全通过、全跳过、全失败与混合套件。
"""
from pathlib import Path

CORE = ("test_core.py", '''\
"""合成核心套件：pass/fail/skip/setup-error/xfail/XPASS/严格 XPASS。"""
import pytest


def test_pass():
    assert 1 + 1 == 2


def test_fail():
    assert 1 == 2


def test_skip():
    pytest.skip("intentional skip")


@pytest.fixture
def broken_setup():
    raise RuntimeError("setup boom")


def test_setup_error(broken_setup):
    assert True


@pytest.mark.xfail(reason="known bug")
def test_xfail():
    assert False


@pytest.mark.xfail(reason="unexpected pass")
def test_xpass():
    assert True


@pytest.mark.xfail(reason="strict pass", strict=True)
def test_xpass_strict():
    assert True
''')

XFAIL_VARIANTS = ("test_xfail_variants.py", '''\
"""xfail 变体：condition=False 应正常执行；run=False 应在 setup 阶段标记 xfail。"""
import pytest


@pytest.mark.xfail(reason="condition false runs normally", condition=False)
def test_xfail_condition_false():
    assert True


@pytest.mark.xfail(reason="not run", run=False)
def test_xfail_run_false():
    assert True
''')

DUAL_FAILURE = ("test_dual_failure.py", '''\
"""同一测试 call 与 teardown 同时失败；以及仅 teardown 失败。"""
import pytest


@pytest.fixture
def teardown_boom():
    yield
    raise RuntimeError("teardown boom")


def test_call_and_teardown_fail(teardown_boom):
    assert 1 == 2


def test_teardown_only_fail(teardown_boom):
    assert True
''')

SEMANTIC = ("test_semantic.py", '''\
"""失败签名用例：含真实语义 时间/时区/expected/actual/颜色、非 repr 的 0x 值、
时长样式值，以及对象 repr 地址与运行目录路径。"""
import os


def test_semantic_failure():
    obj = object()
    artifact = os.path.join(os.getcwd(), "artifacts", "out.txt")
    assert False, (
        "when=2026-10-14T12:34:56+08:00 tz=Asia/Shanghai "
        "expected=3 actual=4 color=red token=0xdeadbeef elapsed=0.123s "
        f"obj={obj!r} artifact={artifact}")
''')

BROKEN = ("test_broken_collect.py", '''\
"""collection 失败用例：导入不存在的模块。"""
import this_module_does_not_exist_pyhost  # noqa: F401


def test_never_runs():
    assert True
''')

ADDED = ("test_added.py", '''\
"""identity 变更用例：额外新增的测试。"""
def test_added():
    assert True
''')

ALL_PASS = ("test_all_pass.py", '''\
def test_alpha():
    assert True


def test_beta():
    assert 2 + 2 == 4
''')

ALL_SKIP = ("test_all_skip.py", '''\
import pytest


def test_skip_one():
    pytest.skip("skip one")


def test_skip_two():
    pytest.skip("skip two")
''')

ALL_FAIL = ("test_all_fail.py", '''\
def test_fail_one():
    assert 1 == 2


def test_fail_two():
    assert 3 == 4
''')

GATE_MIXED = ("test_gate_mixed.py", '''\
"""gate 混合套件：一个真实通过 + skip + xfail + 非严格 XPASS。"""
import pytest


def test_real_pass():
    assert True


def test_skip():
    pytest.skip("allowed skip")


@pytest.mark.xfail(reason="known")
def test_xfail():
    assert False


@pytest.mark.xfail(reason="unexpected pass")
def test_xpass():
    assert True
''')

FIXTURES = {
    "core": CORE,
    "xfail_variants": XFAIL_VARIANTS,
    "dual": DUAL_FAILURE,
    "semantic": SEMANTIC,
    "broken": BROKEN,
    "added": ADDED,
    "all_pass": ALL_PASS,
    "all_skip": ALL_SKIP,
    "all_fail": ALL_FAIL,
    "gate_mixed": GATE_MIXED,
}


def materialize(root, parts=("core",)):
    """在 ``root`` 下写入 ``parts`` 指定的 fixture 文件，返回 ``root``（Path）。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name in parts:
        filename, source = FIXTURES[name]
        (root / filename).write_text(source, encoding="utf-8")
    return root
