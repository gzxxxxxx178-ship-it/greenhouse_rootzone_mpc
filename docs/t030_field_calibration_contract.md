# T030 现场水分传感器与流量计标定入口协议

## 目标

T027的信息量仍使用合成测量噪声。T030建立可填写的原始标定模板和自动质量检查，用真实重复标定估计水分测量噪声及灌水计量误差。标定不能估计根区模型过程噪声，因此不会直接把现场信息量阈值标记为可用。

## 水分传感器记录

每个“传感器＋深度”至少覆盖5个水分等级，每个等级至少3个独立重复，人工参考体积含水率跨度至少0.12 m³/m³。模板记录传感器读数、湿土质量、干土质量、样品体积和试验温度对应的水密度，参考体积含水率按水质量除以水密度和样品体积计算。

每个传感器单独拟合`reference_theta = slope × sensor_theta + intercept`。质量门检查斜率范围、校正RMSE和留一水分等级交叉验证RMSE。校正残差标准差可交接为测量噪声候选值，但必须保留传感器、土源、温度和批次信息，不能将一个合成或单批次结果视为所有现场条件的噪声。

## 流量计记录

每台设备至少覆盖3个运行点，每个运行点至少3个独立重复，参考体积跨度至少15 L。模板记录持续时间、容器前后质量、水密度和设备累计体积，参考体积由质量差除以水密度计算。

每台设备单独拟合`reference_volume = slope × device_volume + intercept`，检查留一运行点相对RMSE、校正相对RMSE和最大绝对相对误差。流量误差属于控制输入不确定性，不能并入土壤水分传感器噪声。

## 暂定质量线

配置中的斜率和误差阈值是本项目的软件验收与首轮工程筛查线，不宣称为国家标准或红壤通用标准。真实设备规格、称量工具精度和试验范围明确后必须先修订并冻结现场配置，再处理正式数据。

## 过程噪声边界

过程噪声包含未建模蒸散、根系吸水、排水、空间异质性和输入误差传播，不能从静态水分标定或流量标定推导。它必须在模型结构冻结后，从不参与参数选择的动态湿干循环残差估计。T030即使通过，也只输出`measurement_noise_ready_process_noise_pending`。

## 文件与运行

- 水分空模板：`data/templates/moisture_sensor_calibration_template.csv`
- 流量空模板：`data/templates/flow_meter_calibration_template.csv`
- 合成接入样例：`data/examples/synthetic_moisture_calibration_v1.csv`和`data/examples/synthetic_flow_calibration_v1.csv`
- 运行入口：`scripts/assess_field_calibration.py`

```bash
python scripts/assess_field_calibration.py 水分标定.csv 流量标定.csv --output 标定报告.json
```

合成样例仅验证计算入口，不改变R003、R009或R010的缺失状态。本任务不生成图形。
