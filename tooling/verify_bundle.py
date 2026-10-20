"""ClawsGO Science Agent：只读证据包的独立复算脚本。

只解压到内存、不执行任何候选项目代码、不安装任何东西。用法：

    python3 verify_bundle.py <证据包.zip> [--json 输出路径]

复算并报告：

1. ARCHIVE_MANIFEST.json 的成员字节数与 SHA-256 是否与包内实际一致，组合指纹是否可复算；
2. 每一轮的逐测试结果向量、阶段状态向量、collection 身份与顺序，与
   ``runs/<candidate>/candidate_result.json`` 是否一致；
3. 启动环境**三视图**是否一致：``roundN.launch_env.json`` 与 ``roundN.boot_env.json``
   的 ``post_init_env``，以及 JSONL 内 ``RuntimeSnapshot.environment``；
4. 每轮起始文件状态哈希是否逐轮相同，以及每轮开始前清理的路径清单；
5. 用包内 ``admission_gate.py`` 对每候选五轮重新判定，与记录结论比对。

任何一项不一致都会以非零退出码结束。
"""
import argparse
import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import zipfile

MEMBER_MANIFEST = "ARCHIVE_MANIFEST.json"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def three_state(env, key):
    if env is None:
        return None
    if key not in env:
        return {"state": "unset"}
    return {"state": "empty"} if env[key] == "" else {"state": "set", "value": env[key]}


def load_tooling(zf, names):
    """把包内运行器源码解到临时目录并按普通模块导入（不执行任何候选项目代码）。"""
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="pyhost-verify-"))
    for name in names:
        if name.startswith("tooling/") and name.endswith(".py"):
            (workdir / pathlib.Path(name).name).write_bytes(zf.read(name))
    sys.path.insert(0, str(workdir))
    import admission_gate as gate_module
    import pytest_jsonl as parser_module
    return parser_module, gate_module


def bundle_path_of(normative_path):
    """把规范文件在仓库中的相对路径映射到证据包内的成员路径。"""
    if normative_path.startswith("admission_freeze/"):
        return "freeze/" + normative_path.split("admission_freeze/", 1)[1]
    if normative_path.startswith("tooling/admission_runner/"):
        return "tooling/" + normative_path.split("admission_runner/", 1)[1]
    if normative_path.startswith("tooling/"):
        return normative_path
    return "records/" + pathlib.PurePosixPath(normative_path).name


