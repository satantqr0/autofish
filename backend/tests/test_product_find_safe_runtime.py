import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_RUNTIME = (
    Path(__file__).resolve().parents[2]
    / "adapter-runtime"
    / "1688-product-find-safe"
    / "cli.py"
)
MOUNTED_RUNTIME = Path("/opt/autofish-adapters/1688-product-find-safe/cli.py")
RUNTIME = MOUNTED_RUNTIME if MOUNTED_RUNTIME.is_file() else PROJECT_RUNTIME


def _fake_upstream(tmp_path):
    service_dir = tmp_path / "scripts" / "capabilities" / "text_search"
    service_dir.mkdir(parents=True)
    (service_dir / "service.py").write_text(
        "def text_search(**kwargs):\n"
        "    return {'similar_products': [{\n"
        "        'product_id': 'REAL-1', 'sku_id': 'SKU-1',\n"
        "        'stock_amount': 88, 'price': '6.40',\n"
        "        'title': kwargs['query'], 'supplier': '测试工厂'\n"
        "    }]}\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(tmp_path, *arguments, access_key="x" * 40):
    environment = os.environ.copy()
    environment.update(
        {
            "ALI_1688_AK": access_key,
            "AUTOFISH_1688_PRODUCT_FIND_ROOT": str(_fake_upstream(tmp_path)),
        }
    )
    return subprocess.run(
        [sys.executable, str(RUNTIME), *arguments],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )


def test_text_search_emits_only_documented_json_contract(tmp_path):
    completed = _run(tmp_path, "text_search", "--query", "桌面收纳", "--limit", "5")

    assert completed.returncode == 0
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["success"] is True
    assert payload["data"]["similar_products"][0]["sku_id"] == "SKU-1"
    assert payload["data"]["similar_products"][0]["stock_amount"] == 88
    assert "x" * 40 not in completed.stdout


def test_configure_status_performs_a_real_read_probe(tmp_path):
    completed = _run(tmp_path, "configure", "--status")

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["data"]["authenticated"] is True
    assert payload["data"]["read_only"] is True
    assert payload["data"]["probe_results"] == 1


def test_invalid_access_key_is_rejected_without_echoing_it(tmp_path):
    completed = _run(tmp_path, "text_search", "--query", "收纳", access_key="short")

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["success"] is False
    assert payload["error_code"] == "SAFE_RUNTIME_ERROR"
    assert "short" not in completed.stdout
