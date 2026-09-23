"""离线单测：bird 一键造数据的 4 个 CLI 参数（--bird-species / --bulk / --variety / --verify-bird-tab）。

覆盖：
  - argparse 层：参数能正确解析、默认值、互斥/组合行为
  - expand_bird_plans 层：物种选择、bulk 展开、错误分支、bulk 默认值、图循环

不打云端 / 不读账号 / 不用 ffmpeg。

跑：python3 -m pytest scripts/tests/test_bird_oneshot.py -v
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_pir_event as cpe  # noqa: E402

BIRDS_DIR = SCRIPT_DIR / "test_images" / "birds"


# ─────────────────────────── argparse 层 ───────────────────────────


def test_parser_bird_species_default_none():
    args = cpe.build_parser().parse_args([])
    assert args.bird_species is None
    assert args.bulk is None
    assert args.variety is None
    assert args.verify_bird_tab is False


def test_parser_accepts_all_four_flags_together():
    args = cpe.build_parser().parse_args([
        "--bird-species", "robin", "--bulk", "5",
        "--variety", "3", "--verify-bird-tab",
    ])
    assert args.bird_species == "robin"
    assert args.bulk == 5
    assert args.variety == 3
    assert args.verify_bird_tab is True


def test_parser_bulk_and_variety_are_int():
    args = cpe.build_parser().parse_args(["--bulk", "10", "--variety", "7"])
    assert isinstance(args.bulk, int) and args.bulk == 10
    assert isinstance(args.variety, int) and args.variety == 7


def test_parser_verify_bird_tab_is_store_true():
    a = cpe.build_parser().parse_args(["--verify-bird-tab"])
    b = cpe.build_parser().parse_args([])
    assert a.verify_bird_tab is True
    assert b.verify_bird_tab is False


# ─────────────────────────── 计划展开：成功路径 ───────────────────────────


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_explicit_species_default_bulk_3():
    plans = cpe.expand_bird_plans(BIRDS_DIR, bird_species="robin", variety=None, bulk=None)
    # 默认 bulk=3，单物种
    assert len(plans) == 3
    assert all(p["species"] == "robin" for p in plans)
    assert all(Path(p["image"]).is_file() for p in plans)
    assert all(p["label"].startswith("robin") for p in plans)


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_explicit_species_custom_bulk():
    plans = cpe.expand_bird_plans(BIRDS_DIR, bird_species="robin", variety=None, bulk=7)
    assert len(plans) == 7
    assert all(p["species"] == "robin" for p in plans)


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_random_species_picks_one_from_library():
    rng = random.Random(42)  # 确定性
    plans = cpe.expand_bird_plans(BIRDS_DIR, bird_species="random",
                                  variety=None, bulk=2, rng=rng)
    assert len(plans) == 2
    species_set = {p["species"] for p in plans}
    assert len(species_set) == 1  # random 只选一个物种


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_variety_picks_n_distinct_species():
    rng = random.Random(0)
    plans = cpe.expand_bird_plans(BIRDS_DIR, bird_species=None,
                                  variety=3, bulk=2, rng=rng)
    species_set = {p["species"] for p in plans}
    # variety=3 → 3 个不同物种，每个 2 条 → 6 条
    assert len(species_set) == 3
    assert len(plans) == 6


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_variety_clamped_to_available_species():
    """请求的 variety 超过图库物种数时，应 clamp 到实际数量而非报错。"""
    all_species = sorted(p.name for p in BIRDS_DIR.iterdir()
                         if p.is_dir() and any(p.glob("*.jpg")))
    rng = random.Random(0)
    plans = cpe.expand_bird_plans(BIRDS_DIR, bird_species=None,
                                  variety=len(all_species) + 5, bulk=1, rng=rng)
    species_set = {p["species"] for p in plans}
    assert species_set == set(all_species)


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_variety_combined_with_bulk():
    """variety=5 --bulk 3 → 5 物种 × 3 条 = 15。"""
    rng = random.Random(1)
    plans = cpe.expand_bird_plans(BIRDS_DIR, bird_species=None,
                                  variety=5, bulk=3, rng=rng)
    species_set = {p["species"] for p in plans}
    assert len(species_set) == 5
    assert len(plans) == 15


# ─────────────────────────── 计划展开：错误分支 ───────────────────────────


def test_expand_missing_birds_dir_raises():
    fake = Path("/tmp/__nonexistent_bird_dir_xyz")
    with pytest.raises(cpe.BirdPlanError, match="不存在"):
        cpe.expand_bird_plans(fake, bird_species="robin", variety=None, bulk=None)


def test_expand_empty_birds_dir_raises(tmp_path):
    # tmp_path 存在但里面没有任何子目录 / jpg
    with pytest.raises(cpe.BirdPlanError, match="为空"):
        cpe.expand_bird_plans(tmp_path, bird_species="robin", variety=None, bulk=None)


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_unknown_species_raises():
    with pytest.raises(cpe.BirdPlanError, match="未知 bird-species"):
        cpe.expand_bird_plans(BIRDS_DIR, bird_species="dodo",
                              variety=None, bulk=None)


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_neither_species_nor_variety_raises():
    with pytest.raises(cpe.BirdPlanError, match="必须传"):
        cpe.expand_bird_plans(BIRDS_DIR, bird_species=None,
                              variety=None, bulk=None)


# ─────────────────────────── 图循环行为 ───────────────────────────


# ─────────────────────────── bird 自动 preset 行为 ───────────────────────────


def _make_cfg_with_default_preset():
    """模拟 resolve_config 后的 cfg：device_firmware_preset 已 fallback 到默认"""
    return cpe.Config(
        brand="kiwibit", region="us", env="prod",
        business_api="https://example.com", device_api="https://example.com",
        device_firmware_preset=cpe.DEFAULT_DEVICE_FIRMWARE_PRESET,
    )


def test_bird_auto_defaults_applies_kf126_when_user_did_not_pass():
    """回归：用户传 --bird-species 但没传 --device-firmware-preset → cfg 落 kf126。"""
    args = cpe.build_parser().parse_args(["--bird-species", "robin"])
    assert args.device_firmware_preset is None  # 前置条件
    cfg = _make_cfg_with_default_preset()
    cpe.apply_bird_auto_defaults(args, cfg)
    assert cfg.device_firmware_preset == "kf126"
    assert cfg.object_type == "bird"


def test_bird_auto_defaults_respects_explicit_user_preset():
    """用户显式传 --device-firmware-preset 时不被覆盖。"""
    args = cpe.build_parser().parse_args([
        "--bird-species", "robin",
        "--device-firmware-preset", "ss131",
    ])
    cfg = _make_cfg_with_default_preset()
    cfg.device_firmware_preset = "ss131"  # 模拟 _coalesce 已用 args 值填充
    cpe.apply_bird_auto_defaults(args, cfg)
    assert cfg.device_firmware_preset == "ss131"


def test_bird_auto_defaults_respects_explicit_object_type():
    """用户显式传 --object-type 时不被覆盖。"""
    args = cpe.build_parser().parse_args([
        "--bird-species", "robin", "--object-type", "small_animal",
    ])
    cfg = _make_cfg_with_default_preset()
    cfg.object_type = "small_animal"
    cpe.apply_bird_auto_defaults(args, cfg)
    assert cfg.object_type == "small_animal"


# ─────────────────────────── 签名密钥从 .env / env 注入 ───────────────────────────


def test_pir_sign_secret_loaded_from_env_file(tmp_path, monkeypatch):
    """resolve_config 读取 .env 后必须把 PIR_SIGN_SECRET 注到模块级 DEVICE_SIGN_SECRET_B64。

    回归 AI code-review 第二轮 P0-2：profile/.env 里的 PIR_SIGN_SECRET 不生效。
    """
    monkeypatch.delenv("PIR_SIGN_SECRET", raising=False)
    cpe.DEVICE_SIGN_SECRET_B64 = ""
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "PIR_BRAND=vicohome\nPIR_REGION=us\nPIR_ENV=prod\n"
        "PIR_EMAIL=a@b.io\nPIR_PASSWORD=p\nPIR_DEVICE=SN1\nPIR_USER_SN=US1\n"
        "PIR_SIGN_SECRET=ZmFrZXNlY3JldA==\n"  # base64('fakesecret')
    )
    args = cpe.build_parser().parse_args(["--env-file", str(env_file), "--quiet"])
    _cfg, _path = cpe.resolve_config(args)
    assert cpe.DEVICE_SIGN_SECRET_B64 == "ZmFrZXNlY3JldA=="
    # 还能真去 hmac 不报"未配置"
    sig = cpe.generate_device_signature("SN1", 1700000000)
    assert sig and isinstance(sig, str)


def test_pir_sign_secret_env_overrides_env_file(tmp_path, monkeypatch):
    """进程 env 优先级高于 .env 文件。"""
    monkeypatch.setenv("PIR_SIGN_SECRET", "ZW52d2lucw==")  # base64('envwins')
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "PIR_BRAND=vicohome\nPIR_REGION=us\nPIR_ENV=prod\n"
        "PIR_EMAIL=a@b.io\nPIR_PASSWORD=p\nPIR_DEVICE=SN1\nPIR_USER_SN=US1\n"
        "PIR_SIGN_SECRET=Zm9vYmFy\n"  # base64('foobar') — should be overridden
    )
    args = cpe.build_parser().parse_args(["--env-file", str(env_file), "--quiet"])
    cpe.resolve_config(args)
    assert cpe.DEVICE_SIGN_SECRET_B64 == "ZW52d2lucw=="


def test_pir_sign_secret_missing_raises_clear_error(monkeypatch):
    """既没 env 也没 .env 时，generate_device_signature 必须抛清晰指引。"""
    monkeypatch.delenv("PIR_SIGN_SECRET", raising=False)
    cpe.DEVICE_SIGN_SECRET_B64 = ""
    with pytest.raises(cpe.PirError, match="PIR_SIGN_SECRET"):
        cpe.generate_device_signature("SN1", 1700000000)


@pytest.mark.skipif(not BIRDS_DIR.is_dir(), reason="bird image library missing")
def test_expand_bulk_exceeds_image_count_cycles_images():
    """bulk 超过该物种 jpg 数量时，应循环复用图（不报错也不丢条目）。"""
    species_dirs = [p for p in BIRDS_DIR.iterdir()
                    if p.is_dir() and any(p.glob("*.jpg"))]
    assert species_dirs, "library has no species"
    # 找一个物种然后造比图数多的 PIR
    species = species_dirs[0]
    n_imgs = len(list(species.glob("*.jpg")))
    plans = cpe.expand_bird_plans(BIRDS_DIR, bird_species=species.name,
                                  variety=None, bulk=n_imgs * 2 + 1)
    assert len(plans) == n_imgs * 2 + 1
    # 第 0 条和第 n_imgs 条应该是同一张图（循环）
    assert plans[0]["image"] == plans[n_imgs]["image"]
