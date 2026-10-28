# Legacy runtimes / 历史运行环境

## QuantOrion Web prototype

```bash
python -m pip install -r prototypes/quantorion/requirements.txt
cd prototypes/quantorion
# Set TUSHARE_TOKEN / OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL in the environment.
uvicorn main:app --host 127.0.0.1 --port 8080
```

原脚本有独立配置和API，仍会访问第三方数据或AI，且不等于统一接口。凭据默认空值，.env不覆盖已设置环境变量。历史backtest_cache、holdings、output已排除，页面依赖这些缓存时应显示缺少历史结果，不自动编造替代值。原型未作真实服务验证，不提供生产安全承诺。

## QuantLibra Web / signals / auditor

```bash
cd prototypes/quantlibra/web
python server.py
```

默认127.0.0.1:8088；原UI仍调用该服务，不会自动改用新HTTP。需要授权行情数据时自行配置环境变量。signal_v6.py可读本地CSV；backtest_auditor.py是纯标准库静态检查，LLM报告需单独凭据。

原allweather_rotation_v6.py依赖聚宽全局API；不能改个名字就当作SuperMind策略。保留原平台标识和交易调度。

## QuantPyxis engines

research/quantpyxis/legacy保留各历史轮次。冻结A与A60使用不同引擎，严禁混用。原快照有相对/历史绝对路径，本次将个人路径替换为<LOCAL_HOME>，并没有声称所有旧程序已变成跨机器可直接运行。结合独立资料归档、原engine/API/RULES文档和本次来源索引重建环境。

统一核心不导入这些引擎；requirements未锁定历史精确版本，因为没有原始锁文件。numpy/pandas只是从源码可见的依赖，不能保证覆盖所有旧脚本。资料归档保留数据与结果用于后续重放；本次未全量重跑。

research/quantpyxis/platform/frozen_a.py与frozen_a80.py需要SuperMind平台API上下文。仅做静态语法检查，不在普通Python中执行订单回调。冻结状态截止2026-10-19必须更新验证，不能直接用于今天。

## Optional document build

英文计划书可用scripts/build_english_plan.py重建，另需reportlab、Pillow、pypdf与Songti字体。该脚本默认使用macOS字体路径；其他平台请设置QUANTSCOPE_SONGTI_FONT和对应字体集子索引。文档依赖不影响核心运行。
