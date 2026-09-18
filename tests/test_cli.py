from __future__ import annotations

from mobilegui_ltm.cli import main, run_demo


def test_cli_ablate(tmp_path, capsys):
    report = run_demo(ltm="ablate", k=2, data_dir=tmp_path)
    captured = capsys.readouterr().out
    assert "ltm-off" in captured
    assert "ltm-on" in captured
    assert "pass@2" in captured
    assert report.ltm_on.success is True
    assert report.ltm_off.success is False


def test_cli_on_and_off(tmp_path, capsys):
    on = run_demo(ltm="on", k=2, data_dir=tmp_path)
    off = run_demo(ltm="off", k=2, data_dir=tmp_path)
    assert on.success is True
    assert off.success is False
    assert main(["--ltm", "on", "--k", "2", "--data-dir", str(tmp_path / "cli")]) == 0


def test_cli_rejects_bad_k(capsys):
    assert main(["--k", "0"]) == 2
    err = capsys.readouterr().err
    assert "k must be" in err


def test_cli_ablate_flag(tmp_path, capsys):
    assert main(["--ablate", "--k", "2", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ltm-on" in out
    assert "recovery_after_failure" in out


def test_cli_matrix(tmp_path, capsys):
    report = run_demo(ltm="matrix", k=2, data_dir=tmp_path)
    out = capsys.readouterr().out
    assert "failures-only" in out
    assert "shortcuts-only" in out
    assert "anchors" in out
    assert "full" in out
    assert report.report("full").success is True
    assert report.report("off").success is False
    assert main(["--matrix", "--k", "2", "--data-dir", str(tmp_path / "cli-matrix")]) == 0
