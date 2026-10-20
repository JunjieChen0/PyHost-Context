"""ClawsGO Science Agent：显式环境三态、启动环境白名单/差异，并与文件状态分开保存。"""
import hashlib
from pathlib import Path

THREE_STATES = ("unset", "empty", "set")

# 直接环境路径键：直接决定可执行文件、模块与临时文件如何被解析，不作为 Python 运行时
# 观测属性；对这类键评估 runtime_effect 记为 not_applicable。
DIRECT_ENVIRONMENT_PATH_KEYS = (
    "PATH", "PYTHONPATH", "PYTHONHOME", "TMPDIR", "HOME", "PWD",
    "USER", "SHELL", "PYTHONEXECUTABLE",
)
# 运行时状态键：改变解释器初始化后的运行时状态。
RUNTIME_STATE_KEYS = (
    "TZ", "LC_ALL", "LC_CTYPE", "LANG", "LANGUAGE",
    "PYTHONHASHSEED", "PYTHONCOERCECLOCALE", "PYTHONUTF8",
)
# 输出/项目消费键：由项目或库在输出层消费。
PROJECT_OUTPUT_KEYS = (
    "NO_COLOR", "TERM", "COLUMNS", "LINES",
    "FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE", "PY_COLORS",
)

SOURCE_SUFFIXES = (".py", ".cfg", ".txt", ".rst", ".yaml", ".ini", ".in", ".toml")


def three_state(env, key):
    """把一个环境映射按显式三态表示：缺失为 unset，空串为 empty，其余为 set(value)。"""
    if key not in env:
        return {"state": "unset"}
    value = env[key]
    if value == "":
        return {"state": "empty"}
    return {"state": "set", "value": value}


def env_state(env, keys):
    """按给定键集合生成三态快照。"""
    return {key: three_state(env, key) for key in keys}


def env_key_kind(key):
    if key in DIRECT_ENVIRONMENT_PATH_KEYS:
        return "direct_environment_path"
    if key in RUNTIME_STATE_KEYS:
        return "runtime_state"
    if key in PROJECT_OUTPUT_KEYS:
        return "project_output"
    return "unclassified"


def env_key_record(key):
    """给出键的分类；直接环境路径键的 runtime_effect 为 not_applicable。"""
    kind = env_key_kind(key)
    return {
        "key": key,
        "kind": kind,
        "runtime_effect": "not_applicable" if kind == "direct_environment_path" else "applicable",
    }


def build_launch_env(whitelist, spec, base_values=None):
    """用空白环境加显式白名单构造启动环境。

    whitelist：允许出现的键全集，白名单外任何键都视为错误。
    spec：三态规格，形如 {key: {"state": "set"/"empty"/"unset", ...}}。
    base_values：白名单内、属于固定控制项的必需取值（如 PATH），不参与扰动规格。
    """
    allowed = set(whitelist)
    base_values = dict(base_values or {})
    overflow = (set(base_values) | set(spec)) - allowed
    if overflow:
        raise ValueError(f"启动环境包含白名单外键：{sorted(overflow)}")
    env = {key: str(value) for key, value in base_values.items()}
    for key, state in spec.items():
        mode = state.get("state")
        if mode == "unset":
            env.pop(key, None)
        elif mode == "empty":
            env[key] = ""
        elif mode == "set":
            env[key] = str(state["value"])
        else:
            raise ValueError(f"未知三态：{mode!r}")
    return env


def launch_env_diff(spec, actual_env, keys):
    """比较期望三态规格与实际启动环境，返回按键排序的差异条目。"""
    diff = []
    for key in sorted(keys):
        want = spec.get(key, {"state": "unset"})
        got = three_state(actual_env, key)
        if want != got:
            diff.append({"key": key, "expected": want, "actual": got})
    return diff


def file_state(source):
    """项目文件状态：路径到 SHA-256。启动环境差异不并入此结构。"""
    source = Path(source)
    state = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part.endswith(".egg-info") or part == "__pycache__" for part in path.parts):
            continue
        state[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return state


def file_state_all(source):
    """项目**全部**常规文件的状态：路径到 SHA-256。

    用于"初始文件状态"证据：不以扩展名过滤，只排除 ``__pycache__``、``*.egg-info`` 与
    ``.git``，避免"未被统计即未被检查"的盲区。
    """
    source = Path(source)
    state = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if any(part == "__pycache__" or part == ".git" or part.endswith(".egg-info")
               for part in path.parts):
            continue
        state[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return state


def file_state_hash(state):
    """只对文件状态求一个哈希；启动环境差异单独保存，不进入此哈希。"""
    payload = "".join(f"{name}:{digest}\n" for name, digest in sorted(state.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def aggregate_fingerprint(files):
    """规范文件的组合指纹；算法**明示**，避免"只给结果不给算法"。

    算法：对每个 ``(path, sha256)`` 取 ``f"{path}:{sha256}\\n"``，按 ``path`` 字节序升序
    拼接为 UTF-8 字节串，再做一次 SHA-256。若任一条目的 ``sha256`` 缺失则拒绝计算。

    ``files``：``{path: sha256}``；返回 ``(组合指纹, 规范化载荷字节数)``。
    """
    missing = sorted(path for path, digest in files.items() if not digest)
    if missing:
        raise ValueError(f"缺少文件哈希，拒绝计算组合指纹：{missing}")
    payload = "".join(f"{path}:{files[path]}\n" for path in sorted(files))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), len(payload.encode("utf-8"))
