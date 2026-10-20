"""ClawsGO Science Agent：只读 B0 入口 CLI。未授权时输出拒绝结果并以非零码退出。"""
import argparse
import json
import sys
from pathlib import Path

from b0_runner import run_b0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="PyHost-Context 准入阶段只读 B0 入口；未授权时不执行候选项目。")
    parser.add_argument("--config", required=True, help="B0 配置 JSON 路径")
    parser.add_argument("--dry-run", action="store_true", help="仅校验授权与快照，不启动进程")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text())
    result = run_b0(config, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["executed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