def aggregate(mapping):
    payload = "".join(f"{path}:{mapping[path]}\n" for path in sorted(mapping))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_executor_revision(zf, names):
    """复核事后修订记录：当前规范指纹可复算，且历史替代后原运行前承诺也可复算。

    这是 v6 审计指出的缺口：原作者验证器不校验修订记录与实际文件是否对应。
    本函数用包内文件复算两个指纹——修订后的当前指纹，以及用 historical_source_at_run/
    替代被修订成员后的原运行前承诺指纹——两者都能在单包内核验。
    """
    detail = {"checked": False}
    rev_name = "freeze/executor_revision_after_run.json"
    commit_name = "freeze/pre_run_commitment.json"
    if rev_name not in names or commit_name not in names:
        detail["note"] = "包内缺修订记录或运行前承诺，跳过"
        return detail

    revision = json.loads(zf.read(rev_name))
    commitment = json.loads(zf.read(commit_name))
    normative = commitment["files"]
    current, historical, missing = {}, {}, []
    for spec_path in normative:
        member = bundle_path_of(spec_path)
        if member not in names:
            missing.append(member)
            continue
        current[spec_path] = sha256(zf.read(member))
        hist_member = "historical_source_at_run/" + pathlib.PurePosixPath(spec_path).name
        historical[spec_path] = (sha256(zf.read(hist_member))
                                 if hist_member in names else current[spec_path])

    detail.update({
        "checked": True,
        "missing_members": missing,
        "current_aggregate": aggregate(current),
        "declared_revised_fingerprint": revision["revised_normative_fingerprint"]["hash"],
        "current_matches_declared": (
            aggregate(current) == revision["revised_normative_fingerprint"]["hash"]),
        "historical_substitution_aggregate": aggregate(historical),
        "run_bound_commitment_hash": commitment["pre_run_commitment_hash"],
        "historical_substitution_matches_run_commitment": (
            aggregate(historical) == commitment["pre_run_commitment_hash"]),
        "changed_files_now_verified": {
            path: {"declared_now": entry["now"], "actual": current.get(path),
                   "matches": current.get(path) == entry["now"]}
            for path, entry in revision.get("changed_files", {}).items()},
    })
    if missing:
        detail["problem"] = "normative_members_missing_from_bundle"
    return detail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle")
    parser.add_argument("--json", dest="out")
    args = parser.parse_args()

    zf = zipfile.ZipFile(args.bundle)
    names = zf.namelist()
    report = {"bundle": pathlib.Path(args.bundle).name,
              "bundle_sha256": sha256(pathlib.Path(args.bundle).read_bytes()),
              "problems": []}

    # ---- 1. 归档清单
    manifest = json.loads(zf.read(MEMBER_MANIFEST))
    bad_members, missing_members = [], []
    for entry in manifest["members"]:
        if entry["path"] not in names:
            missing_members.append(entry["path"])
            continue
        data = zf.read(entry["path"])
        if len(data) != entry["bytes"] or sha256(data) != entry["sha256"]:
            bad_members.append(entry["path"])
    payload = "".join(f"{m['path']}:{m['sha256']}\n" for m in manifest["members"])
    recomputed_aggregate = sha256(payload.encode("utf-8"))
    report["manifest"] = {
        "declared_members": manifest["member_count"],
        "members_in_zip_excluding_manifest": len(names) - 1,
        "missing_members": missing_members,
        "hash_or_size_mismatch": bad_members,
        "aggregate_recomputed": recomputed_aggregate,
        "aggregate_declared": manifest["archive_aggregate_hash"],
        "aggregate_matches": recomputed_aggregate == manifest["archive_aggregate_hash"],
    }
    if missing_members or bad_members or not report["manifest"]["aggregate_matches"]:
        report["problems"].append("manifest_mismatch")

    # ---- 由包内源码复算（不执行候选项目代码）
    parser_module, gate_module = load_tooling(zf, names)
    build_per_test = parser_module.build_per_test
    collection_info = parser_module.collection_info
    parse_jsonl = parser_module.parse_jsonl
    admission_gate = gate_module.admission_gate

    candidates = sorted({n.split("/")[1] for n in names if n.startswith("runs/")})
    per_candidate = {}
    for candidate in candidates:
        result_path = f"runs/{candidate}/candidate_result.json"
        if result_path not in names:
            report["problems"].append(f"{candidate}:candidate_result_missing")
            continue
        recorded = json.loads(zf.read(result_path))
        rounds, notes = [], {"rounds": []}
        for index in range(1, 6):
            jsonl_path = f"runs/{candidate}/logs/round{index}.jsonl"
            if jsonl_path not in names:
                report["problems"].append(f"{candidate}:round{index}:jsonl_missing")
                continue
            text = zf.read(jsonl_path).decode("utf-8")
            records, malformed = parse_jsonl(text)
            collection = collection_info(records)
            per_test = build_per_test(records, collection["collected"])
            round_log = next((r for r in recorded.get("rounds_log", [])
                              if r.get("round") == index), {})

            # 2. 结果向量 / 身份 / 顺序：与 candidate_result.json 里准入门保存的逐轮摘要比对
            author_vector = sorted([node, rec["raw_outcome"], list(rec["derived_labels"])]
                                   for node, rec in per_test.items())
            recorded_digest = next(
                (d for d in recorded.get("admission_gate", {}).get("rounds", [])
                 if d.get("index") == index), {})
            session = next((r for r in records if r.get("$report_type") == "SessionFinished"), None)

            # 3. 启动环境三视图
            launch = json.loads(zf.read(f"runs/{candidate}/logs/round{index}.launch_env.json"))
            boot = json.loads(zf.read(f"runs/{candidate}/logs/round{index}.boot_env.json"))
            snapshot_env = next((r.get("environment") for r in records
                                 if r.get("$report_type") == "RuntimeSnapshot"), None)
            keys = sorted(launch["spec"])
            post = boot.get("probe_result", {}).get("post_init_env")
            env_consistent = (
                post is not None and snapshot_env is not None
                and all(three_state(launch["env"], k) == three_state(post, k) == three_state(snapshot_env, k)
                        for k in keys))
            undeclared = sorted(set(snapshot_env or {}) - set(keys)) if snapshot_env else []

            # 4. 初始状态（源码树 / home / tmp）与执行器证据
            fs = json.loads(zf.read(f"runs/{candidate}/logs/round{index}.file_state.json"))
            source_tree = fs["source_tree"]
            home = fs.get("home", {})
            tmp = fs.get("tmp", {})
            net_path = f"runs/{candidate}/logs/round{index}.net.jsonl"
            net = zf.read(net_path).decode("utf-8") if net_path in names else ""
            net_kinds, net_pids = {}, {}
            for line in net.splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                net_kinds[record.get("kind")] = net_kinds.get(record.get("kind"), 0) + 1
                net_pids[str(record.get("pid"))] = net_pids.get(str(record.get("pid")), 0) + 1

            notes["rounds"].append({
                "round": index,
                "collected": collection["collected_count"],
                "malformed_lines": len(malformed),
                "session_exitstatus": (session or {}).get("exitstatus"),
                "raw_outcome_counts": {
                    outcome: sum(1 for r in per_test.values() if r["raw_outcome"] == outcome)
                    for outcome in ("passed", "failed", "skipped")},
                "collection_node_skips": sum(
                    1 for r in records
                    if r.get("$report_type") == "CollectReport" and r.get("outcome") == "skipped"),
                "identity_fingerprint": collection["identity_fingerprint"],
                "identity_fingerprint_matches_record": (
                    collection["identity_fingerprint"] == recorded_digest.get("identity_fingerprint")),
                "result_vector_matches_record": author_vector == recorded_digest.get("result_vector"),
                "launch_env_three_views_consistent": env_consistent,
                "undeclared_env_keys_in_snapshot": undeclared,
                "round_start_source_tree_hash": source_tree["hash"],
                "source_tree_identical_to_artifact": source_tree["verify"]["identical_to_artifact"],
                "source_tree_files_restored": source_tree["restore"]["restored_count"],
                "source_tree_deleted_not_in_artifact": source_tree["restore"]["deleted_count"],
                "cache_files_removed": source_tree["restore"]["cache_files_removed_count"],
                "round_start_home_state": home,
                "round_start_tmp_state": tmp,
                "process_group_cleanup": round_log.get("process_group_cleanup"),
                "child_pid": round_log.get("child_pid"),
                "child_pgid": round_log.get("child_pgid"),
                "network_record_kinds": net_kinds,
                "network_record_pids": net_pids,
            })
            rounds.append({"jsonl": text,
                           "returncode": round_log.get("returncode"),
                           "timed_out": round_log.get("timed_out")})
            if not env_consistent:
                report["problems"].append(f"{candidate}:round{index}:launch_env_three_views_differ")
            if undeclared:
                report["problems"].append(f"{candidate}:round{index}:undeclared_env_keys")
            if author_vector != recorded_digest.get("result_vector"):
                report["problems"].append(f"{candidate}:round{index}:result_vector_differs_from_record")
            if collection["identity_fingerprint"] != recorded_digest.get("identity_fingerprint"):
                report["problems"].append(f"{candidate}:round{index}:identity_fingerprint_differs_from_record")
            if not source_tree["verify"]["identical_to_artifact"]:
                report["problems"].append(f"{candidate}:round{index}:source_tree_not_identical_to_artifact")
            if round_log.get("timed_out") and not (round_log.get("process_group_cleanup") or {}).get("performed"):
                report["problems"].append(f"{candidate}:round{index}:timeout_without_group_cleanup_record")
            if net_kinds.get("observer_init", 0) == 0:
                report["problems"].append(f"{candidate}:round{index}:network_observer_init_missing")

        hashes = {r["round_start_source_tree_hash"] for r in notes["rounds"]}
        notes["round_start_source_tree_hash_unique_values"] = sorted(hashes)
        if len(hashes) != 1:
            report["problems"].append(f"{candidate}:file_state_drift_across_rounds")

        # home/tmp 比较的是**起始状态**（文件数与哈希），不含"本轮复位删除了几个条目"这一动作计数：
        # 第一轮之前无可删除对象，其后各轮删除上一轮遗留，动作计数本就不同。
        def starts(records, key):
            return {json.dumps({"file_count": r[key].get("file_count"),
                                "hash": r[key].get("hash")}, sort_keys=True)
                    for r in records}

        home_states, tmp_states = starts(notes["rounds"], "round_start_home_state"), \
            starts(notes["rounds"], "round_start_tmp_state")
        notes["home_start_state_unique_values"] = len(home_states)
        notes["tmp_start_state_unique_values"] = len(tmp_states)
        notes["home_reset_entries_per_round"] = [r["round_start_home_state"].get("reset_removed_entries")
                                                 for r in notes["rounds"]]
        notes["tmp_reset_entries_per_round"] = [r["round_start_tmp_state"].get("reset_removed_entries")
                                                for r in notes["rounds"]]
        if len(home_states) != 1:
            report["problems"].append(f"{candidate}:home_start_state_drift_across_rounds")
        if len(tmp_states) != 1:
            report["problems"].append(f"{candidate}:tmp_start_state_drift_across_rounds")

        gate = admission_gate(rounds, policy={"allow_skips": True, "allow_xfails": True,
                                              "allow_xpasses": True}, expected_rounds=5)
        notes["gate_recomputed"] = {"admitted": gate["admitted"], "reasons": gate["reasons"]}
        notes["gate_recorded"] = recorded.get("admission_gate", {}).get("admitted")
        if gate["admitted"] != recorded.get("admission_gate", {}).get("admitted"):
            report["problems"].append(f"{candidate}:gate_decision_differs")
        per_candidate[candidate] = notes

    report["candidates"] = per_candidate

    # ---- 5. 事后修订记录的自洽性（v6 审计指出本脚本原缺此项）
    report["executor_revision"] = check_executor_revision(zf, names)
    ev = report["executor_revision"]
    if ev.get("checked"):
        if ev["missing_members"]:
            report["problems"].append("executor_revision:normative_members_missing")
        if not ev["current_matches_declared"]:
            report["problems"].append("executor_revision:current_fingerprint_mismatch")
        if not ev["historical_substitution_matches_run_commitment"]:
            report["problems"].append("executor_revision:historical_substitution_mismatch")
        if not all(v["matches"] for v in ev["changed_files_now_verified"].values()):
            report["problems"].append("executor_revision:changed_file_now_mismatch")

    report["verdict"] = "consistent" if not report["problems"] else "inconsistent"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
