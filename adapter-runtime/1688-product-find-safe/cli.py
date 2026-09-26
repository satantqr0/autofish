#!/usr/bin/env python3
"""AutoFish wrapper for the official 1688-product-find runtime.

The upstream package is loaded as an isolated external dependency.  This
wrapper deliberately does not call its top-level CLI because that entry point
performs dependency installation, credential persistence and usage telemetry.
Only the documented read-only text-search service is exposed here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


UPSTREAM_VERSION = "1.7.0"
DEFAULT_UPSTREAM_ROOT = (
    Path(__file__).resolve().parent / "upstream" / f"1688-product-find-{UPSTREAM_VERSION}"
)
MAX_QUERY_LENGTH = 200
MAX_RESULTS = 50


class SafeRuntimeError(RuntimeError):
    """An error whose message is safe to return to AutoFish."""


def _output(success: bool, *, data=None, message="", error_code="") -> None:
    payload = {"success": success}
    if data is not None:
        payload["data"] = data
    if message:
        payload["markdown"] = message
    if error_code:
        payload["error_code"] = error_code
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _upstream_root() -> Path:
    configured = os.environ.get("AUTOFISH_1688_PRODUCT_FIND_ROOT")
    return Path(configured).resolve() if configured else DEFAULT_UPSTREAM_ROOT


def _validate_access_key() -> None:
    value = os.environ.get("ALI_1688_AK", "").strip()
    if len(value) <= 32 or any(character.isspace() for character in value):
        raise SafeRuntimeError("1688 AK 未配置或格式无效")


def _load_text_search():
    root = _upstream_root()
    scripts = root / "scripts"
    service = scripts / "capabilities" / "text_search" / "service.py"
    if not service.is_file():
        raise SafeRuntimeError(
            f"1688-product-find {UPSTREAM_VERSION} 外部运行时未安装"
        )
    sys.path.insert(0, str(scripts))
    try:
        from capabilities.text_search.service import text_search
    except Exception as exc:  # pragma: no cover - exact import failure is environment-specific
        raise SafeRuntimeError("1688-product-find 外部运行时加载失败") from exc
    return text_search


def _run_text_search(query: str, limit: int) -> dict:
    query = query.strip()
    if not query or len(query) > MAX_QUERY_LENGTH:
        raise SafeRuntimeError("搜索词长度必须为 1 到 200 个字符")
    if not 1 <= limit <= MAX_RESULTS:
        raise SafeRuntimeError("返回数量必须为 1 到 50")
    _validate_access_key()
    text_search = _load_text_search()
    result = text_search(
        query=query,
        platform="1688",
        limit=limit,
        score_level="high",
        purchase_amount=1,
        tags="4306497",
    )
    products = result.get("similar_products") if isinstance(result, dict) else None
    if not isinstance(products, list):
        raise SafeRuntimeError("1688 商品搜索返回结构异常")
    return {
        "similar_products": products,
        "query": query,
        "total_results": len(products),
        "upstream": f"1688-product-find/{UPSTREAM_VERSION}",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoFish 安全只读 1688 商品搜索")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure")
    configure.add_argument("--status", action="store_true", required=True)

    text = subparsers.add_parser("text_search")
    text.add_argument("--query", required=True)
    text.add_argument("--limit", type=int, default=10)
    return parser


def main() -> int:
    ephemeral_root = tempfile.mkdtemp(prefix="autofish-1688-product-find-")
    os.environ["AGENT_WORK_ROOT"] = ephemeral_root
    try:
        args = _parser().parse_args()
        if args.command == "configure":
            # A signed read proves both credential validity and gateway reachability.
            result = _run_text_search("桌面收纳", 1)
            _output(
                True,
                data={
                    "authenticated": True,
                    "read_only": True,
                    "probe_results": result["total_results"],
                    "upstream": result["upstream"],
                },
            )
            return 0
        if args.command == "text_search":
            _output(True, data=_run_text_search(args.query, args.limit))
            return 0
        raise SafeRuntimeError("不支持的命令")
    except SafeRuntimeError as exc:
        _output(False, message=str(exc), error_code="SAFE_RUNTIME_ERROR")
        return 2
    except Exception:
        # Do not echo upstream exception details because they may contain request data.
        _output(False, message="1688 官方商品搜索暂时不可用", error_code="UPSTREAM_ERROR")
        return 75
    finally:
        shutil.rmtree(ephemeral_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
