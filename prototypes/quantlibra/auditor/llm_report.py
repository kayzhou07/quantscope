#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 报告层 —— 把 backtest_auditor 的结构化发现，转成一份叙事化的专业审计报告。

后端：DeepSeek（OpenAI 兼容接口），默认模型 deepseek-v4-pro。
需要设置 DEEPSEEK_API_KEY 环境变量（或传 --api-key）。

用法：
  DEEPSEEK_API_KEY=sk-xxx python3 llm_report.py <策略文件.py>
  DEEPSEEK_API_KEY=sk-xxx python3 llm_report.py <策略文件.py> --out 报告.md

数据流：
  backtest_auditor.audit_file() → findings(结构化) → build_llm_prompt() → DeepSeek → 叙事报告
"""
import json
import os
import subprocess
import sys

import backtest_auditor as auditor

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"

SYSTEM_PROMPT = (
    "你是一名资深量化回测审计专家，专门识别会让回测收益虚高的偏差。"
    "你会收到一个规则引擎从策略代码里检测出的偏差清单（JSON），"
    "请把它改写为一份面向量化策略开发者的专业审计报告（Markdown）。"
)


def build_llm_prompt(file_name, findings):
    """把结构化发现序列化为给 LLM 的输入。"""
    items = [{
        'rule_id': f.rule_id,
        'rule_name': f.rule_name,
        'severity': f.severity,
        'line': f.line,
        'snippet': f.snippet,
        'why': f.why,
        'fix': f.fix,
    } for f in findings]
    payload = {'file': file_name, 'findings': items}
    return (
        "以下是回测审计规则引擎对策略代码 `{f}` 检测出的 {n} 个偏差，"
        "请生成一份中文审计报告，结构要求：\n"
        "1. 【总体结论】一句话定性这个回测结果可信不可信，为什么；\n"
        "2. 【分项说明】按偏差类型分组，每组讲清楚：这是什么问题、为什么会让收益虚高、"
        "影响大概多大、怎么修；\n"
        "3. 【修复优先级】按严重程度排序给出修复顺序；\n"
        "4. 【一句话建议】给开发者一句话行动建议。\n"
        "不要罗列 JSON，要用连贯的叙述，像资深同行在帮你审代码。\n\n"
        "偏差清单 JSON：\n"
        "{json}"
    ).format(f=file_name, n=len(findings), json=json.dumps(payload, ensure_ascii=False, indent=2))


def call_llm(prompt, api_key=None, model=DEFAULT_MODEL):
    """调用 DeepSeek（OpenAI 兼容接口）生成报告。用 curl 以复用系统信任链。"""
    key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(
            "缺少 API key：请设置环境变量 DEEPSEEK_API_KEY，或用 --api-key 传入。"
        )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "max_tokens": 8000,
        "temperature": 0.2,  # 报告类任务，低温度更稳定
    })
    proc = subprocess.run(
        ["curl", "-s", "-m", "180", DEEPSEEK_URL,
         "-H", "Content-Type: application/json",
         "-H", "Authorization: Bearer " + key,
         "-d", body],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("curl 调用失败: " + proc.stderr)
    data = json.loads(proc.stdout)
    if "choices" not in data:
        raise RuntimeError("API 返回异常: " + proc.stdout[:500])
    return data["choices"][0]["message"]["content"]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    out_path = None
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    model = DEFAULT_MODEL
    if '--out' in argv:
        out_path = argv[argv.index('--out') + 1]
    if '--api-key' in argv:
        api_key = argv[argv.index('--api-key') + 1]
    if '--model' in argv:
        model = argv[argv.index('--model') + 1]

    findings = auditor.audit_file(path)
    if not findings:
        report = "# 回测审计报告\n\n✅ 规则引擎未检出已知偏差，无需 LLM 深化。\n"
    else:
        prompt = build_llm_prompt(os.path.basename(path), findings)
        report = call_llm(prompt, api_key=api_key, model=model)

    if out_path:
        with open(out_path, 'w', encoding='utf-8') as fp:
            fp.write(report)
        print('报告已写入:', out_path)
    else:
        print(report)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
