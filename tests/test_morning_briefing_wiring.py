"""晨间简报「日期误判成周末」修复的接线守卫（2026-10-13 事故）。

当日 07:30（北京）那期：代理**零工具调用**、凭直觉断言「今天是周末」，整期简报被吞
（旧表达式 `0-5` 还让 job 在北京时间周六也跑，session 里躺着上一期合法的周末话术）。
修复分三层：skill 硬规则 + cron 日期预脚本（stdout 每轮注入 prompt）+ 表达式修正。

live 在 `~/.hermes`（agent 自维护，见 docs/hermes-skills.md），本测试钉的是**仓库种子**
不退回旧行为：
  ① 仓库版带 `scripts/{today,cst_today}.py`（版本源完整，换机/重建才有修复）；
  ② `cst_today.py` 仍是薄壳（转发到 skill 的 `today.py`，禁止两处实现漂移）；
  ③ `start.sh` 新部署种子：job 挂 `--script cst_today.py`、表达式 `30 23 * * 0-4`、
     prompt 含「第一步：先查日期」；预脚本**只补缺、不覆盖**（不得用仓库版盖掉 live）。

Run: uv run --with pytest pytest tests/test_morning_briefing_wiring.py -v
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "morning-briefing"
START_SH = REPO_ROOT / "scripts" / "start.sh"


class TestRepoCopyCarriesTheFix:
    def test_today_script_present(self):
        assert (SKILL_DIR / "scripts" / "today.py").is_file(), (
            "仓库版缺 scripts/today.py —— 换机/重建部署会退回「凭直觉算星期几」"
        )

    def test_cst_today_shim_present(self):
        assert (SKILL_DIR / "scripts" / "cst_today.py").is_file(), (
            "仓库版缺 scripts/cst_today.py —— cron --script 找不到它，日期不会被注入 prompt"
        )

    def test_shim_stays_a_thin_delegate(self):
        """薄壳必须转发到 skill 的 today.py，不得另写一份日期逻辑。"""
        shim = (SKILL_DIR / "scripts" / "cst_today.py").read_text()
        assert "skills/devops/morning-briefing/scripts/today.py" in shim, (
            "cst_today.py 没有转发到 skill 的 today.py —— 两处实现会漂移"
        )
        assert "runpy" in shim, "cst_today.py 不再是薄壳（应 runpy.run_path 转发）"

    def test_skill_doc_has_date_first_rule(self):
        skill = (SKILL_DIR / "SKILL.md").read_text()
        assert "--check-weekend" in skill and "is_weekend" in skill, (
            "SKILL.md 丢了「星期几只认脚本输出」的硬规则"
        )


class TestStartShSeedsCorrectly:
    def _text(self) -> str:
        return START_SH.read_text()

    def test_job_gets_the_date_pre_script(self):
        assert "--script cst_today.py" in self._text(), (
            "start.sh 创建晨间简报 job 时没挂 --script cst_today.py —— 新部署仍会凭直觉判周末"
        )

    def test_schedule_is_beijing_weekdays(self):
        assert '"30 23 * * 0-4"' in self._text(), (
            "表达式应为 30 23 * * 0-4（UTC 周日–周四 23:30 = 北京周一–周五 07:30）；"
            "0-5 会让它在北京时间周六也跑"
        )

    def test_pre_script_seed_never_overwrites_live(self):
        assert 'if [ ! -f "${HOME}/.hermes/scripts/cst_today.py" ]' in self._text(), (
            "预脚本种子必须是「只补缺、不覆盖」—— ~/.hermes 是 agent 自维护的权威副本，"
            "无条件覆盖会重演 symlink 事故"
        )

    def test_prompt_requires_date_check_first(self):
        text = self._text()
        assert "千万不要自己推算星期几" in text and "--check-weekend" in text, (
            "start.sh 种子 prompt 丢了「第一步：先查日期」—— 新部署的 job 又会凭直觉判周末"
        )
