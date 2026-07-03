from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_init_scripts_keep_auto_generated_env_placeholders():
    """自动生成变量必须留在模板原位置，避免清理空占位符时被提前删掉。"""
    bash = (ROOT / "scripts" / "init.sh").read_text(encoding="utf-8")
    ps1_path = ROOT / "scripts" / "init.ps1"
    ps1 = ps1_path.read_text(encoding="utf-8")

    assert "\nnormalize_env_file\n\n# 删除模板占位" in bash
    assert "JWT_SECRET_KEY|YUXI_INSTANCE_ID" in bash
    assert 'ensure_env_var JWT_SECRET_KEY "$(generate_hex 32)"' in bash
    assert 'ensure_env_var YUXI_INSTANCE_ID "instance-$(generate_hex 8)"' in bash
    assert bash.index('ensure_env_var JWT_SECRET_KEY "$(generate_hex 32)"') < bash.index(
        "ask_or_skip SILICONFLOW_API_KEY"
    )
    assert '@("JWT_SECRET_KEY", "YUXI_INSTANCE_ID")' in ps1
    assert 'Update-EnvVar "JWT_SECRET_KEY" (New-RandomHex 32)' in ps1
    assert 'Update-EnvVar "YUXI_INSTANCE_ID" ("instance-" + (New-RandomHex 8))' in ps1
    assert ps1.index('Update-EnvVar "JWT_SECRET_KEY" (New-RandomHex 32)') < ps1.index(
        'Read-UserInput "SILICONFLOW_API_KEY"'
    )
    assert ps1_path.read_bytes().startswith(b"\xef\xbb\xbf")
