#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cron 用：把权威的北京时间日期注入晨间简报的提示词。

薄壳，逻辑全部在 skills/devops/morning-briefing/scripts/today.py，
避免两处实现漂移。stdout 会被 cron 引擎注入到 agent 的 prompt 里。
"""
import runpy
import sys

TARGET = "/opt/data/skills/devops/morning-briefing/scripts/today.py"

sys.argv = [TARGET] + sys.argv[1:]
runpy.run_path(TARGET, run_name="__main__")
