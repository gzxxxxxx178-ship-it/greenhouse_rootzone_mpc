# 设施大棚根区水分 MPC 研究验证项目

本项目用于验证“独立虚拟根区对象、强规则基线、基础区间 MPC、可信回退与平台软件在环”的研究路线。当前证据仅限模型仿真，不代表红壤现场节水、增产或设备安全效果。

## 当前阶段

已完成仿真验证与最小软件在环链路：

- 非线性单层根区虚拟对象；
- 经约束的阈值脉冲规则基线；
- 使用简化内部模型的区间 MPC；
- 标称、高蒸散、低灌水效率和强排水四种对象条件；
- 成对仿真、结果表和轨迹图导出；
- 配置与输出 SHA-256 清单；
- 水量平衡与控制约束测试。
- 可信监督锁定异常评价；
- 任务、命令、反馈、联锁、模式和版本的软件在环审计。
- 本地MQTT 3.1.1代理下的QoS 1、持久会话、断连重连和SQLite审计核对。

锁定评价显示基础MPC相对规则形成“仿真灌水减少但轻微亏缺增加”的权衡。当前可信监督未通过预注册机制价值门，主要问题是受污染观测下的回退控制律；该否定性结果已冻结，不构成算法优势证据。

`data/csv/task_list.csv` 是任务状态的唯一事实来源，`context.md` 记录当前结论、边界和下一步。

## 目录

```text
configs/               冻结实验配置
data/csv/              任务台账和结构化数据
data/templates/        现场数据空模板
data/examples/         不进入结论的合成接入示例
data/raw/              原始或外部数据
data/interim/          中间数据
data/processed/        可用于正式分析的数据
docs/                  研究协议和数据字典
notebooks/             只用于探索，不作为正式运行入口
outputs/runs/           逐步轨迹
outputs/tables/         汇总表
outputs/figures/        图形
outputs/logs/           运行日志
scripts/               环境和运行入口
src/rootzone_mpc/      正式源码
tests/                 关键计算测试
```

## 运行

```bash
source .venv/bin/activate
python scripts/run_smoke_validation.py
python scripts/run_mqtt_sil.py
python scripts/validate_field_data.py data/examples/synthetic_field_observations_v1.csv
pytest -q
```

烟雾验证会覆盖四种对象条件，并将结果写入 `outputs/`。正式确认性试验必须先登记任务、冻结配置和随机种子，完成后更新任务台账与 `context.md`。
